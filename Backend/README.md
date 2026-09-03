# Smart Rights Viewer — วิเคราะห์สิทธิจากข้อความด้วย AI + RAG

ระบบเว็บสำหรับช่วยวิเคราะห์ **สิทธิประโยชน์ภาครัฐเบื้องต้น** จากข้อความภาษาไทยที่ผู้ใช้พิมพ์ เช่น อายุ สถานะการทำงาน ประกันสังคม หรือข้อมูลครอบครัว ระบบใช้ RAG ค้นหลักฐานจากคลังความรู้ แล้วให้ OpenAI สรุปสิทธิที่อาจเกี่ยวข้อง พร้อมข้อมูลที่ต้องตรวจสอบเพิ่มและแหล่งอ้างอิง

> ระบบนี้เป็นเครื่องมือคัดกรองและอธิบายเบื้องต้น ไม่ใช่การรับรองสิทธิ หน่วยงานเจ้าของสิทธิเท่านั้นที่ตรวจสอบข้อมูลจริงและยืนยันผลได้

## คุณสมบัติ

- รับข้อความอิสระภาษาไทย ไม่บังคับให้กรอกตามช่องตายตัว
- ค้นข้อมูลสิทธิจาก PostgreSQL ด้วย semantic search (embeddings) และ keyword fallback
- ส่งเฉพาะหลักฐานที่ RAG ค้นพบไปให้ AI วิเคราะห์
- จัดผลลัพธ์เป็น 3 สถานะ: `likely_eligible`, `needs_verification`, `not_eligible`
- แสดงคำถาม/ข้อมูลที่ต้องตรวจเพิ่มและลิงก์อ้างอิง
- มีหน้า loading ระหว่าง AI + RAG กำลังทำงาน
- มีสถิติการใช้งานผ่าน `/stats`

## สถาปัตยกรรม

```text
Frontend: input.html
  ผู้ใช้พิมพ์ข้อมูลอิสระ
        |
        | เก็บข้อความชั่วคราวใน sessionStorage แล้วเปิด result.html
        v
Frontend: result.html
  แสดง AI + RAG loading state
        |
        | POST /analyze-rights  { "text": "..." }
        v
FastAPI (main.py)
        |
        +--> rag.retrieve_for_text()
        |      OpenAI Embeddings + PostgreSQL benefit_documents
        |
        `--> llm.analyze_rights()
               OpenAI Chat Completions + หลักฐาน RAG
        |
        v
