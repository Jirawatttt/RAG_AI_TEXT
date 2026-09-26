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
# ORM models — ชื่อ table/field ตรงกับ data_table.md (ตารางที่ 3-1 ถึง 3-5)
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class InquiryLog(Base):
    """
    บันทึกทุกครั้งที่มีการตรวจสอบสิทธิ (1 แถว = 1 ครั้งที่กด /analyze-rights)

    ไม่เก็บ PII (ชื่อ, เลขบัตร, ที่อยู่)
    `Retrieval_quality` เก็บตัวเลขจาก scoring ของ rag.py (เช่น top_score,
    รายการ score ของผลลัพธ์ที่จัดอันดับ, และวิธีที่ใช้คำนวณ — embedding
    หรือ keyword fallback) เพื่อดูคุณภาพการ match โดยรวมของระบบย้อนหลังได้
    """
    __tablename__ = "Inquiry_log"

    Log_id             = Column(Integer, primary_key=True, autoincrement=True)
    Retrieval_quality  = Column(JSONB, nullable=False, default=dict) # ดู rag.retrieve_for_text() — {"method": ..., "top_score": ..., "scores": [...]}
    Created_at         = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,  # ใช้ ORDER BY / GROUP BY ตามเวลาแทบทุก query สถิติ
    )


class BenefitMatch(Base):
    """
    บันทึก 1 แถวต่อ 1 สิทธิที่ AI ประเมินให้ user ในแต่ละครั้ง 
    source แยกว่าสิทธินี้มาจาก flow หลัก (primary, ถูก LLM ประเมินตอน
    /analyze-rights) หรือมาจากตอนกด "ดูเพิ่มเติม" (additional, ประเมินตอน
    /analyze-more-rights) 
    """
    __tablename__ = "Benefit_match"

    Match_id   = Column(Integer, primary_key=True, autoincrement=True)
    Inquiry_id = Column(Integer, ForeignKey("Inquiry_log.Log_id", ondelete="CASCADE"), nullable=True, index=True)
    Benefit_id = Column(Integer, ForeignKey("Benefits.Benefit_id", ondelete="CASCADE"), nullable=False, index=True)
    Status     = Column(String(30), nullable=False)   # likely_eligible / needs_verification / not_eligible
    Source     = Column(String(20), nullable=False, default="primary")  # primary / additional


class AIResponseLog(Base):
    """
    บันทึก AI response ทุกครั้งที่เรียก LLM เพื่อสรุปคำตอบให้ user
    (ทั้งจาก /analyze-rights และ /analyze-more-rights)
    ใช้ monitor cost, latency และ debug prompt
    """
    __tablename__ = "Ai_response_log"

    Logai_id      = Column(Integer, primary_key=True, autoincrement=True)
    Inquiry_id    = Column(Integer, ForeignKey("Inquiry_log.Log_id", ondelete="CASCADE"), nullable=True, index=True)
    Ai_response   = Column(Text, nullable=False)
    Elapsed_ms    = Column(Integer, nullable=False)   # เวลาตอบ (ms)


# ── RAG knowledge base ──────────────────────────────────────────────────
# These records are the editable source of truth for benefits and retrieved
# evidence.  The initial catalogue is inserted only when empty.
class BenefitRecord(Base):
    __tablename__ = "Benefits"

    Benefit_id = Column(Integer, primary_key=True, autoincrement=True)
    Slug_benefit = Column(String(50), unique=True, nullable=False, index=True)
    Name_benefit = Column(String(100), nullable=False)
    Category_benefit = Column(String(50), nullable=False, index=True)
    Docs_benefit = Column(JSONB, nullable=False, default=list)
    Contact_benefit = Column(JSONB, nullable=False, default=list)
    Link_benefit = Column(String(255), nullable=False, default="")
    # LLM-only context; it is intentionally not embedded or used for RAG ranking.
    Short_description = Column(Text, nullable=False, default="")
    Benefit_details = Column(Text, nullable=False, default="")
    Disqualifying_conditions = Column(Text, nullable=False, default="")
    Checked_at = Column(DateTime(timezone=True), nullable=True)
    Active = Column(Boolean, nullable=False, default=True)


