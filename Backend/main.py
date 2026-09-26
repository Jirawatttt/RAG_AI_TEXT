"""
main.py — FastAPI Entry Point
"""

import os
import time
import hashlib
import logging
from contextlib import asynccontextmanager
from typing import Literal
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import llm
import database
import rag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


# เปิด/ปิด connection ของ database ตามอายุของแอป (ตอน startup/shutdown)
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Starting up — connecting to database...")
    await database.connect()
    yield
    logger.info("🛑 Shutting down — closing database...")
    await database.disconnect()


app = FastAPI(
    title="ระบบแสดงสิทธิประโยชน์ภาครัฐเบื้องต้น",
    version="1.0.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# ── Rate limiting ──
_request_counts: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX    = 20
RATE_LIMIT_WINDOW = 60
# How many of the ranked candidates from retrieve_for_text() go to the LLM
# immediately (shown to the user right away) vs. get deferred behind the
# "load more" button. Kept small on purpose so the LLM prompt — and its
# token cost — stays constant no matter how large the benefit catalogue
# grows (8 today, 100+ later): the LLM only ever sees the top N most
# relevant matches, never the whole catalogue.
RAG_LLM_RESULT_LIMIT = int(os.getenv("RAG_LLM_RESULT_LIMIT", os.getenv("RAG_RESULT_LIMIT", "3")))

# ── /suggest-input cache ──
# AI Input Assistant: keyed by hash(text) — NOT by user/session — so results
# never cross users on purpose. Two users typing identical text is fine and
# even intended (same input should get the same suggestion, and sharing the
# cache entry saves an LLM call); it never mixes one user's data into
# another's context because the key IS the text, nothing else goes in.
# Single in-memory dict is fine for one worker (see AI_Input_Assistant_Plan.md
# §10) — would need a shared store (Redis) if this ever runs multi-worker.
_suggest_input_cache: dict[str, dict] = {}
SUGGEST_INPUT_CACHE_SECONDS = int(os.getenv("SUGGEST_INPUT_CACHE_SECONDS", "900"))
SUGGEST_INPUT_CACHE_MAX_ENTRIES = 500  # cap so distinct inputs can't grow the dict forever


# เก็บผล suggest-input ไว้ในแคชในหน่วยความจำตาม key (hash ของข้อความ)
# เคลียร์รายการที่หมดอายุก่อน แล้ว evict รายการเก่าสุดถ้าแคชเต็ม
def _cache_suggest_input(key: str, result: dict, now: float) -> None:
    """Store a suggestion result for SUGGEST_INPUT_CACHE_SECONDS, pruning
    expired entries opportunistically and evicting the oldest-expiring entry
    if the cache is at capacity."""
    expired = [k for k, v in _suggest_input_cache.items() if v["expires_at"] <= now]
    for k in expired:
        del _suggest_input_cache[k]
    if len(_suggest_input_cache) >= SUGGEST_INPUT_CACHE_MAX_ENTRIES:
        oldest_key = min(_suggest_input_cache, key=lambda k: _suggest_input_cache[k]["expires_at"])
        del _suggest_input_cache[oldest_key]
    _suggest_input_cache[key] = {"result": result, "expires_at": now + SUGGEST_INPUT_CACHE_SECONDS}

# จำกัดจำนวนคำขอต่อ IP ภายในหน้าต่างเวลา (sliding window ง่ายๆ ใน memory)
def check_rate_limit(ip: str) -> bool:
    now = time.time()
    _request_counts[ip] = [t for t in _request_counts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_request_counts[ip]) >= RATE_LIMIT_MAX:
        return False
    _request_counts[ip].append(now)
    return True


# ── Schemas ──

class TextAnalysisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SourceOut(BaseModel):
    title: str
    url: str = ""
    docs: list[str] = []
    contact: list[str] = []
    short_description: str = ""
    benefit_details: str = ""
    # ไม่ได้ตั้งใจให้หน้าเว็บใช้ — เผื่อไว้ให้ debug/ตรวจสอบง่าย field เดียวกับ
    # ที่ analyze_rights_endpoint ดึงไปผูก benefit_id ตอน log BenefitMatch
    slug: str = ""


class AnalysisBenefitOut(BaseModel):
    name: str
    status: Literal["likely_eligible", "needs_verification", "not_eligible"]
    explanation: str
    missing_information: list[str] = []
    sources: list[SourceOut] = []


class TextAnalysisResponse(BaseModel):
    summary: str
    benefits: list[AnalysisBenefitOut]
    follow_up_questions: list[str] = []
    coverage_warning: str
    additional_benefits: list["AdditionalBenefitOut"] = []


class AdditionalBenefitOut(BaseModel):
    slug: str
    name: str


class MoreRightsRequest(TextAnalysisRequest):
    benefit_slugs: list[str] = Field(min_length=1, max_length=10)


class SuggestInputResponse(BaseModel):
    status: Literal["sufficient", "can_add"]
    suggestion: str = ""


# แปลงผลวิเคราะห์ของ LLM เป็นคู่ (slug, status) สำหรับบันทึกลง Benefit_match
def _extract_benefit_hits(analysis: dict) -> list[tuple[str, str]]:
    """แปลงผลจาก llm.analyze_rights() เป็น (slug, status) สำหรับ log_benefit_matches.

    ใช้ slug จาก sources[0] (แหล่งอ้างอิงหลักของแต่ละสิทธิ) แทนชื่อ เพราะชื่อ
    ที่ LLM ตอบกลับมาอาจสะกดคลาดเคลื่อนได้ ในขณะที่ slug มาจาก catalogue
    โดยตรงและผูกกับ benefits.id ได้แม่นยำ 100%
    """
    hits = []
    for item in analysis.get("benefits", []):
        sources = item.get("sources") or []
        slug = sources[0].get("slug") if sources else ""
        if slug:
            hits.append((slug, item.get("status", "needs_verification")))
    return hits


# สร้างผลลัพธ์ว่างแบบปลอดภัย เมื่อ input หลุดสโคปหรือ RAG หาอะไรไม่เจอ (ไม่เรียก LLM)
def _empty_analysis(summary: str) -> dict:
    """Return a safe no-match result without invoking the LLM."""
    return {
        "summary": summary,
        "benefits": [],
        "follow_up_questions": [],
        "coverage_warning": "ระบบวิเคราะห์เฉพาะสิทธิประโยชน์ภาครัฐที่มีในฐานข้อมูล และผลลัพธ์ไม่ใช่การยืนยันสิทธิ",
        "additional_benefits": [],
    }


# ── Routes ──

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "version": app.version}


