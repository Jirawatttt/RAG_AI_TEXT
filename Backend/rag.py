"""RAG retrieval for benefit information, with no rule-based eligibility check."""
from __future__ import annotations

import logging
import os
import re
from math import sqrt

import database
import llm
from models import Benefit

logger = logging.getLogger(__name__)


# With a catalogue this small (currently 8 benefits), retrieval only needs
# to RANK candidates by relevance — it does not filter individual items out.
#
# SCOPE_FLOOR is not a per-item filter and no score is kept afterwards — it
# is a one-time check on the BEST score across the whole catalogue, used
# only to decide whether this input is in-scope at all. If even the closest
# catalogue item is below this, the input is off-topic or garbled, so we
# reject it before anything reaches the LLM (same job as is_rights_query,
# just semantic instead of keyword-based). If it passes, every item is
# returned ranked — none are excluded individually.
SCOPE_FLOOR = float(os.getenv("RAG_SCOPE_FLOOR", os.getenv("RAG_SIMILARITY_THRESHOLD", "0.25")))
SCOPE_FLOOR_KEYWORD = float(os.getenv("RAG_SCOPE_FLOOR_KEYWORD", os.getenv("RAG_KEYWORD_THRESHOLD", "0.08")))
DEFAULT_DISCOVERY_RESULT_LIMIT = int(os.getenv("RAG_DISCOVERY_RESULT_LIMIT", "10"))
# Shared domain keyword list used by both:
#   - is_rights_query()  → cheap scope gate (main.py runs this first, before
#     any embedding call, to reject clearly off-topic text for free)
#   - _tokens() / _keyword_score() → keyword fallback ranking when the
#     embedding API is unavailable
# One list instead of two near-duplicate sets, so new domain terms only
# need to be added in one place.
_RIGHTS_TERMS = {
    "สิทธิ", "สวัสดิการ", "เบี้ย", "เงิน", "อุดหนุน", "ผู้สูงอายุ",
    "พิการ", "ประกันสังคม", "บัตรทอง", "บัตรคนจน", "รักษา", "รายได้",
    "บุตร", "เด็ก", "ว่างงาน", "งาน", "อายุ", "ไทย", "สัญชาติ", "ทะเบียนบ้าน",
    "คนไทย", "ประกัน", "สังคม", "ผู้ประกันตน", "นายจ้าง",
    "ลูกจ้าง", "ครัวเรือน", "บำนาญ", "คนพิการ", "ผู้พิการ",
}


def _tokens(text: str) -> set[str]:
    normalized = normalize_text(text)
    tokens = set(re.findall(r"[\wก-๙]+", normalized))
    # Thai users often type words without spaces. Preserve domain terms found
    # inside those strings so keyword fallback still has useful signals.
    tokens.update(term for term in _RIGHTS_TERMS if term in normalized)
    return tokens


