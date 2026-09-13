# Smart Rights Viewer — Backend

Backend สำหรับเว็บแอปที่ช่วยวิเคราะห์ **สิทธิประโยชน์ภาครัฐเบื้องต้น** จากข้อความภาษาไทยแบบอิสระ เช่น อายุ สถานะงาน ประกันสังคม รายได้ สัญชาติ หรือสถานะครอบครัว ระบบค้นหลักฐานจากคลังความรู้ด้วย RAG แล้วให้ LLM เป็นผู้ตัดสินใจว่าสิทธิใดเกี่ยวข้อง — **retrieval ไม่ทำหน้าที่ตัดสิน eligibility เอง**

> ผลลัพธ์เป็นข้อมูลคัดกรองเบื้องต้น ไม่ใช่การรับรองสิทธิหรือผลอนุมัติ หน่วยงานเจ้าของสิทธิเท่านั้นที่ตรวจสอบและยืนยันผลจริงได้

## ความสามารถ

- รับข้อความภาษาไทย 1–4,000 ตัวอักษร รองรับข้อความที่เขียนติดกันไม่เว้นวรรค
- ใช้ keyword-overlap fallback เมื่อ Embeddings ใช้ไม่ได้ชั่วคราว
- จำกัด 20 requests ต่อ IP ต่อ 60 วินาที (in-memory)
- คลังความรู้ปัจจุบันมี 8 สิทธิ — ดีไซน์ retrieval จึงเน้น "อย่าตัดของจริงทิ้ง" มากกว่า "กรองให้เหลือน้อยที่สุด"

## หลักการออกแบบสำคัญ

ระบบมี pipeline เดียว รับ **ข้อความอิสระ** เป็น input เท่านั้น
หลักที่ยึดตลอดทั้งระบบ: **retrieval (`rag.py`) มีหน้าที่แค่ "หาและจัดอันดับหลักฐาน" ไม่ใช่ "ตัดสินว่าใครได้สิทธิ"** เพราะระบบไม่มี rule engine — การตัดสิน likely/needs verification/not eligible เป็นหน้าที่ของ LLM (`llm.py`) ล้วนๆ โดยอิงจากหลักฐานที่ retrieval หามาให้เท่านั้น

## สถาปัตยกรรม

### Flow หลัก (`/analyze-rights`, `/analyze-more-rights`)

```text
Frontend (result.html + result.js)
  │ POST /analyze-rights { "text": "..." }
  ▼
FastAPI (main.py): validate ความยาว, rate limit
  ▼
1) รู้ขอบเขตคร่าวๆ ด้วย keyword — rag.is_rights_query()
   ├─ ไม่ผ่าน → ตอบ "นอกขอบเขต" ทันที ไม่เรียก embedding/LLM
   ▼ ผ่าน
2) rag.retrieve_for_text()
   ├─ embed ข้อความด้วย text-embedding-3-small
   ├─ เทียบ cosine similarity กับเอกสารทุกสิทธิ active
   ├─ scope-floor check: คะแนนดีที่สุดต่ำกว่า SCOPE_FLOOR หรือไม่
   │    ├─ ต่ำกว่า → คืนค่าว่าง (นอกขอบเขตเชิงความหมาย / "ข้อมูลไม่พอ")
   │    ▼ ไม่ต่ำกว่า
   └─ คืนทุกสิทธิที่จัดอันดับแล้ว (ไม่มีการกรองทีละตัวอีก)
   ▼
3) main.py แบ่งผลลัพธ์: primary = RAG_LLM_RESULT_LIMIT ตัวแรก, additional = ส่วนที่เหลือ
   ▼
4) LLM (llm.analyze_rights) วิเคราะห์เฉพาะ primary
   ├─ ได้เห็นเฉพาะ short_description + benefit_details ของแต่ละสิทธิ 
   ├─ AI มีสิทธิ "ไม่พูดถึง" สิทธิที่เห็นว่าไม่เกี่ยวข้องเลย (ต่างจากปฏิเสธชัดเจนที่ต้องลง not_eligible)
   └─ ตัดสิน status: likely_eligible / needs_verification / not_eligible ต่อรายการที่หยิบมาพูดถึง
   ▼
5) main.py ตรวจ source_ids ให้ตรงกับหลักฐานจริง (ไม่ตรง → ตัดรายการนั้นทิ้งเงียบๆ) + บันทึก analytics
   ▼
JSON response (พร้อม additional_benefits ให้กดดูเพิ่มผ่าน /analyze-more-rights) → Frontend
```

