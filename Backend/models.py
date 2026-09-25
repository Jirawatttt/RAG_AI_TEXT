"""Shared domain model used by the RAG matcher and LLM client.

Note: this file previously also defined a structured-profile model
(UserProfile + Nationality/SocialSecurityType/EmploymentStatus/
ChildrenStatus enums) and Benefit.matched_conditions/missing_conditions,
from an earlier design that took a structured form as input and matched
conditions rule-by-rule. The system now takes free-form text and lets the
LLM judge eligibility directly, so none of that was ever imported or read
anywhere — it has been removed. Benefit is now the single model here.
"""

from dataclasses import dataclass


@dataclass
class Benefit:
    name: str
    docs: list[str]
    contact: list[str]
    link: str = ""
    detail: str = ""
    short_description: str = ""
    benefit_details: str = ""
    # เงื่อนไข/สถานะที่ทำให้ "ไม่ได้รับสิทธิ" (เดิมเคยเป็น RAG document chunk
    # ประเภท exclusions/continuity แต่ย้ายมาที่นี่แทน เพราะเนื้อหาเหล่านี้ไม่
    # ได้ช่วยจัดอันดับความใกล้เคียงกับ input ของ user เลย — RAG ควรกรองด้วย
    # คุณสมบัติ/เกณฑ์รายได้เท่านั้น ส่วนการตัดสิน not_eligible จากเงื่อนไข
    # ตัดสิทธิเป็นหน้าที่ของ LLM โดยตรง จึงส่ง field นี้ให้ llm.analyze_rights()
    # เสมอ ไม่ว่า RAG จะจัดอันดับ document chunk ไหนมาให้ก็ตาม)
    disqualifying_conditions: str = ""
    slug: str = ""