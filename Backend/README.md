# Smart Rights Viewer — Backend

Backend สำหรับเว็บแอปที่ช่วยวิเคราะห์ **สิทธิประโยชน์ภาครัฐเบื้องต้น** จากข้อความภาษาไทยแบบอิสระ เช่น อายุ สถานะงาน ประกันสังคม รายได้ สัญชาติ หรือสถานะครอบครัว ระบบค้นหลักฐานจากคลังความรู้ด้วย RAG แล้วให้ LLM เป็นผู้ตัดสินใจว่าสิทธิใดเกี่ยวข้อง — **retrieval ไม่ทำหน้าที่ตัดสิน eligibility เอง**

> ผลลัพธ์เป็นข้อมูลคัดกรองเบื้องต้น ไม่ใช่การรับรองสิทธิหรือผลอนุมัติ หน่วยงานเจ้าของสิทธิเท่านั้นที่ตรวจสอบและยืนยันผลจริงได้

## ความสามารถ

- รับข้อความภาษาไทย 1–4,000 ตัวอักษร รองรับข้อความที่เขียนติดกันไม่เว้นวรรค
- **scope-check เป็น AI-first** — LLM ตัดสินว่าข้อความอยู่ในขอบเขตหรือไม่โดยตรงทุกครั้ง (ไม่ใช่ keyword-first) พร้อม fallback เป็น keyword list เฉพาะตอน LLM call ล้มเหลวจริงๆ
- ใช้ keyword-overlap fallback เมื่อ Embeddings ใช้ไม่ได้ชั่วคราว (คนละจุดกับ scope-check fallback ด้านบน — ดูหัวข้อ "RAG ทำงานอย่างไร")
- จำกัด 20 requests ต่อ IP ต่อ 60 วินาที (in-memory)
- คลังความรู้ปัจจุบันมี 8 สิทธิ; LLM วิเคราะห์เฉพาะ `RAG_LLM_RESULT_LIMIT` อันดับแรกทันที (default `3`) ส่วนที่เหลือเลื่อนไปเป็น `additional_benefits` ให้กดดูเพิ่มทีหลัง — เป็น progressive disclosure จริง ไม่ใช่แค่ค่าที่ตั้งเผื่อไว้ (ดูหัวข้อ "RAG ทำงานอย่างไร")
- ผลลัพธ์ยังไม่ชัดเจน (`needs_verification` หรือมี `follow_up_questions`) ไม่มีฟอร์มแก้ไขในหน้าแสดงผล — ผู้ใช้กด "ตรวจสอบอีกครั้ง" กลับไปหน้ากรอกข้อความ ซึ่งข้อความเดิมจะถูกกรอกไว้ให้อัตโนมัติ แก้ไข/พิมพ์เพิ่มแล้วส่งใหม่ได้เลย (ดูหัวข้อ "Flow ต่อเนื่อง")
- ดูรายการสิทธิที่ถูกเลื่อนไว้ (`additional_benefits`) ได้ทันทีโดยไม่มี embedding เพิ่ม — ดึงตรงจาก DB ด้วย slug
- แดชบอร์ดสถิติมีสรุปเชิงลึกจาก AI (`/stats/insight`) แยกจากตัวเลขดิบ (`/stats`) และมี cache กันเรียก LLM ถี่เกินไป

## หลักการออกแบบสำคัญ

ระบบมี pipeline เดียว รับ **ข้อความอิสระ** เป็น input เท่านั้น
หลักที่ยึดตลอดทั้งระบบ: **retrieval (`rag.py`) มีหน้าที่แค่ "หาและจัดอันดับหลักฐาน" ไม่ใช่ "ตัดสินว่าใครได้สิทธิ"** เพราะระบบไม่มี rule engine — การตัดสิน likely/needs verification/not eligible เป็นหน้าที่ของ LLM (`llm.py`) ล้วนๆ โดยอิงจากหลักฐานที่ retrieval หามาให้เท่านั้น

**ทุกจุดที่ระบบ "ฉลาดขึ้น" ในตอนนี้ (scope-check, query rewrite, การตัดสิน eligibility, สรุป dashboard) มาจาก prompt engineering ล้วนๆ ไม่มี fine-tuning หรือ rule เสริมที่ไหนเลย** — คุณภาพผลลัพธ์ขึ้นกับตัวโมเดล (`OPENAI_MODEL`) และคำสั่งที่เขียนไว้ใน prompt เท่านั้น ผลที่ตามมาคือ **การตัดสินใจของ LLM ไม่ deterministic 100%** ข้อความเดียวกันยิงต่างเวลาอาจได้คำตอบไม่เหมือนกันเป๊ะ (โอกาสน้อยแต่มีจริง) ต่างจาก keyword matching ที่ผลลัพธ์คงที่เสมอ

## สถาปัตยกรรม

### Flow หลัก (`/analyze-rights`)