**จุดสำคัญที่มักเข้าใจผิด**: `rag.retrieve_for_text()` ยังคง embed เอกสารทุกฉบับและต่อเนื้อหาที่ใกล้เคียงที่สุด (สูงสุด 2 ฉบับต่อสิทธิ) เก็บไว้ในฟิลด์ `Benefit.detail` — แต่ `llm.analyze_rights()` **ไม่ได้อ่านฟิลด์นี้เลย** อ่านแค่ `short_description` และ `benefit_details` (ข้อมูลระดับ catalogue) เท่านั้น เดิม `Benefit.detail` เคยถูกใช้จริงใน endpoint `/explain` (ที่ถูกถอดออกไปแล้ว) ตอนนี้จึงกลายเป็นค่าที่คำนวณไว้แต่ไม่มีใครอ่าน — เวลาจะแก้เงื่อนไขสิทธิให้ LLM เห็น (เช่น เพิ่มเงื่อนไขสัญชาติ) **ต้องแก้ที่ `benefits.short_description`/`benefits.benefit_details` เท่านั้น** แก้ที่ `benefit_documents.content` เพียงอย่างเดียวจะไม่มีผลอะไรกับสิ่งที่ LLM เห็น (มีผลแค่กับการจัดอันดับผ่าน embedding)

## RAG ทำงานอย่างไร

RAG (`retrieve_for_text`) ใช้ threshold **แบบเดียว ทำหน้าที่เดียว** ต่างจากดีไซน์เดิมที่เคยมี absolute threshold กรองทีละสิทธิ (พบว่าทำให้สิทธิที่เกี่ยวข้องจริงหลุดทิ้งไปเงียบๆ เพราะ embedding คะแนนของข้อความสั้นมักไม่สูงเท่าที่คาด):

1. Normalize ข้อความ (`normalize_text`) — รองรับข้อความที่เขียนติดกันไม่เว้นวรรค
2. `is_rights_query()` คัดกรองแบบคำ (keyword) ก่อนเรียก API ใดๆ — กันข้อความนอกเรื่องชัดเจนแบบถูกและเร็ว **แต่เช็คแค่คำในลิสต์ภาษาไทยเท่านั้น** (ดูข้อจำกัดด้านล่าง)
3. Embed ข้อความด้วย `text-embedding-3-small` แล้วเทียบ cosine similarity กับ embedding ของเอกสารทุกฉบับ (สร้างและ cache ใน `benefit_documents.embedding` เมื่อค้นครั้งแรก) เลือกหลักฐานสูงสุดไม่เกิน 2 ฉบับต่อสิทธิ
4. **`SCOPE_FLOOR`** เช็คแค่ครั้งเดียวกับ**คะแนนสูงสุด**ของทั้ง query — ถ้าแม้แต่สิทธิที่ใกล้เคียงที่สุดยังต่ำกว่าเกณฑ์นี้ แปลว่า query นี้ไม่เกี่ยวกับสิทธิใดในคลังเลยจริงๆ (off-topic/พิมพ์มั่ว) จึงคืนค่าว่าง — **ไม่ใช่การกรองทีละสิทธิ ไม่มีการเก็บ score รายตัวไว้ใช้ต่อ**
5. ถ้าผ่านข้อ 4 → คืน**ทุกสิทธิที่จัดอันดับแล้ว** (สูงสุด `RAG_DISCOVERY_RESULT_LIMIT`) ไม่มีใครถูกตัดทิ้งอีก เพราะคลังมีแค่ 8 รายการ ต้นทุนส่งให้ LLM ทั้งหมดต่ำกว่าความเสี่ยงที่จะ tune threshold ผิดแล้วตัดของจริงทิ้ง
6. หาก Embeddings ล้มเหลว ใช้ keyword overlap กับ `SCOPE_FLOOR_KEYWORD` แทนในหลักการเดียวกัน คุณภาพการจัดอันดับจะลดลงแต่ยังใช้งานต่อได้
7. `main.py` ส่งเพียง `RAG_LLM_RESULT_LIMIT` รายการแรก (default `10` — เท่ากับจำนวนสิทธิทั้งหมดในคลังตอนนี้ จึงแทบไม่มี `additional_benefits` เหลือให้กดดูเพิ่ม) ให้ LLM วิเคราะห์
8. LLM ได้รับข้อความผู้ใช้พร้อมชื่อสิทธิ เอกสารที่ต้องใช้ ติดต่อ `short_description`, `benefit_details` ของสิทธิที่คัดมาเท่านั้น — ห้ามใช้ความรู้ภายนอก และห้ามยืนยันว่าได้รับสิทธิจริงไม่ว่าสถานะใด
9. Backend ตรวจ `source_ids` ที่ LLM ตอบให้ตรงกับหลักฐานจริงก่อนสร้าง `sources` ใน response — ถ้าไม่ตรง รายการนั้นถูกตัดทิ้งทั้งอันโดยไม่มี log แจ้ง

