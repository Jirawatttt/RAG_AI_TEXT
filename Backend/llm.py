"""OpenAI generation and embedding client for the RAG workflow."""

import os
import logging
import json
from dotenv import load_dotenv
from openai import AsyncOpenAI
from models import Benefit

load_dotenv()
logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


async def _call_model(model: str, prompt: str) -> str:
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "ตอบตามข้อมูลที่ให้เท่านั้น และคืน JSON ที่ valid เสมอ"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=2048,
    )
    return response.choices[0].message.content or ""


async def embed_text(text: str) -> list[float]:
    """Create an embedding for a RAG query or document chunk."""
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


async def analyze_rights(user_text: str, benefits: list[Benefit]) -> dict:
    """Use only retrieved catalogue evidence to produce a user-facing analysis."""
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")

    benefit_context = []
    source_map = {}
    for index, benefit in enumerate(benefits, 1):
        source_id = f"S{index}"
        source_map[source_id] = benefit
        benefit_context.append(
            f"[{source_id}] {benefit.name}\n"
            f"URL: {benefit.link or 'ไม่มีลิงก์'}\n"
            f"เอกสารที่ต้องใช้: {', '.join(benefit.docs) or 'ไม่ระบุ'}\n"
            f"ติดต่อ: {', '.join(benefit.contact) or 'ไม่ระบุ'}\n"
            f"คำอธิบายสิทธิ: {benefit.short_description or 'ไม่ระบุ'}\n"
            f"ผลประโยชน์: {benefit.benefit_details or 'ไม่ระบุ'}"
        )

    prompt = f"""คุณเป็นผู้ช่วยระบบแสดงสิทธิประโยชน์ภาครัฐเบื้องต้น

ข้อความของผู้ใช้:
{user_text}

RAG ได้เปรียบเทียบข้อความผู้ใช้กับเอกสารในคลังความรู้แล้ว และคัดรายการที่เกี่ยวข้องมาให้ด้านล่าง
ให้ใช้ข้อมูลจากตาราง benefits ของแต่ละรายการเป็นข้อมูลหลักในการสรุปสำหรับผู้ใช้:
{chr(10).join(chr(10) + item for item in benefit_context)}

วิเคราะห์จากข้อความผู้ใช้และข้อมูล benefits ของรายการที่ RAG คัดเลือกเท่านั้น ห้ามใช้ความรู้ภายนอก ห้ามยืนยันว่าได้รับสิทธิจริง
จัดเฉพาะสิทธิที่อาจเกี่ยวข้องกับผู้ใช้ลงในสถานะใดสถานะหนึ่ง:
- likely_eligible: อาจเข้าเกณฑ์จากข้อมูลที่มี
- needs_verification: อาจเกี่ยวข้อง แต่ข้อมูลผู้ใช้หรือเงื่อนไขในหลักฐานยังไม่พอจะสรุปได้
- not_eligible: ข้อความผู้ใช้ระบุชัดเจนว่าไม่ตรงกับเงื่อนไขสำคัญที่หลักฐานกำหนดไว้ (เช่น หลักฐานกำหนดสัญชาติไทยแต่ผู้ใช้บอกว่าไม่ใช่คนไทย, หลักฐานกำหนดต้องเป็นผู้ประกันตนมาตรา 33 แต่ผู้ใช้บอกว่าไม่มีประกันสังคม)

กฎสำคัญเรื่องข้อความปฏิเสธ (negation): ถ้าผู้ใช้ระบุชัดเจนว่า "ไม่ใช่/ไม่มี/ไม่ได้" ในเงื่อนไขที่หลักฐานระบุว่าจำเป็นสำหรับสิทธินั้น ให้จัดเป็น not_eligible ทันที ห้ามจัดเป็น needs_verification เพียงเพราะข้อมูลอื่นยังไม่ครบ — ความไม่ครบของข้อมูลส่วนอื่นไม่ได้ทำให้เงื่อนไขที่ถูกปฏิเสธไปแล้วกลับมาเป็นไปได้อีก

ใน explanation ให้สรุปโดยเน้นคำอธิบายสิทธิและผลประโยชน์จากข้อมูล benefits แล้วเชื่อมกับข้อมูลของผู้ใช้
ถ้าข้อมูลไม่พอ ให้ใช้ needs_verification และระบุคำถามที่ต้องตรวจสอบ
คืน JSON เท่านั้นตาม schema นี้:
{{"summary":"สรุปภาษาไทย", "benefits":[{{"name":"ชื่อจากหลักฐาน", "status":"likely_eligible|needs_verification|not_eligible", "explanation":"เหตุผลภาษาไทย", "missing_information":["คำถามหรือข้อมูลที่ต้องตรวจ"], "source_ids":["S1"]}}], "follow_up_questions":["คำถามเพิ่มเติม"]}}"""

    raw = await _call_model(MODEL, prompt)
    data = json.loads(raw)
    normalized = []
    for item in data.get("benefits", []):
        ids = [source_id for source_id in item.get("source_ids", []) if source_id in source_map]
        sources = [
            {"title": source_map[source_id].name, "url": source_map[source_id].link,
             "docs": source_map[source_id].docs, "contact": source_map[source_id].contact,
             "short_description": source_map[source_id].short_description,
             "benefit_details": source_map[source_id].benefit_details}
            for source_id in ids
        ]
        if not sources:
            continue
        normalized.append({
            "name": item.get("name") or sources[0]["title"],
            "status": item.get("status", "needs_verification"),
            "explanation": item.get("explanation", "โปรดตรวจสอบกับหน่วยงานที่เกี่ยวข้อง"),
            "missing_information": item.get("missing_information", []),
            "sources": sources,
        })
    return {
        "summary": data.get("summary", "ผลการวิเคราะห์เบื้องต้นจากเอกสารที่ระบบค้นพบ"),
        "benefits": normalized,
        "follow_up_questions": data.get("follow_up_questions", []),
        "coverage_warning": "ฐานข้อมูลเอกสารสิทธิของระบบยังอยู่ระหว่างรวบรวม ผลลัพธ์นี้อาจยังไม่ครอบคลุมทุกสิทธิ โปรดตรวจสอบกับหน่วยงานเจ้าของสิทธิอีกครั้ง",
    }