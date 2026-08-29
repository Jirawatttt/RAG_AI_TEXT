# Smart Rights Viewer — Backend

Backend สำหรับระบบแนะนำสิทธิประโยชน์ภาครัฐเบื้องต้น โดยใช้ RAG และ OpenAI

ระบบนี้ไม่มี rule engine ที่ตัดสินสิทธิแบบตายตัว ผลลัพธ์เป็นคำแนะนำเบื้องต้นเท่านั้น ผู้ใช้ต้องตรวจสอบกับหน่วยงานที่เกี่ยวข้องก่อนยืนยันสิทธิ

## การทำงาน

```text
ข้อมูลจากฟอร์ม
  → สร้าง retrieval query
  → Embedding ค้นเอกสารสิทธิใน PostgreSQL
  → ส่งข้อมูลผู้ใช้ + หลักฐาน RAG ให้ LLM
  → LLM เลือกสิทธิที่อาจเกี่ยวข้องและอธิบายผล
```
[1.home](/assets/0.png)<br>

[2.input](/assets/1.png)<br>

[3.select input](/assets/2.png)<br>

[4.RAG+AI Process](/assets/3.png)<br>

[5.result](/assets/4.png)<br>


- `text-embedding-3-small` แปลงข้อความเป็น vector เพื่อค้นเอกสารที่มีความหมายใกล้เคียง
- `gpt-5.6-luna` อ่านหลักฐานที่ค้นได้และเขียนคำแนะนำภาษาไทย
- ถ้า Embedding API ใช้ไม่ได้ชั่วคราว ระบบใช้ keyword fallback เพื่อจัดลำดับเอกสาร แต่จะไม่สามารถสร้างคำอธิบาย AI ได้หาก LLM API ใช้ไม่ได้

## โครงสร้างไฟล์

```text
Backend/
├── main.py          # FastAPI routes และ validation ของข้อมูลจากฟอร์ม
├── rag.py           # Retrieval: query, embeddings และจัดลำดับเอกสาร
├── llm.py           # เรียก OpenAI เพื่อ embedding และสร้างคำตอบ
├── database.py      # PostgreSQL models, lifecycle, logging และ RAG catalogue access
├── rag_catalog.py   # ข้อมูลตั้งต้นสำหรับ seed DB ว่าง
├── models.py        # Domain models ที่ใช้ร่วมกัน
├── run.py           # คำสั่งเริ่ม FastAPI บน localhost:8000
├── requirements.txt # Python dependencies
└── README.md
```

Frontend ที่เรียก API นี้อยู่ใน `../Frontend/`

## ฐานข้อมูล

| ตาราง | หน้าที่ |
| --- | --- |
| `benefits` | ชื่อสิทธิ หมวดหมู่ เอกสาร ช่องทางติดต่อ ลิงก์ และสถานะเปิดใช้งาน |
| `benefit_documents` | เนื้อหาอ้างอิงของสิทธิและ vector embedding |
| `inquiry_log` | ข้อมูลฟอร์มและ candidate ที่ถูกค้นคืน สำหรับสถิติ |
| `ai_response_log` | คำตอบ AI และเวลาในการตอบ สำหรับตรวจสอบระบบ |

ระบบจะ seed ข้อมูลจาก `rag_catalog.py` เฉพาะเมื่อ `benefits` ยังว่าง จึงไม่ทับข้อมูลที่แก้ใน DB แล้ว

> หากมีตาราง `benefit_conditions` จากระบบเวอร์ชันเก่า ตารางนั้นเป็น legacy และไม่ถูกอ่านหรือเขียนโดยโค้ดปัจจุบัน

## การตั้งค่า

สร้างไฟล์ `Backend/.env` (ห้าม commit ไฟล์นี้):

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/rights_db
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
ALLOWED_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
```

OpenAI API billing แยกจาก ChatGPT subscription ต้องมีเครดิต API ที่ใช้งานได้เพื่อให้ Embedding และ LLM ทำงาน

## เริ่มต้นใช้งาน

1. สร้างฐานข้อมูล PostgreSQL:

```sql
CREATE DATABASE rights_db;
```

2. ติดตั้ง dependencies และเริ่ม backend:

```powershell
cd Backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

หรือใช้:

```powershell
uvicorn main:app --reload
```

3. เปิด `Frontend/home.html` ผ่าน Live Server แล้วเข้า `http://localhost:5500/home.html`

## API

| Method | Endpoint | หน้าที่ |
| --- | --- | --- |
| `GET` | `/health` | ตรวจว่า backend ทำงานอยู่ |
| `POST` | `/check-rights` | ค้น candidate และหลักฐาน RAG สำหรับหน้าเว็บ |
| `POST` | `/explain` | ให้ LLM เลือกและอธิบายสิทธิ โดยส่งกลับแบบ SSE |
| `GET` | `/stats` | ดูสถิติการใช้งาน |

ตัวอย่าง request:

```json
{
  "age": 65,
  "nationality": "thai",
  "social_security": "none",
  "employment": "unemployed",
  "children": "0",
  "disability": "no"
}
```

ดูเอกสาร API แบบ interactive ได้ที่ `http://localhost:8000/docs` หลังเริ่ม backend

## การเพิ่มหรือแก้ข้อมูลสิทธิ

หลังจาก DB ถูก seed แล้ว ให้แก้ที่ DB เป็นหลัก:

1. เพิ่มหรือแก้ข้อมูลหลักใน `benefits`
2. เพิ่ม/แก้ `benefit_documents.content` เป็นข้อมูลอ้างอิงที่ชัดเจนและเชื่อถือได้
3. ปล่อย `embedding` เป็น `NULL` เมื่อต้องการให้ระบบสร้าง vector ใหม่ในการค้นครั้งถัดไป
4. ตั้ง `benefits.active = false` เพื่อหยุดให้สิทธินั้นปรากฏ โดยไม่ต้องลบข้อมูล

ยิ่งเนื้อหาใน `benefit_documents` ถูกต้องและมีที่มาดี การแนะนำจาก RAG ก็ยิ่งน่าเชื่อถือ

## ข้อควรระวัง

- อย่าใส่ API key ใน frontend หรือ commit `.env`
- AI เป็นผู้แนะนำ ไม่ใช่ผู้ยืนยันสิทธิ
- เมื่อแก้ข้อมูลสิทธิสำคัญ ควรทดสอบด้วย input หลายรูปแบบและยืนยันกับแหล่งข้อมูลราชการ