| สถานะ | ความหมาย | เกณฑ์ที่ LLM ใช้ตัดสิน |
| --- | --- | --- |
| `likely_eligible` | ข้อมูลผู้ใช้สอดคล้องกับเงื่อนไขในหลักฐานระดับเบื้องต้น | หลักฐานระบุเงื่อนไข และผู้ใช้ให้ข้อมูลที่เข้าเงื่อนไขนั้น |
| `needs_verification` | สิทธิอาจเกี่ยวข้อง แต่ข้อมูลผู้ใช้หรือหลักฐานยังไม่พอสรุป | ขาดข้อมูลบางส่วน (ไม่ใช่ข้อมูลที่ถูกปฏิเสธไปแล้ว) |
| `not_eligible` | ข้อความผู้ใช้ปฏิเสธเงื่อนไขสำคัญที่หลักฐานกำหนดไว้ชัดเจน | เช่น หลักฐานต้องการสัญชาติไทยแต่ user บอกว่าไม่ใช่คนไทย — กฎ negation บังคับให้จัดสถานะนี้ทันที ไม่ปล่อยเป็น `needs_verification` |

ทั้ง 3 สถานะเป็นการแบ่งที่ **เกิดหลัง RAG คัดกรองมาแล้วเท่านั้น** ไม่มีจุดไหนก่อนหน้านั้นในโค้ดที่ตัดสิน "มี/ไม่มีสิทธิ" และแม้แต่ `not_eligible` เองก็ยังถูกบังคับด้วยพรอมต์ให้เป็นคำ hedge เสมอ (frontend แสดงเป็น "ยังไม่น่ามีสิทธิ**จากข้อมูลที่ระบุ**" ไม่ใช่ "ไม่มีสิทธิ") — ระบบไม่มีทางฟันธงเชิงลบตรงๆ กับผู้ใช้เลยไม่ว่ากรณีใด

ที่ควรรู้เพิ่ม: พรอมต์สั่งให้ LLM "จัดเฉพาะสิทธิที่อาจเกี่ยวข้อง" ลงสถานะ — สิทธิที่ LLM เห็นว่าไม่เกี่ยวข้องเลย (ต่างจากปฏิเสธชัดเจน) จะ**ไม่ถูกพูดถึงในผลลัพธ์เลย** ไม่ใช่ถูกจัดเป็น `not_eligible` พร้อมเหตุผล ถ้ารายการที่ผ่าน RAG มาทั้งหมดตกอยู่ในกลุ่มนี้ ผู้ใช้จะเห็นแค่ข้อความ "ยังไม่พบสิทธิที่มีหลักฐานเพียงพอในฐานข้อมูล" แบบทั่วไป

**ข้อจำกัดสำคัญของ retrieval ที่ต้องรู้ไว้**: cosine similarity จับ "ความใกล้เคียงเชิงหัวข้อ" ไม่ได้เข้าใจการปฏิเสธ (negation) — ข้อความ "ไม่ใช่คนไทย" อาจมีคะแนนใกล้เคียงกับเอกสารที่พูดเรื่องสัญชาติสูงพอๆ กับข้อความ "เป็นคนไทย" เพราะแชร์คำศัพท์เดียวกัน **การตัดสินใจที่ถูกต้องเรื่องนี้จึงต้องพึ่ง LLM + เอกสารที่ระบุเงื่อนไขชัดเจนเท่านั้น ไม่ใช่หน้าที่ของ retrieval** ดังนั้น `short_description`/`benefit_details` ของทุกสิทธิในคลังต้องระบุเงื่อนไขสำคัญ (โดยเฉพาะสัญชาติ) ไว้ตรงๆ เสมอ ไม่งั้น LLM จะไม่มีหลักฐานให้ใช้ตัดสิน `not_eligible`

