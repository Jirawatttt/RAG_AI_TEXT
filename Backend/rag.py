"""RAG retrieval for benefit information, with no rule-based eligibility check."""
from __future__ import annotations

import os
import re
from math import sqrt

import database
import llm
from models import Benefit, UserProfile


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


def _profile_query(profile: UserProfile) -> str:
    values = ["ข้อมูลสิทธิ สวัสดิการ เงินช่วยเหลือ"]
    if profile.age is not None:
        values.append(f"อายุ {profile.age} ปี")
    if profile.nationality is not None:
        values.append("สัญชาติไทย" if profile.nationality.value == "thai" else "ไม่ใช่สัญชาติไทย")
    if profile.social_security is not None:
        values.append(f"ประกันสังคมมาตรา {profile.social_security.value}")
    if profile.employment is not None:
        values.append(f"สถานะงาน {profile.employment.value}")
    if profile.children is not None:
        values.append(f"สถานะบุตร {profile.children.value}")
    if profile.has_disability_card is not None:
        values.append("มีบัตรคนพิการ" if profile.has_disability_card else "ไม่มีบัตรคนพิการ")
    return ", ".join(values)


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


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = sqrt(sum(x * x for x in left)) * sqrt(sum(x * x for x in right))
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0


async def _rank_documents(query: str, documents: list[database.BenefitDocument]) -> list[database.BenefitDocument]:
    """Rank source documents with vectors, then fall back to keyword overlap."""
    try:
        query_embedding = await llm.embed_text(query)
        scores = []
        for document in documents:
            embedding = document.embedding
            if embedding is None:
                embedding = await llm.embed_text(document.content)
                await database.save_document_embedding(document.id, embedding)
            scores.append((document, _cosine(query_embedding, embedding)))
    except Exception:
        scores = [(document, _keyword_score(query, document.content)) for document in documents]
    return [document for document, _ in sorted(scores, key=lambda row: row[1], reverse=True)[:2]]


async def retrieve_candidates(profile: UserProfile) -> list[Benefit]:
    """Retrieve evidence for AI review; do not evaluate condition/rule records.

    Every active benefit stays in the candidate set while the catalogue is
    small.  This prevents retrieval from silently rejecting a possible right;
    the LLM receives the ranked evidence and decides what is relevant.
    """
    catalogue = await database.load_rag_catalogue()
    query = _profile_query(profile)
    ranked: list[tuple[float, object, list[database.BenefitDocument]]] = []

    for item in catalogue:
        documents = await _rank_documents(query, list(item.documents)) if item.documents else []
        evidence = "\n".join(document.content for document in documents)
        ranked.append((_keyword_score(query, evidence), item, documents))

    ranked.sort(key=lambda row: row[0], reverse=True)
    return [
        Benefit(
            name=item.name,
            docs=item.docs,
            contact=item.contact,
            link=item.link,
            detail="\n".join(document.content for document in documents),
            short_description=item.short_description,
            benefit_details=item.benefit_details,
            matched_conditions=[],
            missing_conditions=[],
        )
        for _, item, documents in ranked
    ]


async def retrieve_for_text(user_text: str, limit: int = DEFAULT_DISCOVERY_RESULT_LIMIT) -> list[Benefit]:
    """Retrieve the most relevant catalogue evidence from an unrestricted user query.

    This function performs retrieval only.  It never decides eligibility; that
    decision is made by the LLM from the returned source text.
    """
    query = normalize_text(user_text)
    catalogue = await database.load_rag_catalogue()
    ranked: list[tuple[float, object, list[database.BenefitDocument]]] = []

    used_embeddings = True
    try:
        query_embedding = await llm.embed_text(query)
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
        Benefit(
            name=item.name,
            docs=item.docs,
            contact=item.contact,
            link=item.link,
            detail="\n".join(document.content for document in documents),
            short_description=item.short_description,
            benefit_details=item.benefit_details,
            slug=item.slug,
            matched_conditions=[],
            missing_conditions=[],
        )
        for _, item, documents in ranked[:limit]
    ]