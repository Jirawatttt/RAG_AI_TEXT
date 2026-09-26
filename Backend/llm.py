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


# เรียก OpenAI chat completion แบบบังคับ JSON output ใช้ร่วมกันทุกฟังก์ชันด้านล่าง
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


# แปลงข้อความเป็นเวกเตอร์ embedding เพื่อใช้คำนวณ cosine similarity ใน rag.py
async def embed_text(text: str) -> list[float]:
    """Create an embedding for a RAG query or document chunk."""
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


# เรียก LLM 1 ครั้ง คืนทั้ง in_scope (อยู่ในโดเมนสิทธิหรือไม่) และ query ที่ rewrite แล้ว
async def analyze_query(user_text: str) -> dict:
    """One LLM call that both scope-checks the input and distills it into a
    short search query — replaces two separate calls (a scope classifier and
    a query rewriter) with one, since both tasks read the same input text and
    just produce independent outputs. Used by the main /analyze-rights path.

    Raises on failure (no API key, bad JSON) — callers fall back to the
    keyword check + raw text, same as the two-call version did.
    """
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")
    prompt = f"""ทำ 2 อย่างจากข้อความผู้ใช้ด้านล่างพร้อมกัน:

1. ตัดสินว่าข้อความนี้เกี่ยวข้องกับสิทธิประโยชน์ภาครัฐไทยหรือไม่ (เช่น เบี้ยยังชีพ สวัสดิการ ประกันสังคม บัตรสวัสดิการแห่งรัฐ สิทธิรักษาพยาบาล เงินอุดหนุนเด็ก) หรือกล่าวถึงสถานะส่วนตัวที่อาจเชื่อมโยงกับสิทธิเหล่านี้ (อายุ สัญชาติ การมีงานทำ รายได้ ครอบครัว ความพิการ การมี/ไม่มีประกันสังคม) แม้จะไม่ได้ใช้คำว่า "สิทธิ" หรือ "สวัสดิการ" ตรงๆ ก็ตาม
2. ถ้าเกี่ยวข้อง (in_scope = true) ให้แปลงข้อความเป็นข้อความค้นหาสั้นๆ เน้นเฉพาะข้อเท็จจริงเชิงสถานะ (อายุ สัญชาติ อาชีพ ประกันสังคม รายได้ ความพิการ บุตร ทะเบียนบ้าน ฯลฯ) ตัดคำฟุ่มเฟือย น้ำเสียง หรืออารมณ์ออก ห้ามเพิ่มข้อมูลที่ผู้ใช้ไม่ได้พูด ถ้าไม่เกี่ยวข้อง (in_scope = false) ให้ใส่ query เป็นค่าว่าง

ข้อความผู้ใช้: "{user_text}"

คืน JSON เท่านั้นตาม schema นี้: {{"in_scope": true หรือ false, "query": "ข้อความค้นหาที่กระชับ หรือค่าว่างถ้า in_scope เป็น false"}}"""
    raw = await _call_model(MODEL, prompt)
    return json.loads(raw)


# ตัดข้อความผู้ใช้ให้เหลือแค่ข้อเท็จจริงเชิงสถานะ ก่อนนำไป embed (ล้มเหลวได้ ไม่ raise)
async def rewrite_query(user_text: str) -> str:
    """Distill free-form user text into a short, fact-only query before it is
    embedded for retrieval.

    Never raises: on any failure (no API key, API error, bad JSON) it falls
    back to the original text, so retrieval quality can only improve or stay
    the same relative to embedding the raw text directly.
    """
    if client is None:
        return user_text
    prompt = f"""แปลงข้อความของผู้ใช้ด้านล่างให้เป็นข้อความค้นหาสั้นๆ เน้นเฉพาะข้อเท็จจริงเชิงสถานะที่เกี่ยวกับสิทธิประโยชน์ภาครัฐ (อายุ สัญชาติ อาชีพ ประกันสังคม รายได้ ความพิการ บุตร ทะเบียนบ้าน ฯลฯ) ตัดคำฟุ่มเฟือย น้ำเสียง หรืออารมณ์ออก ห้ามเพิ่มข้อมูลที่ผู้ใช้ไม่ได้พูด ห้ามตอบคำถามหรือวิเคราะห์สิทธิ

ข้อความผู้ใช้: "{user_text}"

คืน JSON เท่านั้นตาม schema นี้: {{"query": "ข้อความค้นหาที่กระชับ"}}"""
    try:
        raw = await _call_model(MODEL, prompt)
        query = (json.loads(raw).get("query") or "").strip()
        return query or user_text
    except Exception:
        logger.warning("Query rewrite failed; falling back to the raw query text")
        return user_text


