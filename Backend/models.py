"""Shared domain models used by the API, RAG matcher, and LLM client."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Nationality(str, Enum):
    THAI = "thai"
    OTHER = "other"


class SocialSecurityType(str, Enum):
    SEC_33 = "33"
    SEC_39 = "39"
    SEC_40 = "40"
    NONE = "none"


class EmploymentStatus(str, Enum):
    EMPLOYED = "employed"
    SELF = "self"
    FARMER = "farmer"
    UNEMPLOYED = "unemployed"


class ChildrenStatus(str, Enum):
    NONE = "0"
    YOUNG = "1"
    GROWN_UP = "2"


@dataclass
class UserProfile:
    age: Optional[int] = None
    nationality: Optional[Nationality] = None
    social_security: Optional[SocialSecurityType] = None
    employment: Optional[EmploymentStatus] = None
    children: Optional[ChildrenStatus] = None
    has_disability_card: Optional[bool] = None


@dataclass
class Benefit:
    name: str
    matched_conditions: list[str]
    missing_conditions: list[str]
    docs: list[str]
    contact: list[str]
    link: str = ""
    detail: str = ""
