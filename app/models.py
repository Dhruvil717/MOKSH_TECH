"""
Data models shared across the pipeline. Using Pydantic here does double duty:
it documents the exact shape we expect, and it lets us validate/repair the
LLM's JSON output instead of trusting it blindly.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class LeadStatus(str, Enum):
    PENDING = "pending"
    RESEARCHING = "researching"
    ANALYZING = "analyzing"
    DONE = "done"
    ERROR = "error"


class SalesBrief(BaseModel):
    """The structured output the sales team actually reads."""

    company_overview: str = Field(..., description="2-4 sentences on what the company does")
    core_product_or_service: str = Field(..., description="The primary product/service offered")
    target_customer: str = Field(..., description="Who the company sells to")
    is_b2b_qualified: bool = Field(..., description="True if this looks like a relevant B2B lead")
    qualification_reason: str = Field(
        ..., description="One or two sentences justifying the B2B yes/no decision"
    )
    sales_questions: List[str] = Field(
        ..., min_length=3, max_length=3, description="Exactly 3 discovery-call questions"
    )
    confidence: str = Field(
        default="medium", description="low/medium/high - how much usable content was found"
    )

    @field_validator("sales_questions")
    @classmethod
    def _questions_not_empty(cls, v: List[str]) -> List[str]:
        cleaned = [q.strip() for q in v if q and q.strip()]
        if len(cleaned) != 3:
            raise ValueError("sales_questions must contain exactly 3 non-empty questions")
        return cleaned


class LeadRecord(BaseModel):
    """Full row as stored/returned by the API - the brief plus pipeline metadata."""

    id: int
    raw_input: str
    resolved_url: Optional[str] = None
    status: LeadStatus = LeadStatus.PENDING
    error_message: Optional[str] = None
    pages_scraped: List[str] = Field(default_factory=list)
    brief: Optional[SalesBrief] = None
    created_at: str
    updated_at: str
