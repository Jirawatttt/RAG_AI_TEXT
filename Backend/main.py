"""
main.py — FastAPI Entry Point
"""

import os
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional, Literal
from collections import defaultdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from models import (
    UserProfile, Nationality, SocialSecurityType,
    EmploymentStatus, ChildrenStatus,
)
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

def check_rate_limit(ip: str) -> bool:
    now = time.time()
    _request_counts[ip] = [t for t in _request_counts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_request_counts[ip]) >= RATE_LIMIT_MAX:
        return False
    _request_counts[ip].append(now)
    return True


# ── Schemas ──

class ProfileRequest(BaseModel):
    """ทุก field เป็น Optional — user กรอกแค่บางช่องได้

    ใช้ Literal แทน str ธรรมดา เพื่อให้ตรงกับ option จริงใน input.html
    ค่าที่ไม่ตรงกับตัวเลือกของฟอร์มจะถูก FastAPI reject เป็น 422 ทันที
    เพื่อให้ข้อมูลที่ส่งเข้า RAG มีรูปแบบคงที่
    """
    age:             Optional[int] = Field(None, ge=0, le=120)
    nationality:     Optional[Literal["thai", "other"]] = None
    social_security: Optional[Literal["33", "39", "40", "none"]] = None
    employment:      Optional[Literal["employed", "self", "unemployed"]] = None
    children:        Optional[Literal["0", "1", "2"]] = None
    disability:      Optional[Literal["yes", "no"]] = None


class BenefitOut(BaseModel):
    name:    str
    docs:    list[str]
    contact: list[str]
    link:    str = ""


class CheckRightsResponse(BaseModel):
    total:    int
    benefits: list[BenefitOut]


def _to_user_profile(req: ProfileRequest) -> UserProfile:
    """แปลง ProfileRequest → UserProfile รับ None ได้ทุก field"""

    def parse_enum(enum_cls, val):
        if val is None:
            return None
        try:
            return enum_cls(val)
        except ValueError:
            return None

    return UserProfile(
        age                 = req.age,
        nationality         = parse_enum(Nationality, req.nationality),
        social_security     = parse_enum(SocialSecurityType, req.social_security),
        employment          = parse_enum(EmploymentStatus, req.employment),
        children            = parse_enum(ChildrenStatus, req.children),
        has_disability_card = (req.disability == "yes") if req.disability is not None else None,
    )


# ── Routes ──

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok", "version": app.version}


@app.post("/check-rights", response_model=CheckRightsResponse, tags=["Rights"])
async def check_rights_endpoint(payload: ProfileRequest, request: Request):
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    profile  = _to_user_profile(payload)
    benefits = await rag.retrieve_candidates(profile)

    try:
        await database.log_inquiry(
            profile_data  = payload.model_dump(),
            benefits_data = [b.name for b in benefits],
        )
    except Exception as e:
        logger.error(f"DB log failed: {e}")

    return CheckRightsResponse(
        total    = len(benefits),
        benefits = [
            BenefitOut(
                name=b.name,
                docs=b.docs,
                contact=b.contact,
                link=b.link,
            )
            for b in benefits
        ],
    )


@app.post("/explain", tags=["AI"])
async def explain_endpoint(payload: ProfileRequest, request: Request):
    if not check_rate_limit(request.client.host):
        raise HTTPException(status_code=429, detail="Too many requests")

    profile  = _to_user_profile(payload)
    benefits = await rag.retrieve_candidates(profile)

    if not benefits:
        async def no_benefit():
            yield "data: ยังไม่มีข้อมูลสิทธิในคลังความรู้ของระบบ\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(no_benefit(), media_type="text/event-stream")

    start = time.time()

    async def stream_with_log():
        full = []
        async for chunk in llm.explain_benefits(profile=profile, benefits=benefits):
            full.append(chunk)
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"
        try:
            await database.log_ai_response(
                profile_data  = payload.model_dump(),
                benefits_data = [b.name for b in benefits],
                ai_response   = "".join(full),
                elapsed_ms    = int((time.time() - start) * 1000),
            )
        except Exception as e:
            logger.error(f"DB ai_log failed: {e}")

    return StreamingResponse(
        stream_with_log(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/stats", tags=["Analytics"])
async def get_stats():
    try:
        return await database.get_stats()
    except Exception as e:
        logger.error(f"Stats failed: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถดึงข้อมูลได้")
