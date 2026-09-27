# Smart Rights Viewer — Backend

Backend สำหรับเว็บแอปวิเคราะห์ **สิทธิประโยชน์ภาครัฐเบื้องต้น** จากข้อความภาษาไทยแบบอิสระ (อายุ, สถานะงาน, ประกันสังคม, รายได้, สัญชาติ ฯลฯ)

ระบบใช้ **RAG + LLM**: ค้นหลักฐานที่เกี่ยวข้องจากคลังความรู้ก่อน แล้วให้ LLM เป็นคนตัดสิน ทุกจุดที่ระบบ "ฉลาด" มาจาก prompt engineering และความเก่งของ "โมเดล" ที่เลือกมาใช้ ไม่มี fine-tuning หรือ rule engine จึงไม่ deterministic 100%

> **ผลลัพธ์เป็นข้อมูลคัดกรองเบื้องต้น ไม่ใช่การรับรองสิทธิ หน่วยงานเจ้าของสิทธิเท่านั้นที่ยืนยันผลจริงได้
---
## ความสามารถหลัก
- รับข้อความภาษาไทยอิสระ 1–4,000 ตัวอักษร (รองรับข้อความติดกันไม่เว้นวรรค)
- Scope-check ด้วย AI ก่อนเสมอ (มี keyword fallback ถ้า AI ล่ม)
- คลังความรู้ปัจจุบัน 8 สิทธิ — วิเคราะห์ให้ทันที 3 อันดับแรก ที่เหลือกดดูเพิ่มได้โดยไม่ต้องค้นใหม่
- แดชบอร์ด กราฟแท่ง+วงกลมจากตัวเลขดิบ จากสถิติการใช้งานในระบบ
- **AI Input Assistant** แนะนำเพิ่มระหว่างพิมพ์ (debounce 1.2 วิ)
- จำกัด 20 requests/IP/60 วินาที ทุก endpoint ที่เรียก AI
---
## Flow หลัก (`/analyze-rights`)

```
![/analyze-rights](/assets/UML/UML-Activity%20_analyze-rights.png)
```

รวม 1 รอบ = 2 completions + 1 embedding
---
## Flow (`/analyze-more-rights`)
```
![/analyze-more-rights](/assets/UML/UML-Activity%20_analyze-more-rights.png)
```
**`/analyze-more-rights`**: ดึงสิทธิที่ถูกเลื่อนไว้ตรงๆ ด้วย slug จาก DB (ไม่ embed, ไม่เช็ค scope ซ้ำ) แล้วใช้ logic `llm.analyze_rights` วิเคราะห์อีกรอบเดียว
---
## Flow (`/suggest-input`)
```
![/suggest-input](/assets/UML/UML-Activity%20_suggest-input.png)
```
**`/suggest-input`**: cache ในหน่วยความจำตาม hash(text) (15 นาที) → ถ้าไม่ผ่าน scope หรือไม่มีสิทธิเกี่ยวข้องเลย ตอบ `sufficient`ไม่แสดงแนะนำอะไรให้Userทันที → ถ้ามีใช้ Logic `llm.suggest_input_fields` และเลือกแนะนำUser จาก field เดียวในDatabase (ใช้แค่ short_description/benefit_details ใน Table Benefits เป็น context)
---

## RAG ทำงานอย่างไร (สรุป)

1. Normalize ข้อความ → เช็ค scope ด้วย AI 
2. Embed Input User แล้วเทียบ cosine similarity กับเอกสารทุกสิทธิ เลือกหลักฐานสูงสุด 2 ฉบับ/สิทธิ
3. `SCOPE_FLOOR` เช็คแค่คะแนนสูงสุดของทั้ง query ครั้งเดียว
4. ส่งแค่ `RAG_LLM_RESULT_LIMIT` อันดับแรกให้ LLM วิเคราะห์ ที่เหลือรอกด "ดูเพิ่มเติม"

| สถานะ | ความหมาย |
| --- | --- |
| `likely_eligible` | ข้อมูลผู้ใช้เข้าเกณฑ์คุณสมบัติ และไม่เข้าข่ายเงื่อนไขตัดสิทธิ |
| `needs_verification` | อาจเกี่ยวข้อง แต่ข้อมูลยังไม่พอสรุป |
| `not_eligible` | ปฏิเสธเกณฑ์คุณสมบัติชัดเจน |

**ข้อจำกัดสำคัญ**: cosine similarity ไม่เข้าใจการปฏิเสธ — "ไม่ใช่คนไทย" อาจคะแนนใกล้เคียงเอกสารเรื่องสัญชาติพอๆ กับ "เป็นคนไทย" เพราะแชร์คำเดียวกัน การตัดสิน ที่ถูกต้องจึงต้องพึ่ง LLM + เอกสารที่ระบุเงื่อนไขชัดเจนเท่านั้น