```text
Frontend (input.html + input.js)
  │ POST /analyze-rights { "text": "..." }
  ▼
FastAPI (main.py): validate ความยาว, rate limit
  ▼
1) rag.prepare_query(text)
   └─ llm.analyze_query()  ── 1 completion เดียว ทำ 2 อย่างพร้อมกัน:
        (a) ตัดสิน in_scope (AI เป็นคนตัดสินตรงๆ ไม่ใช่ keyword)
        (b) แปลง text เป็น search_query สั้นกระชับ (ตัดคำฟุ่มเฟือย/อารมณ์)
      ├─ AI call ล้มเหลว (ไม่มี API key / network) → fallback เป็น
      │    rag.is_rights_query() (keyword) + ใช้ text เดิมเป็น search_query
      ├─ in_scope=false → ตอบ "นอกขอบเขต" ทันที จบตรงนี้ ไม่มี embedding/completion เพิ่ม
      ▼ in_scope=true
2) rag.retrieve_for_text(text, search_query=...)
   ├─ llm.embed_text(search_query)  ── ใช้ query ที่ rewrite แล้วจากขั้น 1 โดยตรง
   │    ไม่มีการ rewrite ซ้ำรอบสอง
   ├─ เทียบ cosine similarity กับเอกสารทุกสิทธิ active
   ├─ scope-floor check: คะแนนดีที่สุดต่ำกว่า SCOPE_FLOOR หรือไม่
   │    ├─ ต่ำกว่า → คืนค่าว่าง ("ข้อมูลไม่พอ")
   │    ▼ ไม่ต่ำกว่า
   └─ คืนทุกสิทธิที่จัดอันดับแล้ว (ไม่มีการกรองทีละตัวอีก)
   ▼
3) main.py แบ่งผลลัพธ์: primary = RAG_LLM_RESULT_LIMIT ตัวแรก (default 3), additional = ส่วนที่เหลือ
   ▼
4) llm.analyze_rights() วิเคราะห์เฉพาะ primary  ── 1 completion
   ├─ เห็น short_description + benefit_details + detail (เนื้อหาเอกสารดิบ
   │    ที่ใช้ embed ก็ถูกส่งมาด้วยตอนนี้ — ดูหมายเหตุด้านล่าง) ของแต่ละสิทธิ
   ├─ AI มีสิทธิ "ไม่พูดถึง" สิทธิที่เห็นว่าไม่เกี่ยวข้องเลย (ต่างจากปฏิเสธชัดเจนที่ต้องลง not_eligible)
   └─ ตัดสิน status: likely_eligible / needs_verification / not_eligible ต่อรายการที่หยิบมาพูดถึง
   ▼
5) main.py ตรวจ source_ids ให้ตรงกับหลักฐานจริง (ไม่ตรง → ตัดรายการนั้นทิ้งเงียบๆ) + บันทึก analytics
   ▼
JSON response (พร้อม additional_benefits ให้กดดูเพิ่มผ่าน /analyze-more-rights) → Frontend
```

**รวม 1 รอบ `/analyze-rights` = 2 completions (`analyze_query` + `analyze_rights`) + 1 embedding**

> `RAG_LLM_RESULT_LIMIT` default เป็น **`3`** (ไม่ใช่เท่ากับขนาดคลังอีกต่อไป) — ตั้งใจให้ prompt/token cost ของ `analyze_rights()` คงที่ไม่ว่าคลังจะโตแค่ไหนในอนาคต (8 วันนี้ 100+ วันหน้า) ผลคือ **`additional_benefits` จะมีรายการจริงแทบทุกครั้ง** (ปกติ ~5 จาก 8 รายการ) ต่างจากตอนที่ limit เท่ากับขนาดคลังพอดีซึ่ง `additional_benefits` จะว่างเปล่าตลอด

### Flow ต่อเนื่อง — แก้ไข/เพิ่มข้อมูล (ไม่มีฟอร์มในหน้าแสดงผล)

หน้าแสดงผล (`result.html`) ไม่มีช่องให้พิมพ์ข้อมูลเพิ่มในหน้าเดียวกันอีกต่อไป — เมื่อผลลัพธ์มี `follow_up_questions` หรือสิทธิที่เป็น `needs_verification` ผู้ใช้ต้องกด **"ตรวจสอบอีกครั้ง"** เพื่อกลับไปหน้ากรอกข้อความ (`input.html`)

```text
result.html: กด "ตรวจสอบอีกครั้ง" (footer, กดได้เสมอไม่ว่าผลจะเป็นแบบไหน)
  ▼
input.html โหลด: DOMContentLoaded อ่าน sessionStorage.getItem('userProfileText')
  ▼
textarea ถูกกรอกด้วยข้อความเดิมที่เคยพิมพ์ไว้ให้อัตโนมัติ (ไม่ต้องพิมพ์ใหม่ทั้งหมด)
  ▼
ผู้ใช้แก้ไข/พิมพ์ต่อท้ายตามคำถามที่เห็นในผลก่อนหน้า → กด "ตรวจสอบสิทธิ"
  ▼
submitForm(): sessionStorage.setItem("userProfileText", text)
              sessionStorage.removeItem("analysis")  ← เคลียร์ผลเก่าทิ้งเสมอ
  ▼
กลับไป result.html → รันทั้ง pipeline ของ /analyze-rights ใหม่หมดตั้งแต่ scope check
```

ตัดสินใจแบบนี้เพื่อไม่ต้องดูแล state 2 ชุดในหน้าเดียว (`userProfileText` ที่เก็บไว้ตอนแรก vs. ค่าที่พิมพ์ในฟอร์ม follow-up) แลกกับผู้ใช้ต้องข้ามหน้า 1 รอบต่อการแก้ไข 1 ครั้งแทนที่จะแก้ในหน้าเดิม

### `/analyze-more-rights` — วิเคราะห์รายการที่ถูกเลื่อนไว้

```text
POST /analyze-more-rights { "text": "...", "benefit_slugs": [...] }
  ▼
ไม่เช็ค scope ซ้ำ (text นี้เพิ่งผ่าน prepare_query() มาแล้วใน /analyze-rights ก่อนหน้า)
  ▼
rag.lookup_benefits_by_slugs(benefit_slugs)
   └─ database.get_benefits_by_slugs()  ── query ตรงด้วย slug + active=True
      ไม่มี embedding, ไม่มี cosine similarity, ไม่มี rewrite_query() เลย
  ▼
llm.analyze_rights() วิเคราะห์เฉพาะรายการที่ดึงมา  ── 1 completion
```