# แนะนำ field เดียวที่ user ควรเพิ่ม เพื่อช่วยให้จัดสถานะสิทธิได้แม่นขึ้น (ไม่ตัดสินสิทธิ)
async def suggest_input_fields(user_text: str, benefits: list[Benefit]) -> dict:
    """AI Input Assistant — แนะนำ field เดียวที่ user อาจเพิ่มเพื่อช่วยให้ RAG
    จับคู่สิทธิแม่นขึ้น และช่วยให้ analyze_rights() จัดสถานะ (likely_eligible/
    needs_verification/not_eligible) ได้แม่นขึ้น แทนที่จะค้างที่
    needs_verification เพราะข้อมูลไม่พอ

    ตั้งใจแยกหน้าที่จาก analyze_rights() อย่างเด็ดขาด:
    - ใช้เฉพาะ short_description/benefit_details เป็น context (เกณฑ์
      คุณสมบัติทั่วไป) ไม่ใช้ disqualifying_conditions เลย เพราะ field นั้น
      มีไว้ตัดสิน not_eligible ไม่ใช่แนะนำกรอกข้อมูล — ถ้าปนกันจะเสี่ยงให้ AI
      หลุดไปพูดเรื่อง "อาจไม่ได้สิทธิเพราะ..." ซึ่งผิดขอบเขตของ feature นี้
    - ไม่ตัดสิน/ยืนยันสิทธิ ไม่บังคับกรอก แนะนำได้ทีละ 1 ประเด็นเท่านั้น

    เรียกจาก /suggest-input ใน main.py เท่านั้น ไม่เกี่ยวกับ /analyze-rights
    """
    if client is None:
        raise RuntimeError("ไม่พบ OPENAI_API_KEY ใน .env")

    if not benefits:
        return {"status": "sufficient", "suggestion": ""}

    benefit_context = "\n".join(
        f"- {benefit.name}\n"
        f"  คำอธิบายสิทธิ: {benefit.short_description or 'ไม่ระบุ'}\n"
        f"  ผลประโยชน์: {benefit.benefit_details or 'ไม่ระบุ'}"
        for benefit in benefits
    )

    prompt = f"""คุณคือ AI Assistant ที่ช่วยแนะนำผู้ใช้เติมข้อมูลก่อนตรวจสอบสิทธิประโยชน์ภาครัฐ
คำแนะนำของคุณมีเป้าหมายเดียว: ช่วยให้ข้อความของผู้ใช้มีรายละเอียดพอที่ระบบค้นหา (RAG)
จับคู่สิทธิได้แม่นขึ้น และช่วยให้การจัดสถานะ (likely_eligible / needs_verification / not_eligible)
ทำได้แม่นขึ้น แทนที่จะค้างอยู่ที่ "ข้อมูลไม่พอ"

คำแนะนำของคุณเป็นทางเลือก ไม่ใช่การบังคับ และไม่ใช่การตัดสิน/ยืนยันสิทธิ
- ห้ามใช้คำสั่ง/คำบังคับเด็ดขาด เช่น "โปรดระบุ", "กรุณากรอก", "ต้องระบุ", "จำเป็นต้องบอก"
- ให้ใช้โทนชักชวน/เสนอทางเลือกเบาๆ เหมือนเพื่อนแนะนำ ไม่ใช่แบบฟอร์มราชการ
- เริ่มประโยคด้วยคำทำนองนี้แทน: "ลองเพิ่ม...", "ถ้าสะดวก อาจบอกเพิ่มได้ว่า...","จะช่วยให้แม่นขึ้นถ้าบอกว่า..."

ตัวอย่างที่ไม่ดี (ห้ามใช้):
- "โปรดระบุสถานะการมีบัตรผู้พิการ"
- "กรุณากรอกรายได้ต่อเดือน"

ข้อความของผู้ใช้ปัจจุบัน:
"{user_text}"

ข้อมูลสิทธิที่ RAG พบว่าเกี่ยวข้องกับข้อความนี้:
{benefit_context}

พิจารณาว่า field ไหน "ขาดหายไป" จากข้อความผู้ใช้ ที่ตรงกับเกณฑ์คุณสมบัติของสิทธิเหล่านี้
แล้วเลือกแนะนำ field เดียวที่สำคัญที่สุด (ที่จะช่วยให้การจัดสถานะชัดที่สุด)

ห้ามเดาข้อมูลที่ผู้ใช้ไม่ได้พูด ห้ามตัดสิน/ยืนยันว่าผู้ใช้มีหรือไม่มีสิทธิ แนะนำได้ทีละ 1 ประเด็นเท่านั้น
ถ้าข้อมูลที่มีเพียงพอต่อการจัดสถานะแล้ว ให้ตอบว่าเพียงพอ ห้ามหาเรื่องแนะนำเพิ่มโดยไม่มีเหตุผล

คืน JSON เท่านั้นตาม schema นี้: {{"status": "sufficient หรือ can_add", "suggestion": "ข้อความแนะนำสั้นๆ ภาษาไทย หรือค่าว่างถ้า sufficient"}}"""

    raw = await _call_model(MODEL, prompt)
    data = json.loads(raw)
    status = data.get("status") if data.get("status") in ("sufficient", "can_add") else "sufficient"
    suggestion = (data.get("suggestion") or "").strip() if status == "can_add" else ""
    return {"status": status, "suggestion": suggestion}