class BenefitDocument(Base):
    __tablename__ = "Benefit_embeddings"

    Embedding_id = Column(Integer, primary_key=True, autoincrement=True)
    Benefit_id = Column(Integer, ForeignKey("Benefits.Benefit_id", ondelete="CASCADE"), nullable=False, index=True)
    Title_benefit = Column(String(50), nullable=False)
    Content_benefit = Column(Text, nullable=False)
    Vector_benefit = Column(JSONB, nullable=True)


class CatalogueItem:
    """Lightweight read model used by rag.py; keeps ORM details out of routes."""
    def __init__(self, record, documents):
        self.name, self.docs, self.contact, self.link = (
            record.Name_benefit, record.Docs_benefit, record.Contact_benefit, record.Link_benefit
        )
        self.slug = record.Slug_benefit
        self.short_description = record.Short_description
        self.benefit_details = record.Benefit_details
        self.disqualifying_conditions = record.Disqualifying_conditions
        self.checked_at = record.Checked_at
        self.documents = documents


# ---------------------------------------------------------------------------
# Lifecycle — เรียกจาก main.py lifespan
# ---------------------------------------------------------------------------

async def connect():
    """สร้างตารางถ้ายังไม่มี (CREATE TABLE IF NOT EXISTS)"""
    async with engine.begin() as conn:

        # ── migration ใหม่: เปลี่ยนชื่อ table เดิม (lowercase) เป็นชื่อใหม่
        # ตาม data_table.md — ต้องทำ "ก่อน" create_all เสมอ ไม่งั้น table เก่า
        # ที่มีข้อมูลอยู่แล้วจะถูกทิ้งไว้เฉยๆ ในขณะที่ create_all ไปสร้าง
        # table ชื่อใหม่ว่างเปล่าซ้อนขึ้นมาอีกอัน — ใช้ "IF EXISTS" จึงปลอดภัย
        # กับ DB ที่ยังไม่เคยมี table เก่าเลยด้วย (จะข้ามไปเฉยๆ)
        await conn.execute(text('ALTER TABLE IF EXISTS inquiry_log RENAME TO "Inquiry_log"'))
        await conn.execute(text('ALTER TABLE IF EXISTS benefit_match RENAME TO "Benefit_match"'))
        await conn.execute(text('ALTER TABLE IF EXISTS ai_response_log RENAME TO "Ai_response_log"'))
        await conn.execute(text('ALTER TABLE IF EXISTS benefits RENAME TO "Benefits"'))
        await conn.execute(text('ALTER TABLE IF EXISTS benefit_documents RENAME TO "Benefit_embeddings"'))

        # create_all สร้างตารางใหม่ (เช่น "Benefit_match") แต่ไม่แก้ตารางเก่าที่มีอยู่แล้ว
        await conn.run_sync(Base.metadata.create_all)

        # ── migration ใหม่: เปลี่ยนชื่อ column เดิม (lowercase) เป็นชื่อใหม่
        # ให้ตรงกับ attribute ของ ORM model ด้านบน — ครอบด้วย DO block +
        # EXCEPTION เพราะ "ALTER TABLE ... RENAME COLUMN" ไม่รองรับ
        # "IF EXISTS" ตรงๆ (กัน error ทั้งกรณี table/column ยังไม่มีอยู่แล้ว
        # เพราะเพิ่งถูก create_all สร้างขึ้นมาด้วยชื่อใหม่)
        async def rename_column(table: str, old: str, new: str) -> None:
            await conn.execute(text(f'''
                DO $$
                BEGIN
                    ALTER TABLE "{table}" RENAME COLUMN {old} TO "{new}";
                EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;
                END $$;
            '''))

        for old, new in [
            ("id", "Log_id"), ("retrieval_quality", "Retrieval_quality"), ("created_at", "Created_at"),
        ]:
            await rename_column("Inquiry_log", old, new)

        for old, new in [
            ("id", "Match_id"), ("inquiry_id", "Inquiry_id"), ("benefit_id", "Benefit_id"),
            ("status", "Status"), ("source", "Source"),
        ]:
            await rename_column("Benefit_match", old, new)

        for old, new in [
            ("id", "Logai_id"), ("inquiry_id", "Inquiry_id"),
            ("ai_response", "Ai_response"), ("elapsed_ms", "Elapsed_ms"),
        ]:
            await rename_column("Ai_response_log", old, new)

        for old, new in [
            ("id", "Benefit_id"), ("slug", "Slug_benefit"), ("name", "Name_benefit"),
            ("category", "Category_benefit"), ("docs", "Docs_benefit"), ("contact", "Contact_benefit"),
            ("link", "Link_benefit"), ("short_description", "Short_description"),
            ("benefit_details", "Benefit_details"), ("disqualifying_conditions", "Disqualifying_conditions"),
            ("checked_at", "Checked_at"), ("active", "Active"),
        ]:
            await rename_column("Benefits", old, new)

        for old, new in [
            ("id", "Embedding_id"), ("benefit_id", "Benefit_id"),
            ("title", "Title_benefit"), ("content", "Content_benefit"), ("embedding", "Vector_benefit"),
        ]:
            await rename_column("Benefit_embeddings", old, new)

        # ── migration เดิม ──
        await conn.execute(text('ALTER TABLE "Benefit_embeddings" ADD COLUMN IF NOT EXISTS "Vector_benefit" JSONB'))
        await conn.execute(text('ALTER TABLE "Benefits" ADD COLUMN IF NOT EXISTS "Short_description" TEXT NOT NULL DEFAULT \'\''))
        await conn.execute(text('ALTER TABLE "Benefits" ADD COLUMN IF NOT EXISTS "Benefit_details" TEXT NOT NULL DEFAULT \'\''))
        await conn.execute(text('ALTER TABLE "Benefits" ADD COLUMN IF NOT EXISTS "Disqualifying_conditions" TEXT NOT NULL DEFAULT \'\''))
        # ── migration ใหม่: ตัด source_url ออก — table นี้ใช้แค่สำหรับ RAG
        # (content + embedding) เท่านั้น ส่วนลิงก์อ้างอิงใช้ Benefits.Link_benefit
        # ร่วมกันอยู่แล้ว (ดู seed_rag_catalogue เดิมที่ fallback ไป
        # item["link"] เสมอ ไม่เคยมี source_url แยกต่างหากจริง ๆ)
        await conn.execute(text('ALTER TABLE "Benefit_embeddings" DROP COLUMN IF EXISTS source_url'))

        # ── migration ใหม่: ย้าย checked_at จาก Benefit_embeddings (รายชิ้น
        # เอกสาร) ขึ้นไปเก็บที่ Benefits (รายสิทธิ) แทน เพราะแอดมินตรวจสอบ
        # เนื้อหาทีละสิทธิในทางปฏิบัติ ไม่ได้ตรวจทีละ chunk — คอลัมน์ active
        # ของ Benefit_embeddings ก็ตัดทิ้งเช่นกันเพราะซ้ำกับ Benefits.Active ──
        await conn.execute(text('ALTER TABLE "Benefits" ADD COLUMN IF NOT EXISTS "Checked_at" TIMESTAMPTZ'))
        await conn.execute(text('''
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'Benefit_embeddings' AND column_name = 'checked_at'
                ) THEN
                    -- backfill: เอาค่าที่เก่าที่สุดในบรรดา chunk ของสิทธินั้น
                    -- (ยังไม่เคยตรวจเลยดีกว่าคิดว่าตรวจแล้วทั้งที่บาง chunk ยังไม่ได้ตรวจ)
                    -- และไม่ overwrite ถ้า Benefits.Checked_at มีค่าอยู่แล้ว
                    UPDATE "Benefits" b
                    SET "Checked_at" = sub.min_checked_at
                    FROM (
                        SELECT "Benefit_id" AS benefit_id, MIN(checked_at) AS min_checked_at
                        FROM "Benefit_embeddings"
                        WHERE checked_at IS NOT NULL
                        GROUP BY "Benefit_id"
                    ) sub
                    WHERE b."Benefit_id" = sub.benefit_id AND b."Checked_at" IS NULL;
                END IF;
            END $$;
        '''))
        await conn.execute(text('ALTER TABLE "Benefit_embeddings" DROP COLUMN IF EXISTS checked_at'))
        await conn.execute(text('ALTER TABLE "Benefit_embeddings" DROP COLUMN IF EXISTS active'))

        # ── embedding invalidation trigger ──────────────────────────────
        # ปัญหาเดิม: save_document_embedding() เซ็ต embedding เฉพาะตอนที่ยัง
        # เป็น NULL เท่านั้น ถ้าแอดมินแก้ `Content_benefit` ของแถวที่มี
        # embedding อยู่แล้ว (เช่นแก้เกณฑ์รายได้ที่เปลี่ยนกฎ) embedding เดิม
        # จะค้างอยู่และถูกใช้ rank ต่อไปทั้งที่ไม่ตรงกับข้อความใหม่แล้ว —
        # เป็นบั๊กเงียบที่ทำให้ RAG จัดอันดับผิดโดยไม่มี error ใดๆ ให้เห็น
        #
        # แก้ที่ระดับ DB ด้วย trigger แทนที่จะพึ่ง application code ทุกจุดที่
        # แก้ content (รวมถึงตอนแก้ตรงผ่าน SQL/DB client โดยไม่ผ่าน backend
        # เลยด้วย) — ทุกครั้งที่ Content_benefit เปลี่ยน ให้ Vector_benefit
        # เป็น NULL ทันที รอบ retrieve_for_text() ถัดไปจะเห็น Vector_benefit
        # เป็น NULL แล้วสั่ง embed ใหม่ + save ให้เองตามโค้ดเดิมใน rag.py
        # อยู่แล้ว ไม่ต้องแก้ rag.py เพิ่ม
        await conn.execute(text('''
            CREATE OR REPLACE FUNCTION invalidate_benefit_document_embedding()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW."Content_benefit" IS DISTINCT FROM OLD."Content_benefit" THEN
                    NEW."Vector_benefit" := NULL;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        '''))
        await conn.execute(text(
            'DROP TRIGGER IF EXISTS trg_invalidate_benefit_document_embedding ON "Benefit_embeddings"'
        ))
        await conn.execute(text('''
            CREATE TRIGGER trg_invalidate_benefit_document_embedding
            BEFORE UPDATE ON "Benefit_embeddings"
            FOR EACH ROW
            EXECUTE FUNCTION invalidate_benefit_document_embedding()
        '''))

        # ── migration ใหม่: ย้าย benefits/total_matched ออกจาก Inquiry_log,
        #    ย้าย profile/benefits ออกจาก Ai_response_log แล้วผูก Inquiry_id
        #    แทน (ดูเหตุผลในคอมเมนต์ของแต่ละ model ด้านบน) ──
        await conn.execute(text('ALTER TABLE "Inquiry_log" DROP COLUMN IF EXISTS benefits'))
        await conn.execute(text('ALTER TABLE "Inquiry_log" DROP COLUMN IF EXISTS total_matched'))
        await conn.execute(text('ALTER TABLE "Ai_response_log" DROP COLUMN IF EXISTS profile'))
        await conn.execute(text('ALTER TABLE "Ai_response_log" DROP COLUMN IF EXISTS benefits'))
        await conn.execute(text(
            'ALTER TABLE "Ai_response_log" ADD COLUMN IF NOT EXISTS "Inquiry_id" '
            'INTEGER REFERENCES "Inquiry_log"("Log_id") ON DELETE CASCADE'
        ))

        # ── migration ใหม่: เปลี่ยน Inquiry_log.profile (เก็บแค่ text_length)
        #    เป็น Retrieval_quality (เก็บ scoring จาก rag.py แทน) ──
        await conn.execute(text(
            'ALTER TABLE "Inquiry_log" ADD COLUMN IF NOT EXISTS "Retrieval_quality" '
            "JSONB NOT NULL DEFAULT '{}'::jsonb"
        ))
        await conn.execute(text('ALTER TABLE "Inquiry_log" DROP COLUMN IF EXISTS profile'))

        # ── migration ใหม่: ตัด created_at ออกจาก Benefit_match/Ai_response_log
        #    เพราะเวลาดูได้จาก Inquiry_log.Created_at ผ่าน Inquiry_id (FK) แล้ว
        #    (การ DROP COLUMN นี้จะลบ index ที่ผูกกับคอลัมน์นี้ไปด้วยอัตโนมัติ) ──
        await conn.execute(text('ALTER TABLE "Benefit_match" DROP COLUMN IF EXISTS created_at'))
        await conn.execute(text('ALTER TABLE "Ai_response_log" DROP COLUMN IF EXISTS created_at'))

        # create_all ใส่ index ให้เฉพาะตารางที่เพิ่งสร้างใหม่ — ตารางเก่าที่มี
        # อยู่แล้วต้องสั่ง CREATE INDEX เองแยกต่างหาก
        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_inquiry_log_created_at ON "Inquiry_log" ("Created_at")'))
        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_ai_response_log_inquiry_id ON "Ai_response_log" ("Inquiry_id")'))
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
            # checked_at ตอนนี้อยู่ระดับสิทธิ (ไม่ใช่ระดับ document chunk แล้ว)
            # — ใช้ item["checked_at"] ถ้า catalogue ระบุไว้ตรง ๆ ไม่งั้น
            # fallback ไปเอาค่าที่เก่าที่สุดในบรรดา chunk เดิมของ catalogue
            # นี้ (เผื่อ catalogue เก่ายังระบุ checked_at ไว้ระดับ document)
            checked_at = item.get("checked_at")
            if checked_at is None:
                doc_checked_ats = [
                    doc["checked_at"] for doc in item["documents"] if doc.get("checked_at")
                ]
                checked_at = min(doc_checked_ats) if doc_checked_ats else None

            record = BenefitRecord(
                Slug_benefit=item["slug"], Name_benefit=item["name"], Category_benefit=item["category"],
                Docs_benefit=item["docs"], Contact_benefit=item["contact"], Link_benefit=item["link"],
                Short_description=item.get("short_description", ""),
                Benefit_details=item.get("benefit_details", ""),
                Disqualifying_conditions=item.get("disqualifying_conditions", ""),
                Checked_at=checked_at,
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
                    Benefit_id=record.Benefit_id,
                    Title_benefit=doc["title"],
                    Content_benefit=doc["content"],
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
                select(BenefitRecord).where(BenefitRecord.Slug_benefit == item["slug"])
            )).scalar_one_or_none()
            if record is None:
                continue
            if not (record.Short_description or "").strip():
                record.Short_description = description
            if not (record.Benefit_details or "").strip():
                record.Benefit_details = details
            if not (record.Disqualifying_conditions or "").strip():
                record.Disqualifying_conditions = disqualifying
        await session.commit()


