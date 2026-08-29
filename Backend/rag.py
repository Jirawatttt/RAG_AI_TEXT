"""RAG retrieval for benefit information, with no rule-based eligibility check."""
from __future__ import annotations

import re
from math import sqrt

import database
import llm
from models import Benefit, UserProfile


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
    return set(re.findall(r"[\wก-๙]+", text.lower()))


def _keyword_score(query: str, text: str) -> float:
    query_tokens, document_tokens = _tokens(query), _tokens(text)
    return len(query_tokens & document_tokens) / len(query_tokens) if query_tokens else 0.0


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
            matched_conditions=[],
            missing_conditions=[],
        )
        for _, item, documents in ranked
    ]
