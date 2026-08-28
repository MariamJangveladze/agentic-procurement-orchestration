"""Typed, validated data contracts for the procurement showcase."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_INFORMATION = "request_information"


class ReviewRole(StrEnum):
    FINANCE = "finance"
    LEGAL = "legal"


class ProcurementStatus(StrEnum):
    DRAFT = "draft"
    PENDING_HEAD_APPROVAL = "pending_head_approval"
    PENDING_LOGISTICS_PREPARATION = "pending_logistics_preparation"
    OUT_OF_BUDGET = "out_of_budget"
    BUDGET_SOURCE_UNAVAILABLE = "budget_source_unavailable"
    PENDING_LOGISTICS_AUTHORIZATION = "pending_logistics_authorization"
    PENDING_CONTROL_REVIEWS = "pending_control_reviews"
    PENDING_LOGISTICS_REWORK = "pending_logistics_rework"
    PENDING_CEO_APPROVAL = "pending_ceo_approval"
    PENDING_TENDER_PREPARATION = "pending_tender_preparation"
    TENDER_PREPARED = "tender_prepared"
    PENDING_BOARD_FLOW_CONFIGURATION = "pending_board_flow_configuration"
    PENDING_AGREEMENT_REVIEW = "pending_agreement_review"
    AWAITING_DELIVERY = "awaiting_delivery"
    PENDING_REQUESTER_ACCEPTANCE = "pending_requester_acceptance"
    PENDING_SIGNED_ACT = "pending_signed_act"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ProcurementRequest(BaseModel):
    procurement_type: Literal["material", "service"]
    subcategory: str
    description: str = Field(min_length=10)
    deadline: str
    requester_name: str
    requester_department: str


class SupplierResearch(BaseModel):
    supplier_name: str = Field(min_length=2)
    is_existing_supplier: bool
    estimated_cost_gel: float = Field(gt=0)
    offer_reference: str = Field(min_length=2)
    notes: str = ""


class BudgetSnapshot(BaseModel):
    department: str
    month: str
    allocated_gel: float = Field(ge=0)
    committed_gel: float = Field(ge=0)
    available_gel: float = Field(ge=0)
    checked_at: str
    source: str = "demo-budget-adapter"


class ReviewDecision(BaseModel):
    role: ReviewRole
    decision: Decision
    comment: str = ""
    round_number: int
    decided_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AuditEvent(BaseModel):
    event: str
    actor: str
    detail: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