async def load_rag_catalogue() -> list[CatalogueItem]:
    async with AsyncSessionLocal() as session:
        records = (await session.execute(
            select(BenefitRecord).where(BenefitRecord.Active.is_(True)).order_by(BenefitRecord.Benefit_id)
        )).scalars().all()
        if not records:
            return []
        ids = [record.Benefit_id for record in records]
        documents = (await session.execute(
            select(BenefitDocument).where(BenefitDocument.Benefit_id.in_(ids))
        )).scalars().all()
    by_benefit_documents = {record.Benefit_id: [] for record in records}
    for document in documents: by_benefit_documents[document.Benefit_id].append(document)
    return [
        CatalogueItem(record, by_benefit_documents[record.Benefit_id])
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
            select(BenefitRecord).where(BenefitRecord.Slug_benefit.in_(slugs), BenefitRecord.Active.is_(True))
        )).scalars().all()
        if not records:
            return []
        ids = [record.Benefit_id for record in records]
        documents = (await session.execute(
            select(BenefitDocument).where(BenefitDocument.Benefit_id.in_(ids))
        )).scalars().all()
    by_benefit_documents: dict[int, list] = {record.Benefit_id: [] for record in records}
    for document in documents:
        by_benefit_documents[document.Benefit_id].append(document)
    return [
        CatalogueItem(record, by_benefit_documents[record.Benefit_id])
        for record in records
    ]