**ข้อจำกัดของ `is_rights_query()` (scope-check ชั้นแรก)**: เป็น string-match ตรงตัวกับ `_RIGHTS_TERMS` ซึ่งเป็นคำไทยล้วน ไม่มี fuzzy-match หรือ spell-correction และไม่รองรับภาษาอังกฤษเลย ผลคือ 3 สถานการณ์นี้ตกไปอยู่ผลลัพธ์เดียวกันหมด ("นอกขอบเขต") ทั้งที่สาเหตุต่างกัน:
- ข้อความนอกเรื่องจริงๆ (ตามที่ตั้งใจออกแบบไว้)
- ข้อความพิมพ์ผิดจนคำหลัก (เช่น "สิทธิ") ไม่ตรง exact match
- ข้อความภาษาอังกฤษที่เนื้อหาเกี่ยวข้องจริง (เช่น "old age pension") — โมเดล embedding รองรับได้ แต่ถูกกันไว้ตั้งแต่ก่อนถึงชั้น embedding

ถ้าต้องการแยก 3 กรณีนี้ออกจากกัน ต้องเพิ่ม logic ใหม่ (เช่น เพิ่มคำอังกฤษเข้า `_RIGHTS_TERMS`, หรือข้าม keyword-gate แล้วให้ embedding scope-floor ตัดสินอย่างเดียว) — ปัจจุบันยังไม่ได้ทำ

## ความแม่นยำและข้อจำกัด

โครงการยังไม่มีชุดทดสอบที่เฉลยโดยผู้เชี่ยวชาญหรือหน่วยงานรัฐ จึง **ไม่ควรอ้างเปอร์เซ็นต์ความแม่นยำ** และยังไม่มีค่า Precision, Recall หรือ F1-score ที่ยืนยันได้

- ระบบครอบคลุมเฉพาะสิทธิและเอกสารที่อยู่ในคลังความรู้ (ปัจจุบัน 8 รายการ)
- ผลขึ้นกับความครบถ้วนของข้อความ ความครบถ้วนของเงื่อนไขใน `short_description`/`benefit_details` คุณภาพ retrieval และการตีความของ LLM
- เงื่อนไขจริงจำนวนมากต้องตรวจจากข้อมูลภายนอก เช่น รายได้/ทรัพย์สินครัวเรือน ทะเบียนบ้าน ประวัติประกันสังคม และสิทธิซ้ำซ้อน
- เกณฑ์ราชการเปลี่ยนได้ จึงต้องทบทวนคลังอย่างสม่ำเสมอ
- keyword fallback มีไว้เพื่อความต่อเนื่อง ไม่ใช่ semantic search ที่สมบูรณ์
- embedding-based retrieval ไม่เข้าใจการปฏิเสธ (negation) — พึ่งเอกสารที่ระบุเงื่อนไขชัดเจน + LLM เป็นคนตัดสิน ไม่ใช่ retrieval
- `is_rights_query()` เป็น keyword-whitelist ภาษาไทยล้วน แยกไม่ออกระหว่าง "นอกเรื่องจริง" กับ "พิมพ์ผิด" กับ "ภาษาอังกฤษที่เกี่ยวข้อง" (ดูหัวข้อ RAG ด้านบน)
- `Benefit.detail` (เนื้อหาเอกสารดิบจาก RAG) ถูกคำนวณไว้แต่ `llm.analyze_rights()` ไม่ได้อ่านค่านี้ — เป็นค่าที่คำนวณทิ้งเปล่าอยู่ในโค้ดตอนนี้ ยังไม่ได้ตัดสินใจว่าจะต่อสายให้ LLM ใช้ หรือจะลดงาน embedding ระดับ document ลง (ดู "แนวทางพัฒนาต่อ")
- ข้อความ `coverage_warning` ที่แสดงท้ายผล มาจาก 2 แหล่งคนละไฟล์ (`main.py` สำหรับกรณีไม่ถึง LLM, `llm.py` สำหรับกรณี LLM วิเคราะห์สำเร็จ) เป็นการตั้งใจให้ต่างกันตามสถานการณ์ ไม่ใช่ของหลงเหลือ แต่ยังไม่ได้รวมเป็นค่าคงที่ส่วนกลาง
- rate limit ไม่แชร์ข้าม worker และหายเมื่อ restart
- ห้ามส่งเลขบัตรประชาชน รหัสผ่าน ข้อมูลบัญชี หรือข้อมูลอ่อนไหวเกินจำเป็น