**รวม 1 รอบ `/analyze-more-rights` = 1 completion (`analyze_rights`) + 0 embedding** — เร็วและถูกกว่า `/analyze-rights` มาก เพราะไม่ต้อง rank ใหม่ (slug ที่ frontend ส่งมาคือผลจัดอันดับของรอบ `/analyze-rights` ก่อนหน้าอยู่แล้ว การ rank ซ้ำจะได้ผลเดิม ไม่มีประโยชน์)

> **Trade-off ที่แลกมา**: เดิม endpoint นี้เช็คว่า slug ที่ขอมา "ต้องอยู่ในผลการจัดอันดับของ RAG สำหรับ text นี้จริง" ก่อนอนุญาต ตอนนี้เปลี่ยนมาเช็คแค่ **"slug มีอยู่จริงใน DB และ active"** เท่านั้น (ไม่ผูกกับ text/การจัดอันดับอีกต่อไป) เพราะไม่ได้รัน `retrieve_for_text()` ซ้ำแล้ว จึงไม่มีผลจัดอันดับให้เทียบ ในทางปฏิบัติไม่ใช่ช่องโหว่ร้ายแรง — frontend ส่งแค่ slug จากผลของตัวเองเสมอ ไม่มีข้อมูลอ่อนไหวหลุด — แต่เป็น guarantee ที่เคยมีแล้วถูกสละไปเพื่อแลกความเร็ว ถ้าต้องการ guarantee เดิมกลับมาต้องเก็บ state ของรอบ `/analyze-rights` ก่อนหน้าไว้ (เช่น cache ranked slugs ต่อ session) แล้วเทียบตอนนี้แทนที่จะเชื่อ client เฉยๆ

### `/stats/insight` — สรุปเชิงลึกจาก AI (dashboard เท่านั้น)

```text
GET /stats/insight
  ▼
cache ในหน่วยความจำของ main.py ยังไม่หมดอายุ?
  ├─ ใช่ → คืนค่าจาก cache ทันที ไม่มี LLM call
  ▼ ไม่ (หมดอายุ หรือยังไม่เคย generate)
database.get_stats() + database.get_recent_summaries()
  ▼
llm.generate_stats_insight()  ── 1 completion อ่านสถิติ+ตัวอย่าง ai_response_log
  ▼
เก็บผลลง cache (อายุ INSIGHT_CACHE_SECONDS วินาที) แล้วคืนค่า
```

เรียกจาก `input.html`/`input.js` ทุกครั้งที่เปิดหน้า dashboard — ถ้าไม่มี cache ต้นทุนจะแปรผันตามจำนวนคนเปิดหน้า ไม่ใช่ตามการเปลี่ยนแปลงของข้อมูลจริง จึง cache ไว้เป็นค่า global ระดับ process (ดูข้อจำกัดเรื่อง multi-worker ในหัวข้อ "เฝ้าระวังและ production")

**จุดสำคัญที่มักเข้าใจผิด**: `benefit.detail` (เนื้อหาเอกสารดิบที่ใช้ embed หาความใกล้เคียง) ถูกส่งเข้า prompt ของ `llm.analyze_rights()` ด้วย ไม่ใช่แค่ `short_description`/`benefit_details` — เพราะ `document`/`detail` มักมีเงื่อนไขเชิง verification ที่ชัดกว่า (เช่น เรื่องตรวจทะเบียนบ้าน สถานะบำนาญซ้ำซ้อน) แปลว่าการแก้เงื่อนไขสิทธิให้ LLM เห็น **ต้องแก้ทั้ง 3 ฟิลด์ให้สอดคล้องกัน**: `benefits.short_description`, `benefits.benefit_details`, และ `benefit_documents.content` — แก้แค่ `content` อย่างเดียวยังมีผลกับ embedding ranking เหมือนเดิม แต่ตอนนี้มีผลกับสิ่งที่ LLM เห็นด้วย

## RAG ทำงานอย่างไร

1. Normalize ข้อความ (`normalize_text`) — รองรับข้อความที่เขียนติดกันไม่เว้นวรรค
2. `llm.analyze_query()` ตัดสิน scope โดยตรงด้วย AI (ดูสถาปัตยกรรมด้านบน) — **ไม่ใช่ keyword-first** `is_rights_query()` (คำใน `_RIGHTS_TERMS`) ยังอยู่ในโค้ดแต่เหลือบทบาทแค่ (ก) emergency fallback ตอน AI call ล้มเหลว และ (ข) ตัวช่วย tokenize สำหรับ keyword-ranking fallback ในข้อ 3 ด้านล่าง
3. Embed `search_query` (ที่ rewrite มาจากขั้นก่อน) ด้วย `text-embedding-3-small` แล้วเทียบ cosine similarity กับ embedding ของเอกสารทุกฉบับ (สร้างและ cache ใน `benefit_documents.embedding` เมื่อค้นครั้งแรก) เลือกหลักฐานสูงสุดไม่เกิน 2 ฉบับต่อสิทธิ
4. **`SCOPE_FLOOR`** เช็คแค่ครั้งเดียวกับ**คะแนนสูงสุด**ของทั้ง query — ถ้าแม้แต่สิทธิที่ใกล้เคียงที่สุดยังต่ำกว่าเกณฑ์นี้ แปลว่า query นี้ไม่เกี่ยวกับสิทธิใดในคลังเลยจริงๆ จึงคืนค่าว่าง — **ไม่ใช่การกรองทีละสิทธิ ไม่มีการเก็บ score รายตัวไว้ใช้ต่อ**
5. ถ้าผ่านข้อ 4 → คืน**ทุกสิทธิที่จัดอันดับแล้ว** (สูงสุด `RAG_DISCOVERY_RESULT_LIMIT`) ไม่มีใครถูกตัดทิ้งในขั้นนี้ — การจำกัดว่า LLM จะเห็นกี่รายการเป็นหน้าที่ของ `RAG_LLM_RESULT_LIMIT` ในขั้นถัดไป (ข้อ 7) ไม่ใช่ที่นี่
6. หาก Embeddings ล้มเหลว (คนละกรณีกับ AI scope-check ล้มเหลวในข้อ 2) ใช้ keyword overlap กับ `SCOPE_FLOOR_KEYWORD` แทนในหลักการเดียวกัน คุณภาพการจัดอันดับจะลดลงแต่ยังใช้งานต่อได้ — เส้นทางนี้ใช้ raw normalized text ไม่ใช่ `search_query` ที่ rewrite มา
7. `main.py` ส่งเพียง `RAG_LLM_RESULT_LIMIT` รายการแรก (default **`3`**) ให้ LLM วิเคราะห์ทันที ส่วนที่เหลือ (ปกติ ~5 จาก 8 รายการ) เลื่อนไปเป็น `additional_benefits` ให้ frontend เรียก `/analyze-more-rights` ดูเพิ่มทีหลัง — ตั้งใจกันไว้ล่วงหน้าไม่ให้ prompt โตตามขนาดคลังที่จะขยายในอนาคต
8. LLM ได้รับข้อความผู้ใช้พร้อมชื่อสิทธิ เอกสารที่ต้องใช้ ติดต่อ `short_description`, `benefit_details`, และ `detail` (เอกสารดิบ — ดูหมายเหตุด้านบน) ของสิทธิที่คัดมาเท่านั้น — ห้ามใช้ความรู้ภายนอก และห้ามยืนยันว่าได้รับสิทธิจริงไม่ว่าสถานะใด
9. Backend ตรวจ `source_ids` ที่ LLM ตอบให้ตรงกับหลักฐานจริงก่อนสร้าง `sources` ใน response — ถ้าไม่ตรง รายการนั้นถูกตัดทิ้งทั้งอันโดยไม่มี log แจ้ง