async def save_document_embedding(document_id: int, embedding: list[float]) -> None:
    async with AsyncSessionLocal() as session:
        document = await session.get(BenefitDocument, document_id)
        if document is not None and document.Vector_benefit is None:
            document.Vector_benefit = embedding
            await session.commit()


async def mark_benefit_checked(benefit_id: int, checked_at: datetime | None = None) -> None:
    """Admin action: record that this benefit's content was just verified
    against current government criteria. Does NOT touch any document
    `content`/`embedding` — this is purely a "last verified" timestamp,
    now kept at the benefit level rather than per document chunk.
    """
    async with AsyncSessionLocal() as session:
        record = await session.get(BenefitRecord, benefit_id)
        if record is not None:
            record.Checked_at = checked_at or datetime.now(timezone.utc)
            await session.commit()


async def get_benefits_needing_review(stale_after_days: int = 180) -> list[dict]:
    """List active benefits that either have never been checked
    (`Checked_at IS NULL`, e.g. anything seeded without a real check date)
    or were last checked more than `stale_after_days` ago — for an admin
    screen/report, not used by the retrieval or analysis path.
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text('''
            SELECT b."Benefit_id" AS benefit_id, b."Name_benefit" AS benefit_name,
                   b."Link_benefit" AS link, b."Checked_at" AS checked_at
            FROM "Benefits" b
            WHERE b."Active" IS TRUE
              AND (b."Checked_at" IS NULL OR b."Checked_at" < now() - (:days || \' days\')::interval)
            ORDER BY b."Checked_at" ASC NULLS FIRST
        '''), {"days": stale_after_days})).all()
    return [
        {
            "benefit_id": row.benefit_id,
            "benefit_name": row.benefit_name,
            "checked_at": row.checked_at,
            "link": row.link,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

async def log_inquiry(retrieval_quality: dict) -> int:
    """
    บันทึก inquiry 1 ครั้ง (แถวเดียว ไม่มี benefits/total_matched แล้ว)
    เรียกจาก POST /analyze-rights ใน main.py

    retrieval_quality: ตัวเลข scoring จาก rag.retrieve_for_text() (เช่น
    top_score, scores ของผลลัพธ์ที่จัดอันดับ, และ method ที่ใช้) สำหรับดู
    คุณภาพการ match โดยรวมของระบบย้อนหลัง

    คืนค่า id ของแถวที่สร้าง เผื่ออนาคตอยากผูก inquiry_id ให้แม่นยำตอน
    /analyze-more-rights (ต้องส่ง id นี้กลับไปให้ client เก็บไว้ส่งมาด้วย)
    """
    async with AsyncSessionLocal() as session:
        row = InquiryLog(Retrieval_quality=retrieval_quality)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        logger.info(f"Inquiry logged — id={row.Log_id}")
        return row.Log_id


async def log_benefit_matches(
    hits: list[tuple[str, str]],
    source: str,
    inquiry_id: int | None = None,
) -> None:
    """
    บันทึกสิทธิที่ AI ประเมินให้ user 1 ครั้ง — แทนที่การยัดชื่อสิทธิลง JSONB
    เดิม ด้วยการ insert เป็นแถว ๆ ผูกกับ Benefits.Benefit_id จริง

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
            select(BenefitRecord.Benefit_id, BenefitRecord.Slug_benefit).where(BenefitRecord.Slug_benefit.in_(slugs))
        )).all()
        id_by_slug = {slug: benefit_id for benefit_id, slug in rows}

        matched = 0
        for slug, status in hits:
            benefit_id = id_by_slug.get(slug)
            if benefit_id is None:
                # slug ไม่พบใน Benefits (เช่นถูกลบไปแล้ว) — ข้ามแถวนี้แทนที่จะ error
                logger.warning(f"BenefitMatch skipped — unknown slug: {slug}")
                continue
            session.add(BenefitMatch(
                Inquiry_id=inquiry_id, Benefit_id=benefit_id, Status=status, Source=source,
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
        row = AIResponseLog(Inquiry_id=inquiry_id, Ai_response=ai_response, Elapsed_ms=elapsed_ms)
        session.add(row)
        await session.commit()
        logger.info(f"AI response logged — {elapsed_ms}ms")


# ---------------------------------------------------------------------------
# Read functions — ใช้ตอน GET /stats
# ---------------------------------------------------------------------------

async def get_recent_summaries(limit: int = 20) -> list[str]:
    """Recent AI-response summaries for the /stats/insight endpoint.

    Ai_response_log stores no PII by design (see InquiryLog docstring), so
    this is safe to hand to an LLM for a dashboard-facing summary.

    Ai_response_log ไม่มี created_at ของตัวเองแล้ว (เวลาดูได้จาก
    Inquiry_log.Created_at ผ่าน Inquiry_id แทน) แต่ Inquiry_id เป็น
    nullable — เรียงตาม Logai_id (autoincrement, insert ตามลำดับเวลาอยู่แล้ว)
    แทน จึงยังได้ "ล่าสุดก่อน" โดยไม่ต้อง join กับ Inquiry_log
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(AIResponseLog.Ai_response)
            .order_by(AIResponseLog.Logai_id.desc())
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
                              Ai_response_log ตารางเดียวกันแล้ว
    """
    async with AsyncSessionLocal() as session:

        # จำนวนครั้งที่ตรวจสอบทั้งหมด
        total_result = await session.execute(
            select(func.count()).select_from(InquiryLog)
        )
        total_inquiries = total_result.scalar() or 0

        # เวลาตอบ AI เฉลี่ย — รวมทุก endpoint ที่เรียก LLM
        avg_ms_result = await session.execute(
            select(func.avg(AIResponseLog.Elapsed_ms))
        )
        avg_ai_ms = round(float(avg_ms_result.scalar() or 0), 0)

        # เฉลี่ยสิทธิ primary ต่อครั้ง — เฉพาะ source='primary' เพราะ
        # 'additional' มาจากการกด "ดูเพิ่มเติม" ซึ่งไม่ใช่ทุกครั้งที่จะมี
        avg_result = await session.execute(text('''
            SELECT AVG(cnt) FROM (
                SELECT "Inquiry_id", COUNT(*) AS cnt
                FROM "Benefit_match"
                WHERE "Source" = \'primary\' AND "Inquiry_id" IS NOT NULL
                GROUP BY "Inquiry_id"
            ) per_inquiry
        '''))
        avg_benefits = round(float(avg_result.scalar() or 0), 2)

        # สิทธิที่ระบบแนะนำบ่อย — join ตรงกับ Benefits.Name_benefit (ได้ชื่อ
        # ล่าสุดเสมอ) และกรองเฉพาะ likely_eligible เพราะป้าย "ได้รับบ่อยที่สุด"
        # ในหน้า dashboard ควรหมายถึงสิทธิที่ผ่านเกณฑ์จริง ไม่ใช่ทุกสถานะปนกัน
        top_result = await session.execute(text('''
            SELECT b."Name_benefit" AS benefit, COUNT(*) AS cnt
            FROM "Benefit_match" bm
            JOIN "Benefits" b ON b."Benefit_id" = bm."Benefit_id"
            WHERE bm."Status" = \'likely_eligible\'
            GROUP BY b."Name_benefit"
            ORDER BY cnt DESC
            LIMIT 5
        '''))
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