ควรสร้าง test set ภาษาไทยที่มีโปรไฟล์จำลอง (รวมเคสปฏิเสธเงื่อนไข เช่น "ไม่ใช่คนไทย" และเคสภาษาอังกฤษ/พิมพ์ผิด) หลักฐานที่ควรค้นพบ และผลที่ผู้เชี่ยวชาญรับรอง แล้ววัด retrieval hit rate, Precision, Recall, F1-score ของสถานะ และความถูกต้องของแหล่งอ้างอิง

## API

Swagger UI: `http://127.0.0.1:8000/docs`

### `GET /health`

```json
{ "status": "ok", "version": "1.0.0" }
```

### `POST /analyze-rights` — flow หลัก

```json
{ "text": "อายุ 65 ปี สัญชาติไทย ว่างงาน ไม่มีประกันสังคม และมีบัตรผู้พิการ" }
```

```json
{
  "summary": "สรุปผลการวิเคราะห์เบื้องต้น",
  "benefits": [{
    "name": "เบี้ยยังชีพผู้สูงอายุ",
    "status": "needs_verification",
    "explanation": "อาจเกี่ยวข้องจากอายุและสัญชาติที่ระบุ...",
    "missing_information": ["สถานะบำนาญหรือสวัสดิการรัฐที่ซ้ำซ้อน"],
    "sources": [{
      "title": "เบี้ยยังชีพผู้สูงอายุ", "url": "https://www.dop.go.th/en/topic/view=200",
      "docs": ["สำเนาบัตรประชาชน"], "contact": ["สำนักงานเทศบาล / อบต. ในพื้นที่"],
      "short_description": "...", "benefit_details": "..."
    }]
  }],
  "follow_up_questions": ["ปัจจุบันได้รับบำนาญหรือสวัสดิการอื่นจากรัฐหรือไม่"],
  "coverage_warning": "...",
  "additional_benefits": [{ "slug": "...", "name": "..." }]
}
```

หมายเหตุ: `sources[].title` มีอยู่ใน schema แต่ frontend ปัจจุบันไม่ได้เอาไปแสดง (ชื่อสิทธิหลักแสดงจาก `benefits[].name` แทนอยู่แล้ว)

หากข้อความนอกขอบเขต (ไม่ผ่าน `is_rights_query`) หรือไม่มีสิทธิใดผ่าน `SCOPE_FLOOR` เลย API ตอบ `200` พร้อม `benefits: []` และคำแนะนำ โดยไม่เรียก LLM

### `POST /analyze-more-rights`

วิเคราะห์เฉพาะ `slug` ที่คืนใน `additional_benefits` จากผลก่อนหน้า (มักจะว่างเปล่าเมื่อ `RAG_LLM_RESULT_LIMIT` ≥ จำนวนสิทธิทั้งหมดในคลัง)

```json
{ "text": "อายุ 65 ปี สัญชาติไทย ว่างงาน", "benefit_slugs": ["universal_health"] }
```

response ใช้ schema เดียวกับ `/analyze-rights` และ `additional_benefits` จะว่าง