# ใช้เฉพาะหลักฐานที่ RAG คัดมาให้ ตัดสินสถานะสิทธิแต่ละอัน (likely/needs/not_eligible)
# แล้ว normalize source_ids กลับเป็น sources เต็มรูปแบบ (title/link/slug ฯลฯ)
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
            f"เอกสารที่ต้องใช้: {', '.join(benefit.docs) or 'ไม่ระบุ'}\n"
            f"ติดต่อ: {', '.join(benefit.contact) or 'ไม่ระบุ'}\n"
            f"คำอธิบายสิทธิ: {benefit.short_description or 'ไม่ระบุ'}\n"
            f"ผลประโยชน์: {benefit.benefit_details or 'ไม่ระบุ'}\n"
            f"เกณฑ์คุณสมบัติ/รายได้ (จากเอกสารหลักฐาน): {benefit.detail or 'ไม่ระบุ'}\n"
            f"เงื่อนไขที่ทำให้ไม่ได้รับสิทธิ (ตัดสิทธิ/สิ้นสุดสิทธิ): {benefit.disqualifying_conditions or 'ไม่ระบุ'}"
        )

    prompt = f"""คุณเป็นผู้ช่วยระบบแสดงสิทธิประโยชน์ภาครัฐเบื้องต้น

ข้อความของผู้ใช้:
{user_text}

RAG ได้เปรียบเทียบข้อความผู้ใช้กับเอกสารในคลังความรู้แล้ว และคัดรายการที่เกี่ยวข้องมาให้ด้านล่าง
ให้ใช้ข้อมูลจากตาราง benefits ของแต่ละรายการเป็นข้อมูลหลักในการสรุปสำหรับผู้ใช้ โดยแยกใช้สองส่วนนี้เป็นแหล่งเงื่อนไขที่ละเอียดและน่าเชื่อถือที่สุดสำหรับการตัดสิน status และการสร้าง missing_information:
- "เกณฑ์คุณสมบัติ/รายได้ (จากเอกสารหลักฐาน)" ใช้ตัดสินว่าผู้ใช้ "อาจเข้าเกณฑ์" (likely_eligible) หรือ "ข้อมูลยังไม่พอ" (needs_verification)
- "เงื่อนไขที่ทำให้ไม่ได้รับสิทธิ (ตัดสิทธิ/สิ้นสุดสิทธิ)" ใช้ตัดสิน not_eligible โดยตรงถ้าสถานะของผู้ใช้เข้าข่ายเงื่อนไขนี้ข้อใดข้อหนึ่ง แม้ผู้ใช้จะผ่านเกณฑ์คุณสมบัติ/รายได้ข้างต้นก็ตาม
{chr(10).join(chr(10) + item for item in benefit_context)}

วิเคราะห์จากข้อความผู้ใช้และข้อมูล benefits ของรายการที่ RAG คัดเลือกเท่านั้น ห้ามใช้ความรู้ภายนอก ห้ามยืนยันว่าได้รับสิทธิจริง
จัดเฉพาะสิทธิที่อาจเกี่ยวข้องกับผู้ใช้ลงในสถานะใดสถานะหนึ่ง:
- likely_eligible: อาจเข้าเกณฑ์คุณสมบัติ/รายได้จากข้อมูลที่มี และไม่เข้าข่ายเงื่อนไขตัดสิทธิใดๆ
- needs_verification: อาจเกี่ยวข้อง แต่ข้อมูลผู้ใช้หรือเกณฑ์คุณสมบัติ/รายได้ในหลักฐานยังไม่พอจะสรุปได้ และไม่มีข้อมูลบ่งชี้ว่าเข้าข่ายเงื่อนไขตัดสิทธิ
- not_eligible: ข้อความผู้ใช้ระบุชัดเจนว่าไม่ตรงกับเกณฑ์คุณสมบัติ/รายได้สำคัญที่หลักฐานกำหนดไว้ (เช่น หลักฐานกำหนดสัญชาติไทยแต่ผู้ใช้บอกว่าไม่ใช่คนไทย) หรือระบุชัดเจนว่าเข้าข่าย "เงื่อนไขที่ทำให้ไม่ได้รับสิทธิ" ข้อใดข้อหนึ่งของสิทธินั้น (เช่น เงื่อนไขระบุห้ามเป็นข้าราชการแต่ผู้ใช้บอกว่าเป็นข้าราชการ)

กฎสำคัญเรื่องข้อความปฏิเสธ (negation): ถ้าผู้ใช้ระบุชัดเจนว่า "ไม่ใช่/ไม่มี/ไม่ได้" ในเกณฑ์คุณสมบัติ/รายได้ที่หลักฐานระบุว่าจำเป็น หรือระบุชัดเจนว่าสถานะของตนตรงกับ "เงื่อนไขที่ทำให้ไม่ได้รับสิทธิ" ข้อใดข้อหนึ่ง ให้จัดเป็น not_eligible ทันที ห้ามจัดเป็น needs_verification เพียงเพราะข้อมูลอื่นยังไม่ครบ — ความไม่ครบของข้อมูลส่วนอื่นไม่ได้ทำให้เงื่อนไขที่ถูกปฏิเสธหรือเงื่อนไขตัดสิทธิที่เข้าข่ายไปแล้วกลับมาเป็นไปได้อีก

ใน explanation ให้สรุปโดยเน้นคำอธิบายสิทธิและผลประโยชน์จากข้อมูล benefits แล้วเชื่อมกับข้อมูลของผู้ใช้
ถ้าข้อมูลไม่พอ ให้ใช้ needs_verification และระบุคำถามที่ต้องตรวจสอบ
คืน JSON เท่านั้นตาม schema นี้:
{{"summary":"สรุปภาษาไทย", "benefits":[{{"name":"ชื่อจากหลักฐาน", "status":"likely_eligible|needs_verification|not_eligible", "explanation":"เหตุผลภาษาไทย", "missing_information":["คำถามหรือข้อมูลที่ต้องตรวจ"], "source_ids":["S1"]}}], "follow_up_questions":["คำถามเพิ่มเติม"]}}"""

    raw = await _call_model(MODEL, prompt)
    data = json.loads(raw)
    normalized = []
    # แปลง source_ids (เช่น "S1") กลับเป็นข้อมูลสิทธิเต็มจาก source_map
    # ถ้าไม่มี source ที่ map ได้เลย ให้ข้าม item นี้ทิ้ง (กันข้อมูลหลอน/ไม่มีหลักฐาน)
    for item in data.get("benefits", []):
        ids = [source_id for source_id in item.get("source_ids", []) if source_id in source_map]
        sources = [
            {"title": source_map[source_id].name, "url": source_map[source_id].link,
             "docs": source_map[source_id].docs, "contact": source_map[source_id].contact,
             "short_description": source_map[source_id].short_description,
             "benefit_details": source_map[source_id].benefit_details,
             # slug ไม่ได้ถูกใช้ฝั่งหน้าเว็บ (SourceOut ตั้งเป็น optional) แต่
             # main.py ใช้ผูกกลับไปที่ benefits.id ตอน log BenefitMatch —
             # ไม่งั้นจะรู้ได้แค่ "name" ซึ่งไม่พอจะหา benefit_id ที่แม่นยำ
             "slug": source_map[source_id].slug}
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