| สถานะ | ความหมาย | เกณฑ์ที่ LLM ใช้ตัดสิน |
| --- | --- | --- |
| `likely_eligible` | ข้อมูลผู้ใช้สอดคล้องกับเงื่อนไขในหลักฐานระดับเบื้องต้น | หลักฐานระบุเงื่อนไข และผู้ใช้ให้ข้อมูลที่เข้าเงื่อนไขนั้น |
| `needs_verification` | สิทธิอาจเกี่ยวข้อง แต่ข้อมูลผู้ใช้หรือหลักฐานยังไม่พอสรุป | ขาดข้อมูลบางส่วน (ไม่ใช่ข้อมูลที่ถูกปฏิเสธไปแล้ว) |
| `not_eligible` | ข้อความผู้ใช้ปฏิเสธเงื่อนไขสำคัญที่หลักฐานกำหนดไว้ชัดเจน | เช่น หลักฐานต้องการสัญชาติไทยแต่ user บอกว่าไม่ใช่คนไทย — กฎ negation บังคับให้จัดสถานะนี้ทันที ไม่ปล่อยเป็น `needs_verification` |

ทั้ง 3 สถานะเป็นการแบ่งที่ **เกิดหลัง RAG คัดกรองมาแล้วเท่านั้น** ไม่มีจุดไหนก่อนหน้านั้นในโค้ดที่ตัดสิน "มี/ไม่มีสิทธิ" และแม้แต่ `not_eligible` เองก็ยังถูกบังคับด้วยพรอมต์ให้เป็นคำ hedge เสมอ (frontend แสดงเป็น "ยังไม่น่ามีสิทธิ**จากข้อมูลที่ระบุ**" ไม่ใช่ "ไม่มีสิทธิ") — ระบบไม่มีทางฟันธงเชิงลบตรงๆ กับผู้ใช้เลยไม่ว่ากรณีใด

ที่ควรรู้เพิ่ม: พรอมต์สั่งให้ LLM "จัดเฉพาะสิทธิที่อาจเกี่ยวข้อง" ลงสถานะ — สิทธิที่ LLM เห็นว่าไม่เกี่ยวข้องเลย (ต่างจากปฏิเสธชัดเจน) จะ**ไม่ถูกพูดถึงในผลลัพธ์เลย** ไม่ใช่ถูกจัดเป็น `not_eligible` พร้อมเหตุผล ถ้ารายการที่ผ่าน RAG มาทั้งหมดตกอยู่ในกลุ่มนี้ ผู้ใช้จะเห็นแค่ข้อความ "ยังไม่พบสิทธิที่มีหลักฐานเพียงพอในฐานข้อมูล" แบบทั่วไป

**ข้อจำกัดสำคัญของ retrieval ที่ต้องรู้ไว้**: cosine similarity จับ "ความใกล้เคียงเชิงหัวข้อ" ไม่ได้เข้าใจการปฏิเสธ (negation) — ข้อความ "ไม่ใช่คนไทย" อาจมีคะแนนใกล้เคียงกับเอกสารที่พูดเรื่องสัญชาติสูงพอๆ กับข้อความ "เป็นคนไทย" เพราะแชร์คำศัพท์เดียวกัน **การตัดสินใจที่ถูกต้องเรื่องนี้จึงต้องพึ่ง LLM + เอกสารที่ระบุเงื่อนไขชัดเจนเท่านั้น ไม่ใช่หน้าที่ของ retrieval** ดังนั้นทั้ง `short_description`/`benefit_details`/`document.content` ของทุกสิทธิในคลังต้องระบุเงื่อนไขสำคัญ (โดยเฉพาะสัญชาติ) ไว้ตรงๆ เสมอ ไม่งั้น LLM จะไม่มีหลักฐานให้ใช้ตัดสิน `not_eligible`