> **ข้อควรทราบเรื่องประสิทธิภาพ**: endpoint นี้เรียก `rag.retrieve_for_text()` ใหม่ทั้งหมด (embed query + เอกสารทุกฉบับในคลังอีกครั้ง) แล้วค่อย filter เอาเฉพาะ `benefit_slugs` ที่ร้องขอทีหลัง ทั้งที่รู้ slug อยู่แล้วตั้งแต่ต้น เป็นการคำนวณซ้ำที่ไม่จำเป็น — ยังไม่กระทบผู้ใช้เพราะปัจจุบัน endpoint นี้แทบไม่เคยถูกเรียกจริง (catalogue มีแค่ 8 รายการ ต่ำกว่า `RAG_LLM_RESULT_LIMIT`) แต่ควรแก้เป็นดึง record ตาม slug ตรงๆ จาก database ก่อนคลังข้อมูลจะโตเกิน limit

### `GET /stats`

```json
{ "total_inquiries": 0, "avg_benefits": 0, "avg_ai_response_ms": 0, "top_benefits": [] }
```

| HTTP | กรณี |
| --- | --- |
| `422` | validation ไม่ผ่าน, ข้อความว่าง/เกิน 4,000 ตัวอักษร หรือ slug ไม่อยู่ในผล RAG ที่อนุญาต |
| `429` | เกิน 20 requests ต่อ IP ภายใน 60 วินาที |
| `503` | ไม่มี `OPENAI_API_KEY` ตอนต้องเรียก Embeddings หรือ LLM |
| `502` | RAG หรือ AI วิเคราะห์ไม่สำเร็จ |
| `500` | ดึงสถิติจากฐานข้อมูลไม่สำเร็จ |

## ฐานข้อมูล

ใช้ PostgreSQL ผ่าน SQLAlchemy async และ `asyncpg`; ระบบสร้างตารางเมื่อเริ่มแอป

| ตาราง | หน้าที่ | ใครอ่านใช้ |
| --- | --- | --- |
| `benefits` | สิทธิหลัก: slug, หมวด, เอกสาร, ติดต่อ, URL, `short_description`, `benefit_details`, `active` | `llm.analyze_rights()` อ่าน `short_description`+`benefit_details` โดยตรง — เป็นข้อมูลเดียวที่ LLM เห็นจริง |
| `benefit_documents` | หลักฐานดิบสำหรับ RAG: title, `content`, URL, `embedding`, `active` | `content`/`embedding` ใช้จัดอันดับความใกล้เคียงเท่านั้น (ไม่ได้ถูกส่งให้ LLM อ่านต่ออีกแล้ว — ดูหมายเหตุด้านล่าง) |
| `inquiry_log` | `text_length` และรายชื่อสิทธิที่แสดง | analytics (`/stats`) |
| `ai_response_log` | `text_length`, สิทธิ, summary/response และเวลา AI ตอบ (ms) | analytics (`/stats`) |

**ความสัมพันธ์ระหว่าง field กับสิ่งที่ LLM เห็นจริง (สำคัญตอนแก้เงื่อนไขสิทธิ):**

```text
benefits.short_description ─┐
benefits.benefit_details    ├─► ส่งเข้า prompt ของ llm.analyze_rights() โดยตรง — สิ่งเดียวที่ LLM "อ่าน" ได้จริง
                             │
benefit_documents.content   ─────► ใช้แค่คำนวณ embedding เพื่อจัดอันดับสิทธิ (retrieve_for_text)
                             │      เนื้อหาไม่ถูกส่งต่อให้ LLM อ่านอีกต่อไป (เดิมส่งผ่าน b.detail ให้ /explain ซึ่งถูกลบไปแล้ว)
                             │
benefit_documents.embedding ─────► cosine similarity ใน retrieve_for_text() (สร้าง/cache อัตโนมัติเมื่อค้นครั้งแรก)
```

แก้เงื่อนไขสิทธิที่ต้องการให้ **LLM เห็นจริง** (เช่น เพิ่มเกณฑ์สัญชาติ) ต้องแก้ที่ `benefits.short_description` หรือ `benefits.benefit_details` เท่านั้น — แก้แค่ `benefit_documents.content` เพียงอย่างเดียวจะไม่มีผลอะไรกับสิ่งที่ LLM เห็น เพราะ `content` มีผลแค่กับการจัดอันดับผ่าน embedding ไม่ใช่เนื้อหาที่ส่งเข้า prompt