JSON ผลวิเคราะห์ -> แสดงบัตรผลลัพธ์บน result.html
```

## การทำงานของระบบ

### 1. ผู้ใช้ป้อนข้อความ

หน้า `Frontend/input.html` รับข้อความ 1–4,000 ตัวอักษร ตัวอย่างเช่น

```text
อายุ 65 ปี สัญชาติไทย ว่างงาน ไม่มีประกันสังคม และมีบัตรผู้พิการ
```

เมื่อกดปุ่มตรวจสอบ หน้าเว็บจะเก็บข้อความไว้ใน `sessionStorage` แล้วเปลี่ยนไป `result.html` ทันที เพื่อให้ผู้ใช้เห็นสถานะกำลังวิเคราะห์โดยไม่ต้องรออยู่หน้าเดิม

### 2. หน้า loading และการเรียก API

`result.html` เรียก `loadTextAnalysis()` ซึ่งแสดงการ์ด `AI + RAG ANALYSIS` พร้อมจุด animation 3 จุด แล้วจึงส่ง `POST /analyze-rights`

เมื่อ API ตอบสำเร็จ หน้า loading จะถูกแทนที่ด้วยผลจริง หากเกิดข้อผิดพลาด ระบบจะแสดงข้อความผิดพลาดบนหน้า result เดิมเพื่อให้ผู้ใช้ลองใหม่ได้

### 3. RAG ค้นหลักฐาน

`rag.retrieve_for_text()` ทำงานกับสิทธิที่ active ทุกตัวในคลัง

1. สร้าง embedding ของข้อความผู้ใช้ด้วย `text-embedding-3-small`
2. เปรียบเทียบ query vector กับ embedding ของเอกสารแต่ละชิ้นด้วย cosine similarity
3. เลือกเอกสารที่ใกล้ที่สุดไม่เกิน 2 ชิ้นต่อสิทธิ
4. เรียงสิทธิตามคะแนนของเอกสารที่ใกล้ที่สุด แล้วคืนสูงสุด 8 สิทธิ

หาก Embeddings ใช้งานไม่ได้ชั่วคราว จะใช้ keyword overlap เป็น fallback และยังส่งหลักฐานไปให้ AI ได้ อย่างไรก็ตามคุณภาพการค้นอาจลดลง

### 4. AI วิเคราะห์จากหลักฐาน

`llm.analyze_rights()` สร้าง prompt ที่มี

- ข้อความต้นฉบับของผู้ใช้
- ชื่อสิทธิ เอกสารที่ใช้ ช่องทางติดต่อ และ URL
- เนื้อหาหลักฐาน RAG พร้อมรหัสแหล่งข้อมูล เช่น `S1`, `S2`

โมเดล `gpt-5.6-luna` ถูกกำหนดให้ใช้เฉพาะข้อมูลใน prompt และจัดสิทธิทุกข้อที่มีหลักฐานลงหนึ่งในสามสถานะ

| สถานะ | ความหมาย |
| --- | --- |
| `likely_eligible` | ข้อมูลที่ผู้ใช้ระบุสอดคล้องกับหลักฐานในระดับเบื้องต้น |
| `needs_verification` | อาจเกี่ยวข้อง แต่ข้อมูลผู้ใช้หรือเงื่อนไขในหลักฐานยังไม่พอ |
| `not_eligible` | ข้อมูลผู้ใช้ขัดกับเงื่อนไขที่ระบุชัดในหลักฐาน |

เมื่อหลักฐานไม่เพียงพอ prompt กำหนดให้ AI ใช้ `needs_verification` แทนการสรุปว่าไม่มีสิทธิ

### 5. ตรวจสอบและแสดงผล

Backend ตรวจ `source_ids` ที่ AI ตอบกลับกับรายการหลักฐานจริงก่อนส่งให้ frontend หากแหล่งข้อมูลไม่ตรง ระบบจะไม่นำรายการนั้นมาแสดง ผลลัพธ์จึงประกอบด้วยคำอธิบาย ข้อมูลที่ต้องตรวจเพิ่ม และแหล่งอ้างอิงจากฐานข้อมูลเท่านั้น

## ความแม่นยำและข้อจำกัด

โครงการยังไม่มีชุดทดสอบที่มีคำตอบจริงจากผู้เชี่ยวชาญหรือหน่วยงานรัฐ และยังไม่ได้วัด Precision, Recall หรือ F1-score ดังนั้น **ไม่ควรอ้างเปอร์เซ็นต์ความแม่นยำของระบบ**

ผลลัพธ์ขึ้นกับ:

- ความครบถ้วนของข้อความที่ผู้ใช้พิมพ์
- ความถูกต้อง ความใหม่ และความครอบคลุมของ `benefit_documents`
- คุณภาพของการค้นคืนเอกสารและการตีความหลักฐานของ LLM

ข้อควรระวังสำคัญ:

- RAG ส่งผลเฉพาะสิทธิที่มีอยู่ในคลังความรู้ ไม่ได้ครอบคลุมทุกสิทธิภาครัฐ
- เงื่อนไขจริงหลายอย่างไม่มีในข้อความ เช่น รายได้ครัวเรือน ทรัพย์สิน ทะเบียนบ้าน หรือประวัติประกันสังคม
- สถานะจาก AI ไม่ใช่ผลอนุมัติ และประกาศ/เกณฑ์ราชการอาจเปลี่ยนได้
- อย่าใส่ข้อมูลลับ เช่น เลขบัตรประชาชน รหัสผ่าน หรือข้อมูลการเงินละเอียดลงในช่องข้อความ

หากต้องการประเมินระบบเชิงตัวเลข ควรสร้าง test set ที่มี profile และผลตรวจสิทธิจริง แล้ววัดแยกเป็น retrieval hit rate, Precision, Recall และ F1-score ของการเลือกสถานะ

## API หลัก

### `POST /analyze-rights`

ใช้กับ flow การพิมพ์ข้อความอิสระของหน้าเว็บ

Request:

```json
{
  "text": "อายุ 65 ปี สัญชาติไทย ว่างงาน ไม่มีประกันสังคม"
}
```

Response ตัวอย่าง:

```json
{
  "summary": "สรุปการวิเคราะห์เบื้องต้น",
  "benefits": [
    {
      "name": "เบี้ยยังชีพผู้สูงอายุ",
      "status": "likely_eligible",
      "explanation": "จากอายุและสัญชาติที่ระบุ อาจเกี่ยวข้อง...",
      "missing_information": ["ตรวจทะเบียนบ้านและสิทธิซ้ำซ้อน"],
      "sources": [
        {
          "title": "เบี้ยยังชีพผู้สูงอายุ",
          "url": "https://...",
          "docs": ["สำเนาบัตรประชาชน"],
          "contact": ["สำนักงานเทศบาล / อบต. ในพื้นที่"]
        }
      ]
    }
  ],
  "follow_up_questions": ["ปัจจุบันได้รับบำนาญหรือสวัสดิการอื่นหรือไม่"],
  "coverage_warning": "ฐานข้อมูลเอกสารสิทธิของระบบยังอยู่ระหว่างรวบรวม..."
}
```

ข้อผิดพลาดที่สำคัญ:

| สถานะ | ความหมาย |
| --- | --- |
| `422` | ข้อความว่างหรือยาวเกิน 4,000 ตัวอักษร |
| `429` | เกิน 20 requests ต่อ IP ภายใน 60 วินาที |
| `503` | ไม่มี `OPENAI_API_KEY` |
| `502` | RAG หรือ AI วิเคราะห์ไม่สำเร็จ |

### Endpoints อื่น

| Method | Endpoint | หน้าที่ |
| --- | --- | --- |
| `GET` | `/health` | ตรวจสถานะ backend |
| `POST` | `/check-rights` | flow เดิมแบบ form fields; คืน candidate จาก RAG |
| `POST` | `/explain` | flow เดิมแบบ form fields; ส่งคำอธิบายผ่าน SSE |
| `GET` | `/stats` | สรุป inquiry และเวลา AI จาก log เดิม |

> `/analyze-rights` เป็น flow หลักของ frontend ปัจจุบัน และยังไม่ได้เขียนข้อมูลเข้า `inquiry_log` หรือ `ai_response_log` ดังนั้น dashboard `/stats` จะยังสะท้อนเฉพาะ flow เดิมที่ใช้ `/check-rights` และ `/explain`

## ฐานข้อมูล

| ตาราง | หน้าที่ |
| --- | --- |
| `benefits` | ชื่อสิทธิ หมวดหมู่ เอกสาร ช่องทางติดต่อ ลิงก์ และสถานะ active |
| `benefit_documents` | เนื้อหาอ้างอิง URL และ embedding ของเอกสาร |
| `inquiry_log` | log ของ flow `/check-rights` |
| `ai_response_log` | log ของ flow `/explain` |

ข้อมูลเริ่มต้น 8 สิทธิอยู่ใน `rag_catalog.py` เช่น เบี้ยยังชีพผู้สูงอายุ เบี้ยความพิการ สวัสดิการแห่งรัฐ ประกันสังคมมาตรา 33/39/40 เงินอุดหนุนเด็กแรกเกิด และบัตรทอง

## โครงสร้างโปรเจกต์

```text
RAG_AI_TEXT/
├── Backend/
│   ├── main.py          # FastAPI routes, validation และ rate limit
│   ├── rag.py           # semantic retrieval และ keyword fallback
│   ├── llm.py           # OpenAI embeddings และ AI analysis
│   ├── database.py      # SQLAlchemy, PostgreSQL และ logging
│   ├── rag_catalog.py   # ข้อมูลตั้งต้นของคลังความรู้
│   ├── models.py        # domain models
│   ├── run.py           # รัน Uvicorn
│   └── README.md
└── Frontend/
    ├── input.html       # รับข้อความของผู้ใช้
    ├── result.html      # loading state และผลการวิเคราะห์
    ├── app.js           # เรียก API และ render UI
    └── styles.css        # shared styles