## Logic การตัดสินใจของระบบ

ระบบมี "จุดตัดสินใจ" หลักๆ 4 จุด แต่ละจุดทำหน้าที่เดียวไม่ก้าวก่ายกัน:

**1) Scope check — "ข้อความนี้เกี่ยวกับสิทธิภาครัฐไหม"**
- ตัดสินโดย AI ตรงๆ (`llm.analyze_query`) 
- AI เรียกไม่ได้ (ไม่มี API key / network error) → fallback เป็น keyword list (`is_rights_query`) แทน
- `in_scope=false` → ตัดจบทันที

**2) Retrieval floor — "มีสิทธิไหนในคลังใกล้เคียงพอไหม"**
- เช็คแค่ **คะแนนสูงสุด** ของทั้ง query เทียบกับ `SCOPE_FLOOR` ครั้งเดียว
- ถ้าแม้แต่สิทธิที่ใกล้เคียงที่สุดยังคะแนนต่ำกว่าเกณฑ์ → ถือว่าข้อมูลไม่พอ/ไม่เกี่ยวข้อง คืนลิสต์ว่างโดยไม่ส่งให้ LLM เลย
- ผ่านเกณฑ์แล้ว **ทุกสิทธิที่จัดอันดับได้** จะถูกส่งต่อ (การจำกัดจำนวนเป็นหน้าที่ของจุดถัดไป)

**3) Status decision — "สิทธินี้ user น่าจะได้ไหม" (จุดที่ซับซ้อนที่สุด)**
เกณฑ์ที่ LLM ใช้ตัดสิน เรียงตามลำดับความสำคัญ:
1. เข้าข่ายข้อมูลใน field `disqualifying_conditions` ข้อใดข้อหนึ่ง หรือปฏิเสธเกณฑ์คุณสมบัติที่จำเป็นชัดเจน (เช่น "ไม่ใช่คนไทย") → **`not_eligible` ทันที** 
2. ไม่เข้าข่ายข้อ 1 และข้อมูลที่มีเข้าเกณฑ์คุณสมบัติครบ → `likely_eligible`
3. ไม่เข้าข่ายข้อ 1 แต่ข้อมูลยังไม่พอสรุป → `needs_verification` (พร้อม `missing_information`)
4. สิทธิที่ LLM เห็นว่า "ไม่เกี่ยวข้องเลย" **ไม่ถูกพูดถึงในผลลัพธ์เลย**

หลัง LLM ตอบกลับยังเช็คซ้ำอีกชั้น: `source_ids` ที่ LLM อ้างต้องตรงกับหลักฐานจริงที่ส่งไป ถ้าไม่ตรง (LLM หลอน) → ตัดรายการนั้นทิ้งทั้งอันแบบเงียบๆ ไม่แสดงให้ user เห็น

**4) Field suggestion — "ควรแนะนำ user เพิ่มอะไร" (AI Input Assistant)**
- ใช้ context แค่ `short_description`/`benefit_details`
- แนะนำได้ทีละ 1 field ที่สำคัญที่สุดเท่านั้น ห้ามใช้โทนคำสั่ง/บังคับ
- ข้อมูลพอแล้ว → ตอบ `sufficient` เฉยๆ ห้ามหาเรื่องแนะนำเพิ่มโดยไม่มีเหตุผล

## ข้อจำกัดที่ควรรู้

- ยังไม่มีชุดทดสอบที่เฉลยโดยผู้เชี่ยวชาญ — ห้ามอ้างเปอร์เซ็นต์ความแม่นยำ
- ครอบคลุมเฉพาะ 8 สิทธิในคลัง; เกณฑ์ราชการเปลี่ยนบ่อยต้องทบทวนเป็นระยะ
- LLM ไม่ deterministic 100% — ข้อความเดียวกันอาจได้คำตอบต่างกันเล็กน้อยคนละรอบ
- Rate limit และ cache ของ `/suggest-input` อยู่ใน memory — ไม่แชร์ข้าม worker และหายเมื่อ restart
- ห้ามส่งเลขบัตรประชาชน รหัสผ่าน หรือข้อมูลอ่อนไหวเกินจำเป็น

## API

Swagger UI: `http://127.0.0.1:8000/docs`

| Endpoint | หน้าที่ |
| --- | --- |
| `GET /health` | `{ "status": "ok", "version": "1.0.0" }` |
| `POST /analyze-rights` | flow หลัก — วิเคราะห์สิทธิจากข้อความ |
| `POST /analyze-more-rights` | วิเคราะห์สิทธิที่ถูกเลื่อนไว้ (`additional_benefits`) ด้วย `benefit_slugs` |
| `POST /suggest-input` | AI Input Assistant — แนะนำที่ควรเพิ่ม |
| `GET /stats` | สถิติดิบ: `total_inquiries`, `avg_ai_response_sec`, `top_benefits`, `category_breakdown` |

