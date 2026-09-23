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
    slug: str = ""