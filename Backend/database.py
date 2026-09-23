"""
database.py — PostgreSQL (async)
==================================
ใช้ SQLAlchemy + asyncpg เพื่อ:
  1. สร้างตารางอัตโนมัติตอน startup
  2. บันทึก inquiry ทุกครั้งที่มีการตรวจสอบสิทธิ
  3. บันทึก AI response
  4. ดึง stats สำหรับ present

ตาราง:
  inquiry_log      — ข้อมูลที่กรอก + สิทธิที่ match
  ai_response_log  — prompt/response จาก AI + เวลาตอบ
"""

import os
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean, Column, ForeignKey, Integer, String, Text,
    DateTime, func, select, text
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine
)
from sqlalchemy.orm import DeclarativeBase

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine — อ่าน DATABASE_URL จาก .env
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("ไม่พบ DATABASE_URL ใน .env")

engine = create_async_engine(
    DATABASE_URL,
    pool_size=5,          # connection pool เล็กๆ พอสำหรับโปรเจคจบ
    max_overflow=10,
    pool_pre_ping=True,   # ตรวจ connection ก่อนใช้ ป้องกัน timeout
    echo=False,           # เปลี่ยนเป็น True ถ้าอยากดู SQL query ตอน debug
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class InquiryLog(Base):
    """
    บันทึกทุกครั้งที่มีการตรวจสอบสิทธิ

    ไม่เก็บ PII (ชื่อ, เลขบัตร, ที่อยู่)
    ตอนนี้ระบบรับข้อความอิสระ (ไม่ใช่ฟอร์มโครงสร้าง) จึง `profile` เก็บได้แค่
    {"text_length": ...} เท่านั้น — คอลัมน์ JSONB นี้เผื่อไว้ให้เก็บ metadata
    วิเคราะห์เพิ่มได้ในอนาคตถ้าต้องการ ไม่ได้แปลว่ามี demographic breakdown จริง
    """
    __tablename__ = "inquiry_log"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    # ปัจจุบันมีแค่ {"text_length": ...} — ดู main.py analytics_profile
    profile       = Column(JSONB, nullable=False)
    # benefits shown to the user after the AI/RAG assessment.
    benefits      = Column(JSONB, nullable=False)
    total_matched = Column(Integer, nullable=False, default=0)
    created_at    = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class AIResponseLog(Base):
    """
    บันทึก AI response ทุกครั้งที่เรียก /explain

    ใช้ monitor cost, latency และ debug prompt
    """
    __tablename__ = "ai_response_log"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    # FK ไม่บังคับเพื่อความง่าย เก็บ profile ซ้ำเล็กน้อยแต่ query ง่ายกว่า
    profile       = Column(JSONB, nullable=False)
    benefits      = Column(JSONB, nullable=False)
    ai_response   = Column(Text, nullable=False)
    elapsed_ms    = Column(Integer, nullable=False)   # เวลาตอบ (ms)
    created_at    = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


# ── RAG knowledge base ──────────────────────────────────────────────────
# These records are the editable source of truth for benefits and retrieved
# evidence.  The initial catalogue is inserted only when empty.
class BenefitRecord(Base):
    __tablename__ = "benefits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False, index=True)
    docs = Column(JSONB, nullable=False, default=list)
    contact = Column(JSONB, nullable=False, default=list)
    link = Column(String(500), nullable=False, default="")
    # LLM-only context; it is intentionally not embedded or used for RAG ranking.
    short_description = Column(Text, nullable=False, default="")
    benefit_details = Column(Text, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True)


class BenefitDocument(Base):
    __tablename__ = "benefit_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    benefit_id = Column(Integer, ForeignKey("benefits.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    source_url = Column(String(500), nullable=False, default="")
    embedding = Column(JSONB, nullable=True)
    active = Column(Boolean, nullable=False, default=True)


class CatalogueItem:
    """Lightweight read model used by rag.py; keeps ORM details out of routes."""
    def __init__(self, record, documents):
        self.name, self.docs, self.contact, self.link = record.name, record.docs, record.contact, record.link
        self.slug = record.slug
        self.short_description = record.short_description
        self.benefit_details = record.benefit_details
        self.documents = documents


# ---------------------------------------------------------------------------
# Lifecycle — เรียกจาก main.py lifespan
# ---------------------------------------------------------------------------

async def connect():
    """สร้างตารางถ้ายังไม่มี (CREATE TABLE IF NOT EXISTS)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to an existing table
        await conn.execute(text("ALTER TABLE benefit_documents ADD COLUMN IF NOT EXISTS embedding JSONB"))
        await conn.execute(text("ALTER TABLE benefits ADD COLUMN IF NOT EXISTS short_description TEXT NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE benefits ADD COLUMN IF NOT EXISTS benefit_details TEXT NOT NULL DEFAULT ''"))
    await seed_rag_catalogue()
    await seed_benefit_explanations()
    logger.info("✅ Database connected — tables ready")


async def disconnect():
    """ปิด connection pool"""
    await engine.dispose()
    logger.info("Database disconnected")


async def seed_rag_catalogue() -> None:
    """Create initial RAG data once; existing DB edits are never overwritten."""
    from rag_catalog import BENEFIT_CATALOG

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(func.count()).select_from(BenefitRecord))).scalar_one()
        if existing:
            return
        for item in BENEFIT_CATALOG:
            record = BenefitRecord(
                slug=item["slug"], name=item["name"], category=item["category"],
                docs=item["docs"], contact=item["contact"], link=item["link"],
                short_description=item.get("short_description", ""),
                benefit_details=item.get("benefit_details", ""),
            )
            session.add(record)
            await session.flush()
            session.add(BenefitDocument(
                benefit_id=record.id, title=f"ข้อมูล {record.name}",
                content=item["document"], source_url=item["link"],
            ))
        await session.commit()
        logger.info("✅ RAG catalogue seeded")


async def seed_benefit_explanations() -> None:
    """Fill blank LLM-only context from the catalogue without overwriting edits."""
    from rag_catalog import BENEFIT_CATALOG

    async with AsyncSessionLocal() as session:
        for item in BENEFIT_CATALOG:
            description = item.get("short_description", "")
            details = item.get("benefit_details", "")
            if not description and not details:
                continue
            record = (await session.execute(
                select(BenefitRecord).where(BenefitRecord.slug == item["slug"])
            )).scalar_one_or_none()
            if record is None:
                continue
            if not (record.short_description or "").strip():
                record.short_description = description
            if not (record.benefit_details or "").strip():
                record.benefit_details = details
        await session.commit()


async def load_rag_catalogue() -> list[CatalogueItem]:
    async with AsyncSessionLocal() as session:
        records = (await session.execute(
            select(BenefitRecord).where(BenefitRecord.active.is_(True)).order_by(BenefitRecord.id)
        )).scalars().all()
        if not records:
            return []
        ids = [record.id for record in records]
        documents = (await session.execute(
            select(BenefitDocument).where(BenefitDocument.benefit_id.in_(ids), BenefitDocument.active.is_(True))
        )).scalars().all()
    by_benefit_documents = {record.id: [] for record in records}
    for document in documents: by_benefit_documents[document.benefit_id].append(document)
    return [
        CatalogueItem(record, by_benefit_documents[record.id])
        for record in records
    ]


async def save_document_embedding(document_id: int, embedding: list[float]) -> None:
    async with AsyncSessionLocal() as session:
        document = await session.get(BenefitDocument, document_id)
        if document is not None and document.embedding is None:
            document.embedding = embedding
            await session.commit()


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

async def log_inquiry(
    profile_data:  dict,
    benefits_data: list[str],
) -> None:
    """
    บันทึก inquiry 1 ครั้ง
    เรียกจาก POST /check-rights ใน main.py
    """
    async with AsyncSessionLocal() as session:
        row = InquiryLog(
            profile       = profile_data,
            benefits      = benefits_data,
            total_matched = len(benefits_data),
        )
        session.add(row)
        await session.commit()
        logger.info(f"Inquiry logged — {len(benefits_data)} matched benefits")


async def log_ai_response(
    profile_data:  dict,
    benefits_data: list[str],
    ai_response:   str,
    elapsed_ms:    int,
) -> None:
    """
    บันทึก AI response หลัง stream จบ
    เรียกจาก POST /explain ใน main.py
    """
    async with AsyncSessionLocal() as session:
        row = AIResponseLog(
            profile     = profile_data,
            benefits    = benefits_data,
            ai_response = ai_response,
            elapsed_ms  = elapsed_ms,
        )
        session.add(row)
        await session.commit()
        logger.info(f"AI response logged — {elapsed_ms}ms")


# ---------------------------------------------------------------------------
# Read functions — ใช้ตอน GET /stats
# ---------------------------------------------------------------------------

async def get_recent_summaries(limit: int = 20) -> list[str]:
    """Recent AI-response summaries for the /stats/insight endpoint.

    ai_response_log stores no PII by design (see InquiryLog docstring), so
    this is safe to hand to an LLM for a dashboard-facing summary.
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(AIResponseLog.ai_response)
            .order_by(AIResponseLog.created_at.desc())
            .limit(limit)
        )).scalars().all()
    return list(rows)