# flow หลัก: scope check -> RAG ค้นสิทธิ -> ให้ LLM วิเคราะห์ -> log ผลทั้งหมดลง DB
@app.post("/analyze-rights", response_model=TextAnalysisResponse, tags=["Rights"])
async def analyze_rights_endpoint(payload: TextAnalysisRequest, request: Request):
    """AI-first rights analysis from free text and retrieved catalogue evidence."""
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    # One LLM call does both the scope check and the query rewrite; the
    # rewritten query is reused below instead of paying for a second call.
    in_scope, search_query = await rag.prepare_query(payload.text)
    if not in_scope:
        return TextAnalysisResponse(**_empty_analysis(
            "ข้อความนี้ยังไม่อยู่ในขอบเขตการวิเคราะห์สิทธิประโยชน์ภาครัฐหรือมีคำผิด กรุณาตรวจสอบคำถามหรือระบุข้อมูลเกี่ยวกับสิทธิ สวัสดิการ หรือสถานะของคุณเพิ่มเติม"
        ))

    started_at = time.time()
    try:
        candidates, retrieval_quality = await rag.retrieve_for_text(payload.text, search_query=search_query)
        if not candidates:
            return TextAnalysisResponse(**_empty_analysis(
                "ข้อมูลยังไม่เพียงพอสำหรับค้นหาสิทธิที่เกี่ยวข้อง กรุณาระบุเพิ่ม เช่น สถานะงาน ประกันสังคม รายได้ หรือสัญชาติ"
            ))
        primary, additional = candidates[:RAG_LLM_RESULT_LIMIT], candidates[RAG_LLM_RESULT_LIMIT:]
        analysis = await llm.analyze_rights(payload.text, primary)
        analysis["additional_benefits"] = [
            {"slug": benefit.slug, "name": benefit.name} for benefit in additional
        ]
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("AI/RAG analysis failed")
        raise HTTPException(status_code=502, detail="ไม่สามารถวิเคราะห์ด้วย AI และ RAG ได้ในขณะนี้") from exc

    try:
        inquiry_id = await database.log_inquiry(retrieval_quality=retrieval_quality)
        await database.log_benefit_matches(
            hits=_extract_benefit_hits(analysis),
            source="primary",
            inquiry_id=inquiry_id,
        )
        await database.log_ai_response(
            ai_response=analysis["summary"],
            elapsed_ms=int((time.time() - started_at) * 1000),
            inquiry_id=inquiry_id,
        )
    except Exception as exc:
        logger.error("Text-analysis log failed: %s", exc)

    return TextAnalysisResponse(**analysis)


