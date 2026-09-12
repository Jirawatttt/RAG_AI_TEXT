"""
main.py — FastAPI Entry Point
"""

import os
import time
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
# immediately vs. get deferred behind the "load more" button. Defaults to
# the same size as rag.DEFAULT_DISCOVERY_RESULT_LIMIT (10), so with the
# current 8-benefit catalogue every relevant match is analyzed right away
# and additional_benefits stays empty. If the catalogue grows past 10
# later, lower this on purpose to bring back progressive disclosure.
RAG_LLM_RESULT_LIMIT = int(os.getenv("RAG_LLM_RESULT_LIMIT", os.getenv("RAG_RESULT_LIMIT", "10")))

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


@app.post("/analyze-rights", response_model=TextAnalysisResponse, tags=["Rights"])
async def analyze_rights_endpoint(payload: TextAnalysisRequest, request: Request):
    """AI-first rights analysis from free text and retrieved catalogue evidence."""
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    if not rag.is_rights_query(payload.text):
        return TextAnalysisResponse(**_empty_analysis(
            "ข้อความนี้ยังไม่อยู่ในขอบเขตการวิเคราะห์สิทธิประโยชน์ภาครัฐหรือมีคำผิด กรุณาตรวจสอบคำถามหรือระบุข้อมูลเกี่ยวกับสิทธิ สวัสดิการ หรือสถานะของคุณเพิ่มเติม"
        ))

    started_at = time.time()
    try:
        candidates = await rag.retrieve_for_text(payload.text)
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
        benefit_names = [item["name"] for item in analysis["benefits"]]
        analytics_profile = {"text_length": len(payload.text)}
        await database.log_inquiry(
            profile_data=analytics_profile,
            benefits_data=benefit_names,
        )
        await database.log_ai_response(
            profile_data=analytics_profile,
            benefits_data=benefit_names,
            ai_response=analysis["summary"],
            elapsed_ms=int((time.time() - started_at) * 1000),
        )
    except Exception as exc:
        logger.error("Text-analysis log failed: %s", exc)

    return TextAnalysisResponse(**analysis)


@app.post("/analyze-more-rights", response_model=TextAnalysisResponse, tags=["Rights"])
async def analyze_more_rights_endpoint(payload: MoreRightsRequest, request: Request):
    """Summarize deferred RAG matches only after the user asks to see them."""
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")
    if not rag.is_rights_query(payload.text):
        raise HTTPException(status_code=422, detail="ข้อความอยู่นอกขอบเขตการวิเคราะห์สิทธิ")

    candidates = await rag.retrieve_for_text(payload.text)
    allowed = {benefit.slug: benefit for benefit in candidates[RAG_LLM_RESULT_LIMIT:]}
    if not all(slug in allowed for slug in payload.benefit_slugs):
        raise HTTPException(status_code=422, detail="มีรายการสิทธิที่ไม่ได้อยู่ในผล RAG ของข้อความนี้")

    try:
        analysis = await llm.analyze_rights(
            payload.text, [allowed[slug] for slug in payload.benefit_slugs]
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Additional AI/RAG analysis failed")
        raise HTTPException(status_code=502, detail="ไม่สามารถวิเคราะห์สิทธิเพิ่มเติมได้ในขณะนี้") from exc
    analysis["additional_benefits"] = []
    return TextAnalysisResponse(**analysis)


@app.get("/stats", tags=["Analytics"])
async def get_stats():
    try:
        return await database.get_stats()
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถดึงข้อมูลได้")