**ข้อจำกัดใหม่ที่มาพร้อม `RAG_LLM_RESULT_LIMIT=3`**: ถ้าสิทธิที่เกี่ยวข้องจริงถูกจัดอันดับต่ำกว่าอันดับ 3 ด้วยเหตุผลของ embedding เอง (เช่น ถ้อยคำใน `document` ไม่ตรงกับที่ผู้ใช้พิมพ์) ผู้ใช้จะไม่เห็นสิทธินั้นในผลแรกเลย ต้องกด "ดูรายการเพิ่มเติม" เอง — ถ้าผู้ใช้ไม่กด ก็จะพลาดสิทธิที่อาจเกี่ยวข้องได้ ต่างจากตอน limit เท่ากับขนาดคลังที่ไม่มีความเสี่ยงนี้เลย

### scope-check ทำงานยังไง

ระบบเช็ค scope ด้วย `llm.analyze_query()` เป็นคนตัดสินโดยตรงทุกครั้ง (ไม่ใช่ keyword-first) ส่วน `is_rights_query()` (keyword list) เหลือบทบาทแค่ตอน AI call เองล้มเหลว (ไม่มี API key / network หลุด / ตอบ JSON ผิดรูปแบบ) เท่านั้น

ข้อจำกัดที่ยังเหลืออยู่จริง:

- **ไม่บอกเหตุผล**: `analyze_query()` คืนแค่ `in_scope: true/false` ไม่มี field อธิบายว่า "นอกเรื่องจริง" หรือ "ข้อความกำกวมอ่านไม่ออก" — ข้อความ error ที่ผู้ใช้เห็น ("...หรือมีคำผิด...") เป็นแค่การเดาเหตุผลที่เป็นไปได้ ไม่ใช่ผลจากการเช็คแยกจริงในโค้ด
- **ย้อนกลับไปใช้ keyword ตอน AI ล่ม**: ถ้า OpenAI ใช้งานไม่ได้ชั่วคราว ระบบจะ fallback กลับไปมีข้อจำกัดแบบ keyword ทันที (แยกพิมพ์ผิด/ภาษาอังกฤษไม่ได้) เพราะไม่มี AI ช่วยตีความแล้ว
- **ไม่ deterministic**: ตามที่อธิบายในหัวข้อ "หลักการออกแบบสำคัญ" — ข้อความเดียวกันมีโอกาสน้อย (แต่ไม่ใช่ศูนย์) ที่จะได้ `in_scope` ไม่ตรงกันคนละรอบ

## ความแม่นยำและข้อจำกัด

โครงการยังไม่มีชุดทดสอบที่เฉลยโดยผู้เชี่ยวชาญหรือหน่วยงานรัฐ จึง **ไม่ควรอ้างเปอร์เซ็นต์ความแม่นยำ** และยังไม่มีค่า Precision, Recall หรือ F1-score ที่ยืนยันได้

- ระบบครอบคลุมเฉพาะสิทธิและเอกสารที่อยู่ในคลังความรู้ (ปัจจุบัน 8 รายการ)
- ผลขึ้นกับความครบถ้วนของข้อความ ความครบถ้วนของเงื่อนไขใน `short_description`/`benefit_details`/`document.content` คุณภาพ retrieval และการตีความของ LLM
- เงื่อนไขจริงจำนวนมากต้องตรวจจากข้อมูลภายนอก เช่น รายได้/ทรัพย์สินครัวเรือน ทะเบียนบ้าน ประวัติประกันสังคม และสิทธิซ้ำซ้อน
- เกณฑ์ราชการเปลี่ยนได้บ่อย (เช่น อัตราส่วนลดของบัตรสวัสดิการแห่งรัฐ หรือเพดานเงินสมทบประกันสังคม) จึงต้องทบทวนคลังอย่างสม่ำเสมอ — **ตัวเลข/วันบังคับใช้เชิงนโยบายที่เพิ่งปรับปรุงในคลังควรตรวจทานกับแหล่งทางการอีกครั้งก่อนเผยแพร่จริง** โดยเฉพาะรายละเอียดที่มีผลย้อนหลังหรือมีผลในอนาคต (เช่น แผนปรับเกณฑ์ที่ยังไม่มีผลบังคับใช้)
- `RAG_LLM_RESULT_LIMIT` (default `3`) ต่ำกว่าขนาดคลัง (8) แปลว่าสิทธิที่จัดอันดับต่ำกว่าอันดับ 3 จะไม่ถูกวิเคราะห์จนกว่าผู้ใช้จะกด "ดูรายการเพิ่มเติม" เอง — ถ้าไม่กด อาจพลาดสิทธิที่เกี่ยวข้องได้
- keyword fallback (ทั้งฝั่ง scope-check และฝั่ง embedding) มีไว้เพื่อความต่อเนื่องตอน AI/embedding ใช้งานไม่ได้ ไม่ใช่ semantic search ที่สมบูรณ์
- embedding-based retrieval ไม่เข้าใจการปฏิเสธ (negation) — พึ่งเอกสารที่ระบุเงื่อนไขชัดเจน + LLM เป็นคนตัดสิน ไม่ใช่ retrieval
- ทุกการตัดสินใจของ AI ในระบบ (scope, rewrite, eligibility, insight) เป็นผลจาก prompt engineering ล้วนๆ ไม่ deterministic 100% — ดูหัวข้อ "หลักการออกแบบสำคัญ"
- `/analyze-more-rights` เช็คแค่ว่า slug มีอยู่จริงใน DB เท่านั้น ไม่ได้ยืนยันว่า slug นั้นผ่านการจัดอันดับของ RAG สำหรับ text นี้จริง (ดูหัวข้อ "Trade-off" ใน `/analyze-more-rights`)
- rate limit และ `/stats/insight` cache ไม่แชร์ข้าม worker และหายเมื่อ restart (ดูหัวข้อ "เฝ้าระวังและ production")
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