เมื่อฐานข้อมูลว่าง `rag_catalog.py` จะ seed สิทธิเริ่มต้น 8 รายการเพียงครั้งเดียว: เบี้ยยังชีพผู้สูงอายุ เบี้ยความพิการ สวัสดิการแห่งรัฐ ประกันสังคมมาตรา 33/39/40 เงินอุดหนุนเด็กแรกเกิด และบัตรทอง **การแก้ `rag_catalog.py` ภายหลังจะไม่เขียนทับข้อมูลใน DB ที่ seed ไปแล้ว** — ต้อง `UPDATE` ผ่าน SQL โดยตรง หรือลบ record เดิมแล้วให้ seed ใหม่เท่านั้น

## โครงสร้าง

```text
Backend/
├── main.py         # FastAPI routes (/health, /analyze-rights, /analyze-more-rights, /stats), schemas, CORS, rate limiting
├── rag.py          # retrieval, cosine similarity, scope-floor gate, keyword fallback
├── llm.py          # OpenAI Embeddings / Chat Completions และ prompt (analyze_rights)
├── database.py     # models, lifecycle, seed, analytics
├── rag_catalog.py  # ข้อมูลเริ่มต้นเมื่อ database ว่าง (seed ครั้งเดียว)
├── models.py       # domain models ที่ใช้ร่วมกัน
├── run.py          # Uvicorn development server
├── requirements.txt
└── README.md

Frontend/
├── home.html       # หน้าแรก
├── input.html      # ฟอร์มกรอกข้อความอิสระ + แดชบอร์ดสถิติ
├── result.html     # หน้าแสดงผลวิเคราะห์
├── app.js          # ค่ากลางที่ใช้ร่วมกัน (API_BASE) เท่านั้น
├── input.js        # logic เฉพาะ input.html (submit, dashboard)
└── result.js       # logic เฉพาะ result.html (เรียก /analyze-rights ฯลฯ, render ผล)
```

## การติดตั้ง

### 1. เตรียม PostgreSQL

```sql
CREATE DATABASE rights_db;
```

### 2. สร้าง `Backend/.env`

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/rights_db
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
ALLOWED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

# scope-check เดียว ไม่ใช่ per-item filter — ดูหัวข้อ "RAG ทำงานอย่างไร"
RAG_SCOPE_FLOOR=0.25
RAG_SCOPE_FLOOR_KEYWORD=0.08
RAG_DISCOVERY_RESULT_LIMIT=10
RAG_LLM_RESULT_LIMIT=10
```

`OPENAI_API_KEY` ใช้กับ OpenAI API โดยตรงและมีค่าใช้จ่ายแยกจาก ChatGPT subscription ห้าม commit `.env`

### 3. ติดตั้งและรัน

```powershell
cd Backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

API อยู่ที่ `http://127.0.0.1:8000`; `run.py` เปิด reload สำหรับ development

### 4. เชื่อม Frontend

เปิด `Frontend` ผ่าน Live Server แล้วเข้า `http://localhost:5500/home.html` ค่า CORS เริ่มต้นรองรับ `localhost:5500` และ `127.0.0.1:5500`; deployment คนละ origin ต้องเพิ่ม URL ใน `ALLOWED_ORIGINS`

Frontend เรียก API ผ่านค่าคงที่ `API_BASE` ที่ประกาศไว้ที่เดียวใน `Frontend/app.js` — เปลี่ยน URL ตอน deploy จริงแก้ที่ไฟล์นี้ไฟล์เดียวพอ

### คลังความรู้

หลัง seed ครั้งแรก ให้ดูแลผ่าน PostgreSQL เป็นหลัก (แก้ `rag_catalog.py` จะไม่มีผลกับ DB ที่ seed ไปแล้ว)