async def get_stats() -> dict:
    """
    ดึงสถิติการใช้งานสำหรับ present อาจารย์
    คืนค่า:
      - total_inquiries    : จำนวน inquiry ทั้งหมด
      - top_benefits       : สิทธิที่ match บ่อยที่สุด 5 อันดับ
      - avg_benefits       : เฉลี่ยสิทธิที่ได้ต่อคน
      - avg_ai_response_ms : เวลาตอบเฉลี่ยของ AI
    """
    async with AsyncSessionLocal() as session:

        # จำนวน inquiry ทั้งหมด
        total_result = await session.execute(
            select(func.count()).select_from(InquiryLog)
        )
        total_inquiries = total_result.scalar() or 0

        # เฉลี่ยสิทธิที่ได้ต่อคน
        avg_result = await session.execute(
            select(func.avg(InquiryLog.total_matched))
        )
        avg_benefits = round(float(avg_result.scalar() or 0), 2)

        # เวลาตอบ AI เฉลี่ย
        avg_ms_result = await session.execute(
            select(func.avg(AIResponseLog.elapsed_ms))
        )
        avg_ai_ms = round(float(avg_ms_result.scalar() or 0), 0)

        # สิทธิที่ระบบแนะนำบ่อย — unpack JSONB array แล้วนับ
        top_result = await session.execute(
            text("""
            SELECT benefit, COUNT(*) AS cnt
            FROM inquiry_log,
                 jsonb_array_elements_text(benefits) AS benefit
            GROUP BY benefit
            ORDER BY cnt DESC
            LIMIT 5
            """)
        )
        top_benefits = [
            {"benefit": row[0], "count": row[1]}
            for row in top_result.fetchall()
        ]

    return {
        "total_inquiries":    total_inquiries,
        "avg_benefits":       avg_benefits,
        "avg_ai_response_ms": avg_ai_ms,
        "top_benefits":       top_benefits,
    }