หากข้อความนอกขอบเขต (LLM หรือ keyword fallback ตัดสินว่า `in_scope=false`) หรือไม่มีสิทธิใดผ่าน `SCOPE_FLOOR` เลย API ตอบ `200` พร้อม `benefits: []` และคำแนะนำ โดยไม่เรียก `analyze_rights()`

ด้วย `RAG_LLM_RESULT_LIMIT=3` ปกติจะมี `additional_benefits` ติดมาด้วยแทบทุกครั้ง (~5 รายการจาก 8) ให้กด "ดูรายการเพิ่มเติม" เรียก `/analyze-more-rights` ต่อ

ถ้าผลลัพธ์ยังไม่ชัดเจน (`needs_verification` หรือมี `follow_up_questions`) ไม่มีฟอร์มแก้ไขในหน้าผลลัพธ์ — ผู้ใช้กด "ตรวจสอบอีกครั้ง" กลับไปหน้ากรอกข้อความซึ่งพรีฟิลข้อความเดิมไว้ให้ (ดูหัวข้อ "Flow ต่อเนื่อง")

### `POST /analyze-more-rights`

วิเคราะห์เฉพาะ `slug` ที่คืนใน `additional_benefits` จากผลก่อนหน้า (มีรายการจริงแทบทุกครั้งตอนนี้ที่ `RAG_LLM_RESULT_LIMIT=3` < ขนาดคลัง) — **ไม่เช็ค scope ซ้ำ** และ**ไม่รัน retrieval ซ้ำ** ดึงตรงจาก DB ด้วย slug แทน (ดูหัวข้อ "สถาปัตยกรรม")

```json
{ "text": "อายุ 65 ปี สัญชาติไทย ว่างงาน", "benefit_slugs": ["universal_health"] }
```

response ใช้ schema เดียวกับ `/analyze-rights` และ `additional_benefits` จะว่าง

### `GET /stats`

```json
{ "total_inquiries": 0, "avg_benefits": 0, "avg_ai_response_ms": 0, "top_benefits": [] }
```

ตัวเลขดิบล้วน ไม่มี LLM call เลย เร็วและไม่มีวันพังเพราะ OpenAI

### `GET /stats/insight`

```json
{ "insight": "ช่วงนี้มีคนถามเรื่องบัตรสวัสดิการแห่งรัฐและประกันสังคมมากที่สุด..." }
```

สรุปเชิงลึกจาก AI อ่านจาก `top_benefits`/สถิติรวม + ตัวอย่าง `ai_response_log` ล่าสุด (ไม่มี PII) — **มี cache ในหน่วยความจำ** อายุ `INSIGHT_CACHE_SECONDS` วินาที (default 900 = 15 นาที) เรียกกี่ครั้งในช่วงเวลานั้นก็ได้ค่าเดิม ไม่มี LLM call ซ้ำ ต่อเมื่อ cache หมดอายุถึงจะ generate ใหม่

| HTTP | กรณี |
| --- | --- |
| `422` | validation ไม่ผ่าน, ข้อความว่าง/เกิน 4,000 ตัวอักษร หรือ slug ที่ขอใน `/analyze-more-rights` ไม่พบในฐานข้อมูล |
| `429` | เกิน 20 requests ต่อ IP ภายใน 60 วินาที |
| `503` | ไม่มี `OPENAI_API_KEY` ตอนต้องเรียก Embeddings หรือ LLM |
| `502` | RAG หรือ AI วิเคราะห์ไม่สำเร็จ |
| `500` | ดึงสถิติจากฐานข้อมูลไม่สำเร็จ หรือสร้าง insight ไม่สำเร็จ |

## ฐานข้อมูล

ใช้ PostgreSQL ผ่าน SQLAlchemy async และ `asyncpg`; ระบบสร้างตารางเมื่อเริ่มแอป

| ตาราง | หน้าที่ | ใครอ่านใช้ |
| --- | --- | --- |
| `benefits` | สิทธิหลัก: slug, หมวด, เอกสาร, ติดต่อ, URL, `short_description`, `benefit_details`, `active` | `llm.analyze_rights()` อ่านโดยตรง; `database.get_benefits_by_slugs()` ใช้ทำ direct lookup ให้ `/analyze-more-rights` |
| `benefit_documents` | หลักฐานดิบสำหรับ RAG: title, `content`, URL, `embedding`, `active` | `content`/`embedding` ใช้จัดอันดับความใกล้เคียง **และ** `content` ถูกส่งต่อให้ LLM อ่านด้วย (ผ่านฟิลด์ `Benefit.detail` — ดูหมายเหตุด้านล่าง) |
| `inquiry_log` | `{"text_length": ...}` และรายชื่อสิทธิที่แสดง | analytics (`/stats`) |
| `ai_response_log` | `{"text_length": ...}`, สิทธิ, summary/response และเวลา AI ตอบ (ms) | analytics (`/stats`, `/stats/insight`) |

**ความสัมพันธ์ระหว่าง field กับสิ่งที่ LLM เห็นจริง (สำคัญตอนแก้เงื่อนไขสิทธิ):**

```text
benefits.short_description ─┐
benefits.benefit_details    ├─► ส่งเข้า prompt ของ llm.analyze_rights() โดยตรง
benefit_documents.content ──┘   (เข้าทาง Benefit.detail — ต่อ document สูงสุด 2 ฉบับต่อสิทธิ)

benefit_documents.content   ─────► (เส้นทางที่สอง) ใช้คำนวณ embedding เพื่อจัดอันดับสิทธิ (retrieve_for_text)

benefit_documents.embedding ─────► cosine similarity ใน retrieve_for_text() (สร้าง/cache อัตโนมัติเมื่อค้นครั้งแรก)
```