def normalize_text(text: str) -> str:
    """Normalize user text without removing any original meaning."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    normalized = re.sub(r"(?<=[ก-๙])(?=\d)|(?<=\d)(?=[ก-๙])", " ", normalized)
    return normalized


def _keyword_score(query: str, text: str) -> float:
    query_tokens, document_tokens = _tokens(query), _tokens(text)
    return len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0


def is_rights_query(text: str) -> bool:
    """Reject obviously out-of-scope requests before retrieval and LLM use."""
    normalized = normalize_text(text)
    return any(term in normalized for term in _RIGHTS_TERMS)


async def prepare_query(text: str) -> tuple[bool, str]:
    """Scope gate + query rewrite in a single LLM round trip.

    Replaces two separate calls (a scope classifier and a query rewriter)
    with llm.analyze_query(), which returns both in one response — the same
    AI-first design as before (the model decides scope directly, not a
    keyword list), just consolidated to cut the per-request LLM call count
    in half. Returns (in_scope, search_query); search_query is only
    meaningful when in_scope is True.

    is_rights_query() is kept only as an emergency fallback for when the AI
    call itself fails (no API key, network error, bad response) — not as a
    first-pass filter — so a full OpenAI outage doesn't leave the scope gate
    with no answer at all.
    """
    normalized = normalize_text(text)
    try:
        result = await llm.analyze_query(text)
        return bool(result.get("in_scope")), (result.get("query") or "").strip() or normalized
    except Exception as exc:
        logger.warning("Scope+query analysis failed, falling back to keyword check: %s", exc)
        return is_rights_query(text), normalized


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = sqrt(sum(x * x for x in left)) * sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0


async def retrieve_for_text(
    user_text: str,
    limit: int = DEFAULT_DISCOVERY_RESULT_LIMIT,
    search_query: str | None = None,
) -> list[Benefit]:
    """Retrieve the most relevant catalogue evidence from an unrestricted user query.

    This function performs retrieval only.  It never decides eligibility; that
    decision is made by the LLM from the returned source text.

    search_query: an already-rewritten query (e.g. from prepare_query()) to
    embed directly, skipping a second rewrite_query() call. If omitted, this
    function rewrites the text itself — keeping this callable on its own.
    """
    query = normalize_text(user_text)
    catalogue = await database.load_rag_catalogue()
    ranked: list[tuple[float, object, list[database.BenefitDocument]]] = []

    used_embeddings = True
    try:
        # Distill free-form/emotional user text into a short, fact-only query
        # before embedding it. rewrite_query() never raises — on any failure
        # it returns the original text — so this can only help or be a no-op,
        # never make retrieval worse than embedding the raw text directly.
        embed_query = search_query if search_query is not None else await llm.rewrite_query(query)
        query_embedding = await llm.embed_text(embed_query)
        for item in catalogue:
            scored_documents = []
            for document in item.documents:
                embedding = document.embedding
                if embedding is None:
                    embedding = await llm.embed_text(document.content)
                    await database.save_document_embedding(document.id, embedding)
                scored_documents.append((_cosine(query_embedding, embedding), document))
            scored_documents.sort(key=lambda row: row[0], reverse=True)
            documents = [document for _, document in scored_documents[:2]]
            score = scored_documents[0][0] if scored_documents else 0.0
            ranked.append((score, item, documents))
    except Exception:
        # A lexical fallback keeps retrieval available while embeddings are
        # temporarily unavailable; it is retrieval only, never eligibility logic.
        used_embeddings = False
        for item in catalogue:
            documents = list(item.documents)[:2]
            evidence = "\n".join(document.content for document in documents)
            ranked.append((_keyword_score(query, evidence), item, documents))

    ranked.sort(key=lambda row: row[0], reverse=True)

    scope_floor = SCOPE_FLOOR if used_embeddings else SCOPE_FLOOR_KEYWORD
    if not ranked or ranked[0][0] < scope_floor:
        # Nothing in the catalogue is even close to this input — treat it as
        # out of scope (same outcome as failing is_rights_query) instead of
        # forcing the closest-but-irrelevant items on the LLM. No per-item
        # score is kept beyond this one check.
        return []

    return [
        _to_benefit(item, documents)
        for _, item, documents in ranked[:limit]
    ]


def _to_benefit(item, documents) -> Benefit:
    """Build the LLM-facing Benefit from a catalogue item and the evidence
    documents to attach to it. Shared by retrieve_for_text() (ranked results)
    and lookup_benefits_by_slugs() (direct slug lookup, no ranking) so both
    paths hand analyze_rights() the exact same shape of evidence.
    """
    return Benefit(
        name=item.name,
        docs=item.docs,
        contact=item.contact,
        link=item.link,
        detail="\n".join(document.content for document in documents),
        short_description=item.short_description,
        benefit_details=item.benefit_details,
        disqualifying_conditions=item.disqualifying_conditions,
        slug=item.slug,
    )


async def lookup_benefits_by_slugs(slugs: list[str]) -> list[Benefit]:
    """Fetch specific benefits by slug for /analyze-more-rights.

    Deliberately skips embedding + cosine ranking entirely: the slugs given
    here were already ranked once by retrieve_for_text() in the preceding
    /analyze-rights call, so re-ranking them here would just repeat an LLM
    rewrite call and an embedding call for a result that can't change.
    """
    items = await database.get_benefits_by_slugs(slugs)
    return [_to_benefit(item, list(item.documents)) for item in items]