```

## การติดตั้ง

### 1. สร้าง PostgreSQL database

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
```

ห้าม commit `.env` และค่าใช้จ่าย OpenAI API แยกจาก ChatGPT subscription

### 3. เริ่ม backend

```powershell
cd Backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

API จะอยู่ที่ `http://127.0.0.1:8000` และดู Swagger UI ได้ที่ `http://127.0.0.1:8000/docs`

### 4. เปิด frontend

เปิดโฟลเดอร์ `Frontend` ผ่าน Live Server แล้วเข้า `http://localhost:5500/home.html`

## การดูแลคลังความรู้

หลังเริ่มใช้งานครั้งแรก ให้แก้ข้อมูลสิทธิใน PostgreSQL เป็นหลัก

1. เพิ่มหรือแก้ `benefits` และปิดสิทธิด้วย `active = false` หากไม่ต้องการให้ค้นพบ
2. แก้ `benefit_documents.content` ให้มีเงื่อนไขที่ชัดเจน พร้อม URL และวันที่อัปเดตจากแหล่งทางการ
3. ตั้ง `embedding = NULL` หลังแก้เนื้อหา เพื่อให้ระบบสร้าง vector ใหม่ในการค้นครั้งถัดไป
4. ทดสอบข้อความหลายลักษณะและให้ผู้เชี่ยวชาญตรวจผลก่อนใช้กับผู้ใช้จริง

## แนวทางพัฒนาต่อ

- เขียน log สำหรับ `/analyze-rights` เพื่อให้ dashboard สะท้อน flow หลัก
- เพิ่ม test set และ evaluation อัตโนมัติสำหรับ RAG และ LLM
- เพิ่ม metadata ของเอกสาร เช่น วันที่มีผลบังคับใช้ จังหวัด และแหล่งราชการ
- เพิ่ม similarity threshold หรือใช้ pgvector เมื่อจำนวนเอกสารมากขึ้น
- เพิ่ม rule checks สำหรับเงื่อนไขที่ตรวจได้แน่นอน โดยยังให้ AI อธิบายกรณีข้อมูลไม่ครบ