# กด "ดูเพิ่มเติม": ดึงสิทธิที่ถูกจัดอันดับไว้แล้วตรงๆ ด้วย slug (ไม่ embed/ไม่ scope-check ซ้ำ)
@app.post("/analyze-more-rights", response_model=TextAnalysisResponse, tags=["Rights"])
async def analyze_more_rights_endpoint(payload: MoreRightsRequest, request: Request):
    """Explain deferred benefits the user asked to see, by slug.

    Deliberately does NOT call rag.retrieve_for_text() again: the slugs
    given here were already ranked once by the preceding /analyze-rights
    call for this same text, and that ranking cannot change for the same
    input. Re-running retrieval would pay for a second LLM query-rewrite
    call and a second embedding call just to reproduce the same order —
    so this looks the benefits up directly by slug instead (a plain DB
    query, no LLM/embedding cost at all) and only spends an LLM call on
    the one thing that does need it: writing the explanation.

    No scope re-check either, for the same reason as before: this endpoint
    is only reachable with a text that already passed prepare_query()'s
    scope check in the preceding /analyze-rights call.
    """
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    benefits = await rag.lookup_benefits_by_slugs(payload.benefit_slugs)
    found_slugs = {benefit.slug for benefit in benefits}
    if not all(slug in found_slugs for slug in payload.benefit_slugs):
        raise HTTPException(status_code=422, detail="มีรายการสิทธิที่ไม่พบในฐานข้อมูล")

    started_at = time.time()
    try:
        analysis = await llm.analyze_rights(payload.text, benefits)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Additional AI/RAG analysis failed")
        raise HTTPException(status_code=502, detail="ไม่สามารถวิเคราะห์สิทธิเพิ่มเติมได้ในขณะนี้") from exc
    analysis["additional_benefits"] = []

    # เดิม endpoint นี้ไม่ log อะไรเลย ทำให้ "เวลา AI เฉลี่ย" และบาร์กราฟใน
    # /stats ไม่เห็นการเรียก LLM กลุ่มนี้เลยแม้ user จะเห็นผลจริงหน้าจอ
    # inquiry_id เป็น None เพราะยังไม่ได้ผูก session กับ /analyze-rights
    # รอบแรก (ดูคอมเมนต์ BenefitMatch.inquiry_id ใน database.py)
    try:
        await database.log_benefit_matches(
            hits=_extract_benefit_hits(analysis),
            source="additional",
        )
        await database.log_ai_response(
            ai_response=analysis["summary"],
            elapsed_ms=int((time.time() - started_at) * 1000),
        )
    except Exception as exc:
        logger.error("Additional-rights log failed: %s", exc)

    return TextAnalysisResponse(**analysis)


# แนะนำ field ที่ควรเพิ่มระหว่าง user พิมพ์ — แยกอิสระจาก /analyze-rights และไม่ log ลง DB
@app.post("/suggest-input", response_model=SuggestInputResponse, tags=["Rights"])
async def suggest_input_endpoint(payload: TextAnalysisRequest, request: Request):
    """AI Input Assistant — runs while the user is still typing, independent
    of /analyze-rights (does not gate it, does not need it to run first, and
    does not run after it either). Purely a stateless suggestion layer over
    Benefits + the user's current text: recommends at most one optional field
    that would help RAG matching and analyze_rights() status accuracy.

    Never writes to the database — no Inquiry_log/Benefit_match/Ai_response_log
    rows — this feature is intentionally kept out of analytics entirely (see
    AI_Input_Assistant_Plan.md §4, §11).

    Cached 15 minutes by hash(text) — see _cache_suggest_input above for why
    that's safe across concurrent/simultaneous users.
    """
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    text = payload.text.strip()
    now = time.time()
    cache_key = hashlib.sha256(text.encode("utf-8")).hexdigest()
    cached = _suggest_input_cache.get(cache_key)
    if cached and now < cached["expires_at"]:
        return cached["result"]

    # Reuses the same scope check as /analyze-rights — text that isn't about
    # government benefits gets an empty result without spending an LLM call
    # on suggest_input_fields() at all.
    in_scope, search_query = await rag.prepare_query(text)
    if not in_scope:
        result = {"status": "sufficient", "suggestion": ""}
        _cache_suggest_input(cache_key, result, now)
        return result

    try:
        candidates, _ = await rag.retrieve_for_text(text, search_query=search_query)
        if not candidates:
            result = {"status": "sufficient", "suggestion": ""}
        else:
            result = await llm.suggest_input_fields(text, candidates[:RAG_LLM_RESULT_LIMIT])
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Input suggestion failed")
        raise HTTPException(status_code=502, detail="ไม่สามารถแนะนำการกรอกข้อมูลได้ในขณะนี้") from exc

    _cache_suggest_input(cache_key, result, now)
    return result


# ดึงสถิติสรุปทั้งหมดจาก database.get_stats() สำหรับหน้า analytics/นำเสนอ
@app.get("/stats", tags=["Analytics"])
async def get_stats():
    try:
        return await database.get_stats()
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถดึงข้อมูลได้")