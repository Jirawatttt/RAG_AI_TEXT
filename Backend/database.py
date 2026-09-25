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
    บันทึกทุกครั้งที่มีการตรวจสอบสิทธิ (1 แถว = 1 ครั้งที่กด /analyze-rights)

    ไม่เก็บ PII (ชื่อ, เลขบัตร, ที่อยู่)
    ตอนนี้ระบบรับข้อความอิสระ (ไม่ใช่ฟอร์มโครงสร้าง) จึง `profile` เก็บได้แค่
    {"text_length": ...} เท่านั้น — คอลัมน์ JSONB นี้เผื่อไว้ให้เก็บ metadata
    วิเคราะห์เพิ่มได้ในอนาคตถ้าต้องการ ไม่ได้แปลว่ามี demographic breakdown จริง

    ไม่เก็บ `benefits`/`total_matched` แล้ว — ย้ายไปตาราง BenefitMatch แทน
    เพื่อให้ join กับ benefits.name ได้ตรง ๆ (ไม่ต้องแกะ JSONB) และไม่มีชื่อ
    สิทธิค้างเก่าถ้ามีการแก้ไขชื่อใน benefits ภายหลัง
    """
    __tablename__ = "inquiry_log"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    # ปัจจุบันมีแค่ {"text_length": ...} — ดู main.py analytics_profile
    profile       = Column(JSONB, nullable=False)
    created_at    = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,  # ใช้ ORDER BY / GROUP BY ตามเวลาแทบทุก query สถิติ
    )


class BenefitMatch(Base):
    """
    บันทึก 1 แถวต่อ 1 สิทธิที่ AI ประเมินให้ user ในแต่ละครั้ง — ใช้แทนคอลัมน์
    `benefits` (JSONB ของชื่อ) เดิมใน inquiry_log/ai_response_log

    benefit_id อ้างกลับไปที่ benefits.id ตรง ๆ (ไม่ใช่เก็บชื่อ) จึง join เอา
    ชื่อปัจจุบันมาแสดงในบาร์กราฟได้เสมอ ต่อให้ภายหลังมีการแก้ชื่อสิทธิ

    source แยกว่าสิทธินี้มาจาก flow หลัก (primary, ถูก LLM ประเมินตอน
    /analyze-rights) หรือมาจากตอนกด "ดูเพิ่มเติม" (additional, ประเมินตอน
    /analyze-more-rights) — ทั้งสอง flow ควร log เข้ามาที่นี่เหมือนกัน

    inquiry_id เป็น nullable เพราะ /analyze-more-rights ยังไม่มีการส่ง
    inquiry_id เดิมกลับมาจาก client (ต้องแก้ API contract เพิ่มถ้าจะผูกแบบ
    เป๊ะ) — ปล่อย NULL ไปก่อนไม่กระทบการนับบาร์กราฟหรือค่าเฉลี่ยเวลา AI
    """
    __tablename__ = "benefit_match"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    inquiry_id = Column(Integer, ForeignKey("inquiry_log.id", ondelete="CASCADE"), nullable=True, index=True)
    benefit_id = Column(Integer, ForeignKey("benefits.id", ondelete="CASCADE"), nullable=False, index=True)
    status     = Column(String(30), nullable=False)   # likely_eligible / needs_verification / not_eligible
    source     = Column(String(20), nullable=False, default="primary")  # primary / additional
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class AIResponseLog(Base):
    """
    บันทึก AI response ทุกครั้งที่เรียก LLM เพื่อสรุปคำตอบให้ user
    (ทั้งจาก /analyze-rights และ /analyze-more-rights)

    ใช้ monitor cost, latency และ debug prompt

    ตัด `profile`/`benefits` ที่เคยเก็บซ้ำออก เพราะตอนนี้มี inquiry_id (FK)
    ผูกกลับไปที่ inquiry_log ได้แล้ว และ benefits ก็ดูได้จาก BenefitMatch —
    ไม่จำเป็นต้องเก็บซ้ำสองที่เหมือนก่อนหน้านี้
    """
    __tablename__ = "ai_response_log"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    # nullable ด้วยเหตุผลเดียวกับ BenefitMatch.inquiry_id ข้างบน
    inquiry_id    = Column(Integer, ForeignKey("inquiry_log.id", ondelete="CASCADE"), nullable=True, index=True)
    ai_response   = Column(Text, nullable=False)
    elapsed_ms    = Column(Integer, nullable=False)   # เวลาตอบ (ms)
    created_at    = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
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
    # เงื่อนไข/สถานะที่ทำให้ "ไม่ได้รับสิทธิ" (เดิมเคยเป็น RAG document chunk
    # ประเภท exclusions/continuity ใน benefit_documents) — ย้ายมาไว้ที่นี่แทน
    # เพราะเนื้อหานี้ไม่ได้ใช้คัดกรองความใกล้เคียงกับ input ของ user (ไม่มี
    # embedding, ไม่ผ่าน RAG ranking) แต่ยังต้องส่งให้ LLM วิเคราะห์ทุกครั้ง
    # เหมือน short_description/benefit_details ด้านบน เพื่อใช้ตัดสิน
    # not_eligible โดยตรง
    disqualifying_conditions = Column(Text, nullable=False, default="")
    active = Column(Boolean, nullable=False, default=True)


class BenefitDocument(Base):
    __tablename__ = "benefit_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    benefit_id = Column(Integer, ForeignKey("benefits.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(JSONB, nullable=True)
    # วันที่แอดมิน "หาข้อมูล/ตรวจสอบ" content นี้กับเกณฑ์ของรัฐล่าสุด — ไม่ใช่
    # วันที่แก้ record ในฐานข้อมูล (นั่นคือหน้าที่ของ trigger ที่ล้าง embedding
    # ด้านล่าง) แต่เป็นวันที่คนตรวจว่าเนื้อหายังตรงกับประกาศ/กฎหมายปัจจุบันไหม
    # ใช้เทียบว่าห่างจากวันนี้นานแค่ไหน เพื่อรู้ว่ารายการไหนถึงรอบต้องตรวจซ้ำ
    checked_at = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, nullable=False, default=True)


class CatalogueItem:
    """Lightweight read model used by rag.py; keeps ORM details out of routes."""
    def __init__(self, record, documents):
        self.name, self.docs, self.contact, self.link = record.name, record.docs, record.contact, record.link
        self.slug = record.slug
        self.short_description = record.short_description
        self.benefit_details = record.benefit_details
        self.disqualifying_conditions = record.disqualifying_conditions
        self.documents = documents


# ---------------------------------------------------------------------------
# Lifecycle — เรียกจาก main.py lifespan
# ---------------------------------------------------------------------------

async def connect():
    """สร้างตารางถ้ายังไม่มี (CREATE TABLE IF NOT EXISTS)"""
    async with engine.begin() as conn:
        # create_all สร้างตารางใหม่ (เช่น benefit_match) แต่ไม่แก้ตารางเก่าที่มีอยู่แล้ว
        await conn.run_sync(Base.metadata.create_all)

        # ── migration เดิม ──
        await conn.execute(text("ALTER TABLE benefit_documents ADD COLUMN IF NOT EXISTS embedding JSONB"))
        await conn.execute(text("ALTER TABLE benefits ADD COLUMN IF NOT EXISTS short_description TEXT NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE benefits ADD COLUMN IF NOT EXISTS benefit_details TEXT NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE benefits ADD COLUMN IF NOT EXISTS disqualifying_conditions TEXT NOT NULL DEFAULT ''"))
        await conn.execute(text("ALTER TABLE benefit_documents ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ"))
        # ── migration ใหม่: ตัด source_url ออก — table นี้ใช้แค่สำหรับ RAG
        # (content + embedding) เท่านั้น ส่วนลิงก์อ้างอิงใช้ benefits.link
        # ร่วมกันอยู่แล้ว (ดู seed_rag_catalogue เดิมที่ fallback ไป
        # item["link"] เสมอ ไม่เคยมี source_url แยกต่างหากจริง ๆ)
        await conn.execute(text("ALTER TABLE benefit_documents DROP COLUMN IF EXISTS source_url"))

        # ── embedding invalidation trigger ──────────────────────────────
        # ปัญหาเดิม: save_document_embedding() เซ็ต embedding เฉพาะตอนที่ยัง
        # เป็น NULL เท่านั้น ถ้าแอดมินแก้ `content` ของแถวที่มี embedding อยู่
        # แล้ว (เช่นแก้เกณฑ์รายได้ที่เปลี่ยนกฎ) embedding เดิมจะค้างอยู่และถูก
        # ใช้ rank ต่อไปทั้งที่ไม่ตรงกับข้อความใหม่แล้ว — เป็นบั๊กเงียบที่ทำให้
        # RAG จัดอันดับผิดโดยไม่มี error ใดๆ ให้เห็น
        #
        # แก้ที่ระดับ DB ด้วย trigger แทนที่จะพึ่ง application code ทุกจุดที่
        # แก้ content (รวมถึงตอนแก้ตรงผ่าน SQL/DB client โดยไม่ผ่าน backend
        # เลยด้วย) — ทุกครั้งที่ content เปลี่ยน ให้ embedding เป็น NULL ทันที
        # รอบ retrieve_for_text() ถัดไปจะเห็น embedding เป็น NULL แล้วสั่ง
        # embed ใหม่ + save ให้เองตามโค้ดเดิมใน rag.py อยู่แล้ว ไม่ต้องแก้
        # rag.py เพิ่ม
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION invalidate_benefit_document_embedding()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.content IS DISTINCT FROM OLD.content THEN
                    NEW.embedding := NULL;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        await conn.execute(text(
            "DROP TRIGGER IF EXISTS trg_invalidate_benefit_document_embedding ON benefit_documents"
        ))
        await conn.execute(text("""
            CREATE TRIGGER trg_invalidate_benefit_document_embedding
            BEFORE UPDATE ON benefit_documents
            FOR EACH ROW
            EXECUTE FUNCTION invalidate_benefit_document_embedding()
        """))

        # ── migration ใหม่: ย้าย benefits/total_matched ออกจาก inquiry_log,
        #    ย้าย profile/benefits ออกจาก ai_response_log แล้วผูก inquiry_id
        #    แทน (ดูเหตุผลในคอมเมนต์ของแต่ละ model ด้านบน) ──
        await conn.execute(text("ALTER TABLE inquiry_log DROP COLUMN IF EXISTS benefits"))
        await conn.execute(text("ALTER TABLE inquiry_log DROP COLUMN IF EXISTS total_matched"))
        await conn.execute(text("ALTER TABLE ai_response_log DROP COLUMN IF EXISTS profile"))
        await conn.execute(text("ALTER TABLE ai_response_log DROP COLUMN IF EXISTS benefits"))
        await conn.execute(text(
            "ALTER TABLE ai_response_log ADD COLUMN IF NOT EXISTS inquiry_id "
            "INTEGER REFERENCES inquiry_log(id) ON DELETE CASCADE"
        ))

        # create_all ใส่ index ให้เฉพาะตารางที่เพิ่งสร้างใหม่ — ตารางเก่าที่มี
        # อยู่แล้วต้องสั่ง CREATE INDEX เองแยกต่างหาก
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_inquiry_log_created_at ON inquiry_log (created_at)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_response_log_created_at ON ai_response_log (created_at)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ai_response_log_inquiry_id ON ai_response_log (inquiry_id)"))
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
                disqualifying_conditions=item.get("disqualifying_conditions", ""),
            )
            session.add(record)
            await session.flush()
            # แต่ละสิทธิตอนนี้เก็บเป็นหลาย chunk ต่อ 1 มิติเกณฑ์ (ดู
            # rag_catalog.py) แทนย่อหน้าเดียวที่อัดทุกเกณฑ์ไว้ด้วยกัน —
            # ทำให้ embedding ต่อ chunk เจาะจงสัญญาณกว่าตอน user ถามมาแค่
            # มิติเดียว (เช่น "อายุ 65") และแก้ทีละ chunk ได้โดยไม่กระทบ
            # เกณฑ์ข้ออื่น retrieve_for_text() เดิมรองรับหลาย document ต่อ
            # benefit อยู่แล้ว (จัดอันดับแล้วหยิบ top-2) จึงไม่ต้องแก้ rag.py
            for doc in item["documents"]:
                session.add(BenefitDocument(
                    benefit_id=record.id,
                    title=doc["title"],
                    content=doc["content"],
                    checked_at=doc.get("checked_at"),
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
            disqualifying = item.get("disqualifying_conditions", "")
            if not description and not details and not disqualifying:
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
            if not (record.disqualifying_conditions or "").strip():
                record.disqualifying_conditions = disqualifying
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


async def get_benefits_by_slugs(slugs: list[str]) -> list[CatalogueItem]:
    """Fetch specific active benefits directly by slug — no embedding, no
    cosine similarity, no re-ranking. Used by /analyze-more-rights so that
    viewing benefits already ranked once by retrieve_for_text() never pays
    for a second embedding+ranking pass.
    """
    if not slugs:
        return []
    async with AsyncSessionLocal() as session:
        records = (await session.execute(
            select(BenefitRecord).where(BenefitRecord.slug.in_(slugs), BenefitRecord.active.is_(True))
        )).scalars().all()
        if not records:
            return []
        ids = [record.id for record in records]
        documents = (await session.execute(
            select(BenefitDocument).where(BenefitDocument.benefit_id.in_(ids), BenefitDocument.active.is_(True))
        )).scalars().all()
    by_benefit_documents: dict[int, list] = {record.id: [] for record in records}
    for document in documents:
        by_benefit_documents[document.benefit_id].append(document)
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


async def mark_document_checked(document_id: int, checked_at: datetime | None = None) -> None:
    """Admin action: record that this chunk's content was just verified
    against current government criteria. Does NOT touch `content` or
    `embedding` — this is purely a "last verified" timestamp.
    """
    async with AsyncSessionLocal() as session:
        document = await session.get(BenefitDocument, document_id)
        if document is not None:
            document.checked_at = checked_at or datetime.now(timezone.utc)
            await session.commit()


async def get_documents_needing_review(stale_after_days: int = 180) -> list[dict]:
    """List active RAG chunks that either have never been checked
    (`checked_at IS NULL`, e.g. anything seeded without a real check date)
    or were last checked more than `stale_after_days` ago — for an admin
    screen/report, not used by the retrieval or analysis path.
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("""
            SELECT bd.id, b.name AS benefit_name, b.link, bd.title, bd.checked_at
            FROM benefit_documents bd
            JOIN benefits b ON b.id = bd.benefit_id
            WHERE bd.active IS TRUE
              AND (bd.checked_at IS NULL OR bd.checked_at < now() - (:days || ' days')::interval)
            ORDER BY bd.checked_at ASC NULLS FIRST
        """), {"days": stale_after_days})).all()
    return [
        {
            "document_id": row.id,
            "benefit_name": row.benefit_name,
            "title": row.title,
            "checked_at": row.checked_at,
            "link": row.link,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

async def log_inquiry(profile_data: dict) -> int:
    """
    บันทึก inquiry 1 ครั้ง (แถวเดียว ไม่มี benefits/total_matched แล้ว)
    เรียกจาก POST /analyze-rights ใน main.py

    คืนค่า id ของแถวที่สร้าง เผื่ออนาคตอยากผูก inquiry_id ให้แม่นยำตอน
    /analyze-more-rights (ต้องส่ง id นี้กลับไปให้ client เก็บไว้ส่งมาด้วย)
    """
    async with AsyncSessionLocal() as session:
        row = InquiryLog(profile=profile_data)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        logger.info(f"Inquiry logged — id={row.id}")
        return row.id


async def log_benefit_matches(
    hits: list[tuple[str, str]],
    source: str,
    inquiry_id: int | None = None,
) -> None:
    """
    บันทึกสิทธิที่ AI ประเมินให้ user 1 ครั้ง — แทนที่การยัดชื่อสิทธิลง JSONB
    เดิม ด้วยการ insert เป็นแถว ๆ ผูกกับ benefits.id จริง

    hits: list ของ (slug, status) เช่น [("elderly_allowance", "likely_eligible")]
    source: "primary" (จาก /analyze-rights) หรือ "additional" (จาก
    /analyze-more-rights) — ใช้แยกวิเคราะห์ภายหลังได้ว่าสิทธิไหนมักถูกเจอ
    ตอน flow ไหน โดยไม่ต้องเพิ่มตารางใหม่อีก

    เรียกจากทั้ง /analyze-rights และ /analyze-more-rights ใน main.py —
    เดิม /analyze-more-rights ไม่เคย log บาร์กราฟจึงไม่เห็นสิทธิกลุ่มนี้เลย
    """
    if not hits:
        return
    async with AsyncSessionLocal() as session:
        slugs = [slug for slug, _ in hits]
        rows = (await session.execute(
            select(BenefitRecord.id, BenefitRecord.slug).where(BenefitRecord.slug.in_(slugs))
        )).all()
        id_by_slug = {slug: benefit_id for benefit_id, slug in rows}

        matched = 0
        for slug, status in hits:
            benefit_id = id_by_slug.get(slug)
            if benefit_id is None:
                # slug ไม่พบใน benefits (เช่นถูกลบไปแล้ว) — ข้ามแถวนี้แทนที่จะ error
                logger.warning(f"BenefitMatch skipped — unknown slug: {slug}")
                continue
            session.add(BenefitMatch(
                inquiry_id=inquiry_id, benefit_id=benefit_id, status=status, source=source,
            ))
            matched += 1
        await session.commit()
        logger.info(f"Benefit matches logged — {matched} ({source})")


async def log_ai_response(
    ai_response: str,
    elapsed_ms:  int,
    inquiry_id:  int | None = None,
) -> None:
    """
    บันทึก AI response ทุกครั้งที่เรียก LLM
    เรียกจากทั้ง /analyze-rights และ /analyze-more-rights ใน main.py —
    เดิมมีแค่ /analyze-rights ที่ log ทำให้ "เวลา AI เฉลี่ย" ใน /stats
    ไม่รวมการเรียก LLM ของ /analyze-more-rights เลย
    """
    async with AsyncSessionLocal() as session:
        row = AIResponseLog(inquiry_id=inquiry_id, ai_response=ai_response, elapsed_ms=elapsed_ms)
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
      - total_inquiries    : จำนวนครั้งที่มีการตรวจสอบ (นับ /analyze-rights)
      - top_benefits       : สิทธิที่ "น่าจะได้" (likely_eligible) บ่อยที่สุด 5 อันดับ
      - avg_benefits       : เฉลี่ยสิทธิ primary ที่แนะนำต่อครั้ง (ถูก cap ไว้ที่
                              RAG_LLM_RESULT_LIMIT ใน main.py จึงมักใกล้ค่า cap นั้น)
      - avg_ai_response_ms : เวลาตอบเฉลี่ยของ AI — รวมทั้ง /analyze-rights
                              และ /analyze-more-rights เพราะทั้งคู่ log เข้า
                              ai_response_log ตารางเดียวกันแล้ว
    """
    async with AsyncSessionLocal() as session:

        # จำนวนครั้งที่ตรวจสอบทั้งหมด
        total_result = await session.execute(
            select(func.count()).select_from(InquiryLog)
        )
        total_inquiries = total_result.scalar() or 0

        # เวลาตอบ AI เฉลี่ย — รวมทุก endpoint ที่เรียก LLM
        avg_ms_result = await session.execute(
            select(func.avg(AIResponseLog.elapsed_ms))
        )
        avg_ai_ms = round(float(avg_ms_result.scalar() or 0), 0)

        # เฉลี่ยสิทธิ primary ต่อครั้ง — เฉพาะ source='primary' เพราะ
        # 'additional' มาจากการกด "ดูเพิ่มเติม" ซึ่งไม่ใช่ทุกครั้งที่จะมี
        avg_result = await session.execute(text("""
            SELECT AVG(cnt) FROM (
                SELECT inquiry_id, COUNT(*) AS cnt
                FROM benefit_match
                WHERE source = 'primary' AND inquiry_id IS NOT NULL
                GROUP BY inquiry_id
            ) per_inquiry
        """))
        avg_benefits = round(float(avg_result.scalar() or 0), 2)

        # สิทธิที่ระบบแนะนำบ่อย — join ตรงกับ benefits.name (ได้ชื่อล่าสุด
        # เสมอ) และกรองเฉพาะ likely_eligible เพราะป้าย "ได้รับบ่อยที่สุด"
        # ในหน้า dashboard ควรหมายถึงสิทธิที่ผ่านเกณฑ์จริง ไม่ใช่ทุกสถานะปนกัน
        top_result = await session.execute(text("""
            SELECT b.name AS benefit, COUNT(*) AS cnt
            FROM benefit_match bm
            JOIN benefits b ON b.id = bm.benefit_id
            WHERE bm.status = 'likely_eligible'
            GROUP BY b.name
            ORDER BY cnt DESC
            LIMIT 5
        """))
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