| HTTP | กรณี |
| --- | --- |
| `422` | validation ไม่ผ่าน / slug ไม่พบ |
| `429` | เกิน rate limit |
| `503` | ไม่มี `OPENAI_API_KEY` |
| `502` | RAG/AI วิเคราะห์ไม่สำเร็จ |
| `500` | ดึงสถิติไม่สำเร็จ |

## ฐานข้อมูล

PostgreSQL ผ่าน SQLAlchemy async

| ตาราง | หน้าที่ |
| --- | --- |
| `"Benefits"` | สิทธิหลัก: slug, ชื่อ, หมวด, เอกสาร, ติดต่อ, `Short_description`, `Benefit_details`, `Disqualifying_conditions`, `Checked_at`, `Active` |
| `"Benefit_embeddings"` | หลักฐาน RAG: `Content_benefit` (ใช้ทั้ง embed และส่งให้ LLM), `Vector_benefit` |
| `"Inquiry_log"` | 1 แถว/1 ครั้งที่กด `/analyze-rights` |
| `"Benefit_match"` | 1 แถว/1 สิทธิที่ AI ประเมิน (`Status`, `Source` = primary/additional) |
| `"Ai_response_log"` | คำตอบดิบของ LLM + เวลาตอบ — `/suggest-input` ไม่ log ลงตารางนี้ |

**สำคัญ**: แก้ `Content_benefit` แล้ว **ไม่ต้อง**ตั้ง `Vector_benefit = NULL` เอง — มี database trigger เคลียร์ให้อัตโนมัติ

**เขียนคลังความรู้**: เกณฑ์คุณสมบัติทั่วไปเขียนใน `Short_description`/`Benefit_details`/เอกสาร RAG ส่วน**เงื่อนไขตัดสิทธิ**เขียนแยกใน `Disqualifying_conditions` โดยเฉพาะ — ต้องเขียนเงื่อนไขสำคัญ (โดยเฉพาะสัญชาติ) ให้ชัดตรงๆ ไม่งั้น LLM ไม่มีหลักฐานตัดสิน `not_eligible`

Seed ข้อมูลจาก `rag_catalog.py` ทำครั้งเดียวตอน DB ว่าง แก้ไฟล์ทีหลังไม่มีผลกับ DB ที่ seed ไปแล้ว (ยกเว้นแถวที่ยังว่าง จะถูกเติมให้ทุก startup)

## โครงสร้าง

```text
Backend/
├── main.py         # routes, schemas, CORS, rate limiting, suggest-input cache
├── rag.py          # retrieval, cosine similarity, scope-floor, prepare_query()
├── llm.py          # OpenAI calls + prompt ทั้งหมด (analyze_query, analyze_rights, suggest_input_fields)
├── database.py     # models, migration, seed, analytics
├── rag_catalog.py  # ข้อมูลเริ่มต้น (seed ครั้งเดียว)
├── models.py       # domain model เดียว (Benefit)
└── run.py          # dev server

Frontend/
├── home.html / input.html / result.html
├── app.js          # API_BASE
├── input.js        # submit, dashboard, AI Input Assistant
└── result.js       # เรียก /analyze-rights, /analyze-more-rights, render ผล
```

## การติดตั้ง

```sql
CREATE DATABASE rights_db;
```

`Backend/.env`:

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/rights_db
APIKEY=sk-...
MODEL=...
EMBEDDING_MODEL=...
ALLOWED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

RAG_SCOPE_FLOOR=0.25
RAG_SCOPE_FLOOR_KEYWORD=0.08
RAG_DISCOVERY_RESULT_LIMIT=10
RAG_LLM_RESULT_LIMIT=3
SUGGEST_INPUT_CACHE_SECONDS=900
```

```powershell
cd Backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

เปิด Frontend ผ่าน Live Server → `http://localhost:5500/home.html`

## แนวทางพัฒนาต่อ

- เปิด endpoint แอดมินให้ `mark_benefit_checked()` / `get_benefits_needing_review()` ใช้งานได้จริง
- เพิ่ม field เหตุผล (`reason`) ใน scope-check เพื่อแยก "นอกเรื่อง" กับ "ข้อความกำกวม"
- ย้าย cache/rate limit ไป Redis ก่อน deploy หลาย instance
- เพิ่ม automated evaluation (retrieval, negation/disqualifying_conditions, scope accuracy)
- ใช้ `pgvector` เมื่อคลังโตขึ้นมาก
- เพิ่มการ redaction ข้อมูลอ่อนไหวก่อนเรียก AI