`benefit_documents.content` ตอนนี้**ทำ 2 หน้าที่พร้อมกัน**: (1) ตัวที่ embed เพื่อจัดอันดับ และ (2) เนื้อหาที่ LLM เห็นตรงๆ ผ่าน `Benefit.detail` — แปลว่าแก้ไขคอลัมน์นี้มีผลทั้งสองทาง ต้องเขียนให้เหมาะทั้งการค้นหา (สั้น ตรงประเด็น มีคำที่ user น่าจะพิมพ์) และการเป็นหลักฐานเชิงเงื่อนไข (ระบุเกณฑ์ชัดเจน) ไปพร้อมกัน — ถ้าเขียนไม่ดีอาจกระทบทั้งคุณภาพการค้นหาและคุณภาพการตัดสิน eligibility พร้อมกัน

เมื่อฐานข้อมูลว่าง `rag_catalog.py` จะ seed สิทธิเริ่มต้น 8 รายการเพียงครั้งเดียว: เบี้ยยังชีพผู้สูงอายุ เบี้ยความพิการ สวัสดิการแห่งรัฐ ประกันสังคมมาตรา 33/39/40 เงินอุดหนุนเด็กแรกเกิด และบัตรทอง **การแก้ `rag_catalog.py` ภายหลังจะไม่เขียนทับข้อมูลใน DB ที่ seed ไปแล้ว** — ต้อง `UPDATE` ผ่าน SQL โดยตรง หรือลบ record เดิมแล้วให้ seed ใหม่เท่านั้น

## โครงสร้าง