1. เพิ่ม/แก้ `benefits.short_description` และ `benefits.benefit_details` เพื่อปรับเงื่อนไขที่ LLM มองเห็น — **ทุกเงื่อนไขคัดออกที่สำคัญ (โดยเฉพาะสัญชาติ) ต้องเขียนตรงๆ ในสองฟิลด์นี้** ไม่งั้น LLM จะไม่มีหลักฐานตัดสิน `not_eligible`
2. เพิ่ม/แก้ `benefit_documents.content` เมื่อต้องการปรับ**การจัดอันดับ**ความเกี่ยวข้อง (ไม่ใช่เพื่อให้ LLM เห็นเนื้อหาเพิ่ม — ดูหัวข้อฐานข้อมูล)
3. ตั้ง `embedding = NULL` หลังแก้ `content` เพื่อให้ระบบสร้าง vector ใหม่ครั้งถัดไป
4. ตั้ง `active = false` สำหรับสิทธิหรือเอกสารที่ไม่ต้องการให้ค้นพบ แทนการลบประวัติ
5. ทดสอบทั้งเคสปกติและเคส**ปฏิเสธเงื่อนไข** (เช่น "ไม่ใช่คนไทย", "ไม่มีบุตร") ก่อนเผยแพร่การเปลี่ยนแปลง เพื่อยืนยันว่า LLM จัดเป็น `not_eligible` ถูกต้อง ไม่ใช่ `needs_verification`

### เฝ้าระวังและ production

- ตรวจ `/health` และ `/stats` เพื่อติดตามสถานะ ปริมาณงาน เวลา AI ตอบ และสิทธิที่พบบ่อย
- ติดตาม log สำหรับข้อผิดพลาด OpenAI, PostgreSQL และ `429`
- `RAG_SCOPE_FLOOR` ปรับจาก test set ที่มี label: ลดเกณฑ์เพิ่ม recall แต่เสี่ยงให้ query นอกเรื่องหลุดผ่าน; เพิ่มเกณฑ์มีผลตรงกันข้าม — ปรับเฉพาะจุดนี้จุดเดียว ไม่ต้อง tune ต่อสิทธิ
- ถ้าคลังโตเกิน ~10-20 รายการ ทบทวนว่ายังเหมาะจะส่งทุกสิทธิให้ LLM แบบไม่กรองอยู่หรือไม่ (ตอนนี้ตั้งใจทำแบบนี้เพราะคลังมีแค่ 8 รายการ) และทบทวน `/analyze-more-rights` ที่ปัจจุบัน re-run retrieval ทั้งหมดทุกครั้ง (ดูหัวข้อ API)
- หลาย worker/instance ควรย้าย rate limit ไป Redis หรือ API gateway และเก็บ secret ใน secret manager
- สำรองฐานข้อมูล จำกัดสิทธิ์บัญชี PostgreSQL และทบทวน URL/เกณฑ์สิทธิเป็นรอบ

## แนวทางพัฒนาต่อ

- ตัดสินใจชะตากรรมของ `Benefit.detail`: ต่อสายให้ `llm.analyze_rights()` อ่านเนื้อหาเอกสารดิบด้วย (ตามที่ retrieval คำนวณไว้อยู่แล้ว) หรือลดงาน embed เอกสารระดับ document ลงถ้าจะไม่ใช้ต่อ
- แก้ `/analyze-more-rights` ให้ดึง record ตาม `slug` ตรงๆ จาก database แทนการรัน `retrieve_for_text()` ใหม่ทั้งหมด
- รวมข้อความ `coverage_warning`/`summary` fallback ที่กระจายอยู่ `main.py` และ `llm.py` ไว้เป็นค่าคงที่ส่วนกลาง (เช่นไฟล์ `messages.py`) เพื่อคุมโทนให้ตรงกันง่ายขึ้น
- ปรับ `is_rights_query()` ให้แยกแยะ "นอกเรื่องจริง" กับ "พิมพ์ผิด" กับ "ภาษาอังกฤษที่เกี่ยวข้อง" ออกจากกัน แทนที่จะตกไปเป็นข้อความ "นอกขอบเขต" เดียวกันหมด
- เพิ่ม Alembic migrations แทน `create_all`/`ALTER TABLE` ระหว่าง startup
- เพิ่ม metadata เอกสาร: วันที่มีผลบังคับใช้ จังหวัด หน่วยงาน และวันที่ตรวจทาน
- เพิ่ม automated evaluation สำหรับ retrieval, การเลือกสถานะ (โดยเฉพาะเคส negation) และความถูกต้องของแหล่งอ้างอิง
- ใช้ `pgvector` หรือ vector store เมื่อจำนวนเอกสารเพิ่มขึ้นจนไม่เหมาะกับการส่งทุกสิทธิให้ LLM แบบไม่กรอง
- เพิ่มการ redaction/ตรวจจับข้อมูลอ่อนไหวก่อนเรียก AI