```text
Backend/
├── main.py         # FastAPI routes (/health, /analyze-rights, /analyze-more-rights, /stats, /stats/insight), schemas, CORS, rate limiting, insight cache
├── rag.py          # retrieval, cosine similarity, scope-floor gate, prepare_query() (AI scope+rewrite), lookup_benefits_by_slugs() (direct slug lookup), keyword fallback
├── llm.py          # OpenAI Embeddings / Chat Completions และ prompt ทั้งหมด (analyze_query, rewrite_query, analyze_rights, generate_stats_insight)
├── database.py     # models, lifecycle, seed, analytics, get_recent_summaries, get_benefits_by_slugs
├── rag_catalog.py  # ข้อมูลเริ่มต้นเมื่อ database ว่าง (seed ครั้งเดียว)
├── models.py       # domain model เดียว (Benefit)
├── run.py          # Uvicorn development server
├── requirements.txt
└── README.md

Frontend/
├── home.html       # หน้าแรก
├── input.html      # ฟอร์มกรอกข้อความอิสระ + แดชบอร์ดสถิติ + สรุปเชิงลึกจาก AI + พรีฟิลข้อความเดิมเมื่อย้อนกลับมาแก้ไข
├── result.html     # หน้าแสดงผลวิเคราะห์ (ไม่มีฟอร์มแก้ไขข้อมูลในหน้านี้แล้ว)
├── app.js          # ค่ากลางที่ใช้ร่วมกัน (API_BASE) เท่านั้น
├── input.js        # logic เฉพาะ input.html (submit, dashboard, insight)
└── result.js       # logic เฉพาะ result.html (เรียก /analyze-rights, /analyze-more-rights, render ผล)
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

# จำนวนสิทธิสูงสุดที่ส่งให้ LLM วิเคราะห์ทันที (ที่เหลือเลื่อนไป additional_benefits)
# ต่ำกว่านี้ = token cost ต่อรอบถูกลง แต่ additional_benefits จะมีรายการบ่อยขึ้น
# สูงกว่านี้ (เท่ากับขนาดคลัง) = ไม่มี additional_benefits เหลือเลย แต่ prompt โตตามคลัง
RAG_LLM_RESULT_LIMIT=3

# อายุ cache ของ /stats/insight เป็นวินาที — ไม่ใส่บรรทัดนี้จะใช้ default 900 (15 นาที)
# แก้แล้วต้อง restart server ค่าใหม่ถึงจะมีผล (อ่านตอน main.py เริ่มรันครั้งเดียว)
INSIGHT_CACHE_SECONDS=900
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

1. เพิ่ม/แก้ `benefits.short_description`, `benefits.benefit_details`, **และ** `benefit_documents.content` ให้สอดคล้องกันเสมอเพื่อปรับเงื่อนไขที่ LLM มองเห็น (ทั้ง 3 ฟิลด์นี้ถูกส่งเข้า prompt แล้วตอนนี้ — ดูหัวข้อฐานข้อมูล) — **ทุกเงื่อนไขคัดออกที่สำคัญ (โดยเฉพาะสัญชาติ) ต้องเขียนตรงๆ อย่างน้อยในฟิลด์ใดฟิลด์หนึ่ง** ไม่งั้น LLM จะไม่มีหลักฐานตัดสิน `not_eligible`
2. `benefit_documents.content` มีอิทธิพลต่อ**ทั้งการจัดอันดับความเกี่ยวข้องและสิ่งที่ LLM เห็น** พร้อมกัน — เขียนให้กระชับ ตรงประเด็น และมีทั้งคำที่ผู้ใช้น่าจะพิมพ์ (ช่วย embedding) กับเงื่อนไขที่ชัดเจน (ช่วย LLM ตัดสิน)
3. ตั้ง `embedding = NULL` หลังแก้ `content` เพื่อให้ระบบสร้าง vector ใหม่ครั้งถัดไป
4. ตั้ง `active = false` สำหรับสิทธิหรือเอกสารที่ไม่ต้องการให้ค้นพบ แทนการลบประวัติ
5. ทดสอบทั้งเคสปกติและเคส**ปฏิเสธเงื่อนไข** (เช่น "ไม่ใช่คนไทย", "ไม่มีบุตร") ก่อนเผยแพร่การเปลี่ยนแปลง เพื่อยืนยันว่า LLM จัดเป็น `not_eligible` ถูกต้อง ไม่ใช่ `needs_verification`
6. **ตัวเลข/เกณฑ์เชิงนโยบายที่เปลี่ยนบ่อย** (ส่วนลดของบัตรสวัสดิการแห่งรัฐ เพดานเงินสมทบประกันสังคม แผนปรับเกณฑ์ในอนาคต ฯลฯ) ควรมีวันที่ตรวจทานล่าสุดกำกับไว้ในเนื้อหาเอง (เช่น "ตามเกณฑ์ที่ปรับปีXXXX") และตรวจทานกับแหล่งทางการซ้ำเป็นระยะ — โดยเฉพาะก่อนนำไปเสนอหรือ demo

### เฝ้าระวังและ production

- ตรวจ `/health` และ `/stats` เพื่อติดตามสถานะ ปริมาณงาน เวลา AI ตอบ และสิทธิที่พบบ่อย
- ติดตาม log สำหรับข้อผิดพลาด OpenAI, PostgreSQL และ `429`
- `RAG_SCOPE_FLOOR` ปรับจาก test set ที่มี label: ลดเกณฑ์เพิ่ม recall แต่เสี่ยงให้ query นอกเรื่องหลุดผ่าน; เพิ่มเกณฑ์มีผลตรงกันข้าม — ปรับเฉพาะจุดนี้จุดเดียว ไม่ต้อง tune ต่อสิทธิ
- ปรับ `RAG_LLM_RESULT_LIMIT` เป็น trade-off ระหว่าง token cost กับโอกาสที่ผู้ใช้จะพลาดสิทธิที่ไม่ได้กดดูเพิ่ม — ทดสอบกับ query จริงก่อนตั้งค่าถาวร
- **`_insight_cache` เก็บใน memory ของ process เดียว** — restart server แล้ว cache หาย ต้อง generate ใหม่รอบแรก; ถ้า deploy แบบหลาย worker/instance แต่ละตัวมี cache แยกกันเอง (ไม่ share) ทำให้ยิง LLM ได้มากกว่า 1 ครั้งต่อ `INSIGHT_CACHE_SECONDS` จริง — ถ้าจะ scale ออกหลาย process ค่อยย้าย cache ไปเก็บที่ Redis หรือ DB แทน
- หลาย worker/instance ควรย้าย rate limit ไป Redis หรือ API gateway เช่นเดียวกับ insight cache ด้านบน และเก็บ secret ใน secret manager
- สำรองฐานข้อมูล จำกัดสิทธิ์บัญชี PostgreSQL และทบทวน URL/เกณฑ์สิทธิเป็นรอบ

## แนวทางพัฒนาต่อ

- พิจารณาคืนความสามารถแก้ไข/เพิ่มข้อมูลในหน้า `result.html` เอง (conversational follow-up) ถ้าอยากลด friction ของการย้อนกลับไป `input.html` ทุกครั้งที่ต้องแก้ไขข้อมูล — ตอนนี้ตัดออกไปแล้วโดยตั้งใจเพื่อความเรียบง่ายของ state (ดูหัวข้อ "Flow ต่อเนื่อง")
- ถ้าต้องการ guarantee เดิมของ `/analyze-more-rights` กลับมา (slug ต้องผ่านการจัดอันดับ RAG ของ text นี้จริง ไม่ใช่แค่มีอยู่ใน DB) ต้องเก็บ state ของรอบ `/analyze-rights` ก่อนหน้าไว้ต่อ session แล้วเทียบตอนเรียกซ้ำ
- เพิ่ม field เหตุผล (เช่น `reason`) ใน `llm.analyze_query()` เพื่อแยกแยะ "นอกเรื่องจริง" กับ "ข้อความกำกวม/พิมพ์ผิดจนอ่านไม่ออก" แทนที่จะรวมเป็นข้อความ "นอกขอบเขต" เดียวกันหมด — ช่วยทั้ง debug และข้อความที่โชว์ผู้ใช้ให้ตรงสาเหตุจริง
- ย้าย `_insight_cache`/rate limit ไป Redis หรือ store กลางก่อน deploy แบบหลาย worker/instance
- รวมข้อความ `coverage_warning`/`summary` fallback ที่กระจายอยู่ `main.py` และ `llm.py` ไว้เป็นค่าคงที่ส่วนกลาง (เช่นไฟล์ `messages.py`) เพื่อคุมโทนให้ตรงกันง่ายขึ้น
- เพิ่ม Alembic migrations แทน `create_all`/`ALTER TABLE` ระหว่าง startup
- เพิ่ม metadata เอกสาร: วันที่มีผลบังคับใช้ จังหวัด หน่วยงาน และวันที่ตรวจทาน (ช่วยแก้ปัญหาตัวเลขเชิงนโยบายที่เปลี่ยนบ่อยตามหัวข้อ "คลังความรู้" ด้านบน)
- เพิ่ม automated evaluation สำหรับ retrieval, การเลือกสถานะ (โดยเฉพาะเคส negation), scope-check accuracy และความถูกต้องของแหล่งอ้างอิง
- ใช้ `pgvector` หรือ vector store เมื่อจำนวนเอกสารเพิ่มขึ้นจนไม่เหมาะกับการเทียบ cosine ทีละรายการแบบตอนนี้
- เพิ่มการ redaction/ตรวจจับข้อมูลอ่อนไหวก่อนเรียก AI
- ทำสคริปต์ QA แยกต่างหาก (ไม่ผูกกับ user flow) ให้ LLM ช่วยตรวจความสอดคล้องภายในของ `short_description`/`benefit_details`/`document.content` ต่อสิทธิเดียวกัน ก่อนเผยแพร่การแก้ไขคลังความรู้แต่ละรอบ