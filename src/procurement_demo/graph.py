"""LangGraph case workflow with explicit human decision interrupts."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from .agents import ProcurementAgents
from .budget import BudgetProvider, BudgetSourceError, FixedBudgetProvider, GoogleSheetsBudgetProvider
from .knowledge import configured_knowledge_base
from .models import (
    AuditEvent,
    BudgetSnapshot,
    Decision,
    ProcurementRequest,
    ProcurementStatus,
    ReviewDecision,
    ReviewRole,
    SupplierResearch,
    utc_now,
)
from .observability import trace_config


class CaseState(TypedDict, total=False):
    case_id: str
    status: str
    request: dict[str, Any]
    research: dict[str, Any]
    budget: dict[str, Any]
    supplier_evidence: str
    review_pack: str
    agreement_draft: str
    review_round: int
    review_decisions: Annotated[list[dict[str, Any]], operator.add]
    audit_events: Annotated[list[dict[str, Any]], operator.add]
    model_used: bool


REVIEWERS: tuple[ReviewRole, ...] = (
    ReviewRole.FINANCE,
    ReviewRole.LEGAL,
)


def _event(event: str, actor: str, detail: str) -> list[dict[str, str]]:
    return [AuditEvent(event=event, actor=actor, detail=detail).model_dump()]


def _command_decision(payload: object) -> tuple[Decision, str]:
    if not isinstance(payload, dict):
        return Decision.REJECT, "Invalid human decision payload."
    try:
        decision = Decision(payload.get("decision", Decision.REJECT))
    except ValueError:
        decision = Decision.REJECT
    return decision, str(payload.get("comment", ""))


def _request(state: CaseState) -> ProcurementRequest:
    return ProcurementRequest.model_validate(state["request"])


def _research(state: CaseState) -> SupplierResearch:
    return SupplierResearch.model_validate(state["research"])


def _approval_band(estimated_cost_gel: float) -> str:
    """Classify using the organization's inclusive approval boundaries."""

    if estimated_cost_gel < 500:
        return "under_500"
    if estimated_cost_gel < 5000:
        return "500_to_4999"
    if estimated_cost_gel < 20000:
        return "5000_to_19999"
    return "board_required"


def build_graph(
    agents: ProcurementAgents | None = None,
    budget_provider: BudgetProvider | None = None,
    *,
    checkpointer: Any | None = None,
):
    """Build the authoritative workflow graph for one procurement case.

    The deployed API supplies a Postgres checkpointer. In-memory persistence is
    retained only for unit tests and the local UI-only demo.
    """

    agents = agents or ProcurementAgents(configured_knowledge_base())
    budget_provider = budget_provider or GoogleSheetsBudgetProvider.from_environment()

    def register_request(state: CaseState) -> dict[str, Any]:
        request = _request(state)
        return {
            "status": ProcurementStatus.PENDING_HEAD_APPROVAL.value,
            "review_round": state.get("review_round", 1),
            "audit_events": _event(
                "request_registered",
                request.requester_name,
                f"{request.procurement_type}/{request.subcategory}; amount to be estimated by Logistics",
            ),
        }

    def head_approval(state: CaseState) -> Command[Literal["logistics_preparation", "finish"]]:
        request = _request(state)
        response = interrupt(
            {
                "kind": "head_approval",
                "title": "Head of Department approval required",
                "request": request.model_dump(),
                "allowed_decisions": [Decision.APPROVE.value, Decision.REJECT.value],
            }
        )
        decision, comment = _command_decision(response)
        if decision == Decision.APPROVE:
            return Command(
                update={
                    "status": ProcurementStatus.PENDING_LOGISTICS_PREPARATION.value,
                    "audit_events": _event("head_approved", "head_of_department", comment or "Approved"),
                },
                goto="logistics_preparation",
            )
        return Command(
            update={
                "status": ProcurementStatus.CANCELLED.value,
                "audit_events": _event("head_rejected", "head_of_department", comment or "Rejected"),
            },
            goto="finish",
        )

    def logistics_preparation(state: CaseState) -> Command[Literal["budget_check"]]:
        request = _request(state)
        response = interrupt(
            {
                "kind": "logistics_preparation",
                "title": "Logistics supplier research required",
                "request": request.model_dump(),
                "allowed_decisions": ["submit_research"],
                "required_fields": ["supplier_name", "is_existing_supplier", "estimated_cost_gel", "offer_reference", "notes"],
            }
        )
        research = SupplierResearch.model_validate(response.get("research", response))
        agent_result = agents.prepare_supplier_evidence(request, research, thread_id=state["case_id"])
        return Command(
            update={
                "research": research.model_dump(),
                "supplier_evidence": agent_result.content,
                "model_used": agent_result.used_model,
                "audit_events": _event(
                    "logistics_research_submitted",
                    "logistics",
                    f"{research.supplier_name}; estimated {research.estimated_cost_gel:,.2f} GEL",
                ),
            },
            goto="budget_check",
        )

    def budget_check(state: CaseState) -> Command[Literal["logistics_authorization", "logistics_preparation"]]:
        request, research = _request(state), _research(state)
        month = str(state.get("budget", {}).get("month", utc_now()[:7]))
        # A test may inject an explicit amount; normal cases use the Sheet. Key
        # this off the injection marker, never off a field this node writes
        # itself: the rework loop re-enters budget_check, and inferring the
        # override from `available_gel` would freeze the budget at the first
        # reading instead of re-reading the authoritative source.
        injected = state.get("budget") or {}
        overridden = injected.get("source") == "test-budget-override"
        try:
            snapshot = (
                FixedBudgetProvider(float(injected["available_gel"]))
                .monthly_budget(department=request.requester_department, month=month)
                .model_copy(update={"source": "test-budget-override"})
                if overridden
                else budget_provider.monthly_budget(department=request.requester_department, month=month)
            )
        except BudgetSourceError as error:
            return Command(
                update={
                    "status": ProcurementStatus.BUDGET_SOURCE_UNAVAILABLE.value,
                    "audit_events": _event("budget_source_unavailable", "budget_adapter", str(error)),
                },
                # Returning to Logistics keeps the case alive and owned. Resubmitting
                # the research re-runs this check once the adapter recovers.
                goto="logistics_preparation",
            )
        available = snapshot.available_gel
        if snapshot.available_gel < research.estimated_cost_gel:
            return Command(
                update={
                    "budget": snapshot.model_dump(),
                    "status": ProcurementStatus.OUT_OF_BUDGET.value,
                    "audit_events": _event(
                        "budget_insufficient",
                        "budget_adapter",
                        f"{month}: available {available:,.2f} GEL; estimated cost {research.estimated_cost_gel:,.2f} GEL",
                    ),
                },
                # Logistics owns the estimated cost, so Logistics owns the rework.
                goto="logistics_preparation",
            )
        return Command(
            update={
                "budget": snapshot.model_dump(),
                "status": ProcurementStatus.PENDING_LOGISTICS_AUTHORIZATION.value,
                "audit_events": _event("budget_confirmed", "budget_adapter", f"{month}: available {available:,.2f} GEL"),
            },
            goto="logistics_authorization",
        )

    def logistics_authorization(state: CaseState) -> Command[Literal["prepare_review_pack", "finish"]]:
        research = _research(state)
        band = _approval_band(research.estimated_cost_gel)
        role = "head_of_logistics" if band == "under_500" else "director_of_logistics"
        response = interrupt(
            {
                "kind": "logistics_authorization",
                "title": f"{role.replace('_', ' ').title()} approval required",
                "role": role,
                "approval_band": band,
                "estimated_cost_gel": research.estimated_cost_gel,
                "allowed_decisions": [Decision.APPROVE.value, Decision.REJECT.value],
            }
        )
        decision, comment = _command_decision(response)
        if decision == Decision.APPROVE:
            return Command(
                update={
                    "status": ProcurementStatus.PENDING_CONTROL_REVIEWS.value,
                    "audit_events": _event("logistics_authorized", role, comment or "Approved"),
                },
                goto="prepare_review_pack",
            )
        return Command(
            update={"status": ProcurementStatus.REJECTED.value, "audit_events": _event("logistics_authorization_rejected", role, comment or "Rejected")},
            goto="finish",
        )

    def prepare_review_pack(state: CaseState) -> dict[str, Any]:
        result = agents.prepare_review_pack(
            _request(state), _research(state), state["supplier_evidence"], thread_id=state["case_id"]
        )
        return {
            "review_pack": result.content,
            "model_used": state.get("model_used", False) or result.used_model,
            "audit_events": _event("review_pack_prepared", "review_pack_agent", "Control review pack is ready"),
        }

    def review_node(role: ReviewRole):
        def node(state: CaseState) -> dict[str, Any]:
            round_number = state.get("review_round", 1)
            response = interrupt(
                {
                    "kind": "control_review",
                    "title": f"{role.value.upper()} review required",
                    "role": role.value,
                    "round_number": round_number,
                    "review_pack": state["review_pack"],
                    "allowed_decisions": [
                        Decision.APPROVE.value,
                        Decision.REJECT.value,
                        Decision.REQUEST_INFORMATION.value,
                    ],
                }
            )
            decision, comment = _command_decision(response)
            review = ReviewDecision(role=role, decision=decision, comment=comment, round_number=round_number)
            return {
                # Checkpoints must contain JSON primitives, not Python Enum objects.
                "review_decisions": [review.model_dump(mode="json")],
                "audit_events": _event(
                    f"{role.value}_{decision.value}", role.value, comment or decision.value.replace("_", " ")
                ),
            }

        return node

    def aggregate_reviews(state: CaseState) -> Command[Literal["logistics_rework", "ceo_approval", "start_procurement", "board_flow_pending"]]:
        round_number = state.get("review_round", 1)
        current = [
            ReviewDecision.model_validate(item)
            for item in state.get("review_decisions", [])
            if item.get("round_number") == round_number
        ]
        roles = {decision.role for decision in current}
        if roles != set(REVIEWERS):
            raise RuntimeError("Control review aggregation ran before all reviewers completed.")
        blockers = [decision for decision in current if decision.decision != Decision.APPROVE]
        if blockers:
            summary = "; ".join(f"{item.role.value}: {item.comment or item.decision.value}" for item in blockers)
            return Command(
                update={
                    "status": ProcurementStatus.PENDING_LOGISTICS_REWORK.value,
                    "audit_events": _event("control_review_blocked", "policy_engine", summary),
                },
                goto="logistics_rework",
            )
        band = _approval_band(_research(state).estimated_cost_gel)
        update = {"audit_events": _event("all_control_reviews_approved", "policy_engine", "Finance and Legal approved")}
        if band in {"under_500", "500_to_4999"}:
            return Command(update=update, goto="start_procurement")
        if band == "5000_to_19999":
            update["status"] = ProcurementStatus.PENDING_CEO_APPROVAL.value
            return Command(update=update, goto="ceo_approval")
        update["status"] = ProcurementStatus.PENDING_BOARD_FLOW_CONFIGURATION.value
        update["audit_events"] += _event("board_flow_not_configured", "policy_engine", "Board approval workflow is intentionally not configured yet")
        return Command(update=update, goto="board_flow_pending")

    def logistics_rework(state: CaseState) -> Command[Literal["prepare_review_pack", "finish"]]:
        round_number = state.get("review_round", 1)
        blockers = [
            item for item in state.get("review_decisions", []) if item.get("round_number") == round_number and item.get("decision") != Decision.APPROVE.value
        ]
        response = interrupt(
            {
                "kind": "logistics_rework",
                "title": "Logistics must address control findings",
                "findings": blockers,
                "allowed_decisions": ["resubmit", "stop"],
            }
        )
        # Every HITL node reads the same "decision" key the trusted API sends.
        action = response.get("decision", "stop") if isinstance(response, dict) else "stop"
        comment = response.get("comment", "") if isinstance(response, dict) else ""
        if action == "resubmit":
            return Command(
                update={
                    "status": ProcurementStatus.PENDING_CONTROL_REVIEWS.value,
                    "review_round": round_number + 1,
                    "audit_events": _event("logistics_resubmitted", "logistics", comment or "Resubmitted after rework"),
                },
                goto="prepare_review_pack",
            )
        return Command(
            update={
                "status": ProcurementStatus.REJECTED.value,
                "audit_events": _event("case_stopped_after_review", "logistics", comment or "Stopped after review findings"),
            },
            goto="finish",
        )

    def ceo_approval(state: CaseState) -> Command[Literal["start_procurement", "tender_preparation", "finish"]]:
        response = interrupt(
            {
                "kind": "ceo_approval",
                "title": "CEO approval required",
                "estimated_cost_gel": _research(state).estimated_cost_gel,
                "allowed_decisions": [Decision.APPROVE.value, "tender_request", Decision.REJECT.value],
            }
        )
        action = response.get("decision", Decision.REJECT) if isinstance(response, dict) else Decision.REJECT
        comment = str(response.get("comment", "")) if isinstance(response, dict) else ""
        if action == Decision.APPROVE.value:
            return Command(update={"audit_events": _event("ceo_approved", "ceo", comment or "Approved")}, goto="start_procurement")
        if action == "tender_request":
            return Command(
                update={
                    "status": ProcurementStatus.PENDING_TENDER_PREPARATION.value,
                    "audit_events": _event(
                        "ceo_requested_tender",
                        "ceo",
                        comment or "Tender requested; case returned to Logistics for tender preparation",
                    ),
                },
                goto="tender_preparation",
            )
        return Command(update={"status": ProcurementStatus.REJECTED.value, "audit_events": _event("ceo_rejected", "ceo", comment or "Rejected")}, goto="finish")

    def tender_preparation(state: CaseState) -> Command[Literal["finish"]]:
        """Logistics prepares the tender the CEO requested.

        Tender award rules are out of scope for this prototype, so the case stops
        once Logistics has recorded its preparation and attached the documents.
        """

        response = interrupt(
            {
                "kind": "tender_preparation",
                "title": "Logistics must prepare the tender",
                "estimated_cost_gel": _research(state).estimated_cost_gel,
                "allowed_decisions": ["submit_tender_preparation", "stop"],
            }
        )
        action = response.get("decision", "stop") if isinstance(response, dict) else "stop"
        comment = response.get("comment", "") if isinstance(response, dict) else ""
        if action == "submit_tender_preparation":
            return Command(
                update={
                    "status": ProcurementStatus.TENDER_PREPARED.value,
                    "audit_events": _event("tender_prepared", "logistics", comment or "Tender preparation submitted"),
                },
                goto="finish",
            )
        return Command(
            update={
                "status": ProcurementStatus.REJECTED.value,
                "audit_events": _event("tender_stopped", "logistics", comment or "Stopped instead of preparing a tender"),
            },
            goto="finish",
        )

    def board_flow_pending(state: CaseState) -> Command[Literal["finish"]]:
        return Command(goto="finish")

    def start_procurement(state: CaseState) -> Command[Literal["agreement_draft", "delivery_record"]]:
        next_node = "delivery_record" if _research(state).is_existing_supplier else "agreement_draft"
        return Command(
            update={"status": ProcurementStatus.AWAITING_DELIVERY.value, "audit_events": _event("procurement_started", "policy_engine", "All required approvals completed")}, goto=next_node
        )

    def agreement_draft(state: CaseState) -> Command[Literal["agreement_review"]]:
        result = agents.draft_agreement(_request(state), _research(state), thread_id=state["case_id"])
        return Command(
            update={
                "agreement_draft": result.content,
                "status": ProcurementStatus.PENDING_AGREEMENT_REVIEW.value,
                "model_used": state.get("model_used", False) or result.used_model,
                "audit_events": _event("agreement_draft_generated", "agreement_agent", "Draft marked not for signature"),
            },
            goto="agreement_review",
        )

    def agreement_review(state: CaseState) -> Command[Literal["agreement_draft", "delivery_record", "finish"]]:
        response = interrupt(
            {
                "kind": "agreement_review",
                "title": "Logistics and Legal agreement review required",
                "draft": state["agreement_draft"],
                "allowed_decisions": [Decision.APPROVE.value, Decision.REJECT.value, "revise"],
            }
        )
        action = response.get("decision", Decision.REJECT) if isinstance(response, dict) else Decision.REJECT
        comment = response.get("comment", "") if isinstance(response, dict) else ""
        if action == Decision.APPROVE.value:
            return Command(
                update={"status": ProcurementStatus.AWAITING_DELIVERY.value, "audit_events": _event("agreement_reviewed", "logistics_legal", comment or "Approved for external signature")},
                goto="delivery_record",
            )
        if action == "revise":
            return Command(update={"audit_events": _event("agreement_revision_requested", "logistics_legal", comment)}, goto="agreement_draft")
        return Command(
            update={"status": ProcurementStatus.REJECTED.value, "audit_events": _event("agreement_rejected", "logistics_legal", comment or "Rejected")},
            goto="finish",
        )

    def delivery_record(state: CaseState) -> Command[Literal["requester_acceptance"]]:
        response = interrupt(
            {
                "kind": "delivery_record",
                "title": "Logistics delivery record required",
                "allowed_decisions": ["record_delivery"],
            }
        )
        comment = response.get("comment", "") if isinstance(response, dict) else ""
        return Command(
            update={
                "status": ProcurementStatus.PENDING_REQUESTER_ACCEPTANCE.value,
                "audit_events": _event("delivery_recorded", "logistics", comment or "Delivery recorded"),
            },
            goto="requester_acceptance",
        )

    def requester_acceptance(state: CaseState) -> Command[Literal["signed_act", "delivery_record"]]:
        response = interrupt(
            {
                "kind": "requester_acceptance",
                "title": "Requester must accept delivery or report an issue",
                "allowed_decisions": [Decision.APPROVE.value, Decision.REJECT.value],
            }
        )
        decision, comment = _command_decision(response)
        if decision == Decision.APPROVE:
            return Command(
                update={"status": ProcurementStatus.PENDING_SIGNED_ACT.value, "audit_events": _event("delivery_accepted", "requester", comment or "Accepted")},
                goto="signed_act",
            )
        return Command(
            update={"status": ProcurementStatus.AWAITING_DELIVERY.value, "audit_events": _event("delivery_issue_reported", "requester", comment or "Issue reported")},
            goto="delivery_record",
        )

    def signed_act(state: CaseState) -> Command[Literal["finish"]]:
        response = interrupt(
            {
                "kind": "signed_act",
                "title": "Logistics confirms receipt of signed acceptance act",
                "allowed_decisions": ["confirm_received"],
            }
        )
        comment = response.get("comment", "") if isinstance(response, dict) else ""
        return Command(
            update={
                "status": ProcurementStatus.CLOSED.value,
                "audit_events": _event("case_closed", "logistics", comment or "Signed acceptance act received"),
            },
            goto="finish",
        )

    def finish(state: CaseState) -> dict[str, Any]:
        return {"audit_events": _event("workflow_finished", "system", f"Final status: {state['status']}")}

    graph = StateGraph(CaseState)
    graph.add_node("register_request", register_request)
    graph.add_node("head_approval", head_approval)
    graph.add_node("logistics_preparation", logistics_preparation)
    graph.add_node("budget_check", budget_check)
    graph.add_node("logistics_authorization", logistics_authorization)
    graph.add_node("prepare_review_pack", prepare_review_pack)
    graph.add_node("review_finance", review_node(ReviewRole.FINANCE))
    graph.add_node("review_legal", review_node(ReviewRole.LEGAL))
    graph.add_node("aggregate_reviews", aggregate_reviews)
    graph.add_node("logistics_rework", logistics_rework)
    graph.add_node("ceo_approval", ceo_approval)
    graph.add_node("tender_preparation", tender_preparation)
    graph.add_node("board_flow_pending", board_flow_pending)
    graph.add_node("start_procurement", start_procurement)
    graph.add_node("agreement_draft", agreement_draft)
    graph.add_node("agreement_review", agreement_review)
    graph.add_node("delivery_record", delivery_record)
    graph.add_node("requester_acceptance", requester_acceptance)
    graph.add_node("signed_act", signed_act)
    graph.add_node("finish", finish)
    graph.add_edge(START, "register_request")
    graph.add_edge("register_request", "head_approval")
    graph.add_edge("prepare_review_pack", "review_finance")
    graph.add_edge("prepare_review_pack", "review_legal")
    graph.add_edge("review_finance", "aggregate_reviews")
    graph.add_edge("review_legal", "aggregate_reviews")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def new_case_input(
    request: ProcurementRequest,
    *,
    budget_available_gel: float | None = None,
    budget_month: str | None = None,
    budget_allocated_gel: float | None = None,
) -> CaseState:
    """Create validated initial graph state. The graph thread ID equals case ID."""

    state: CaseState = {
        "case_id": f"PR-{uuid4().hex[:8].upper()}",
        "status": ProcurementStatus.DRAFT.value,
        "request": request.model_dump(),
        "review_round": 1,
        "review_decisions": [],
        "audit_events": [],
    }
    # Tests may inject a budget override. Normal requester-created cases do
    # not carry any budget data until the internal Budget Check node runs.
    if budget_available_gel is not None:
        allocated = budget_allocated_gel if budget_allocated_gel is not None else budget_available_gel
        state["budget"] = {
            "month": budget_month or utc_now()[:7],
            "allocated_gel": allocated,
            "committed_gel": max(0.0, allocated - budget_available_gel),
            "available_gel": budget_available_gel,
            "source": "test-budget-override",
        }
    return state


def invoke_case(graph, initial_state: CaseState) -> dict[str, Any]:
    return graph.invoke(
        initial_state,
        config={
            "configurable": {"thread_id": initial_state["case_id"]},
            **trace_config(initial_state["case_id"], component="procurement-workflow"),
        },
    )


def resume_case(graph, case_id: str, response: object) -> dict[str, Any]:
    """Resume only the durable case thread associated with this procurement case."""

    return graph.invoke(
        Command(resume=response),
        config={
            "configurable": {"thread_id": case_id},
            **trace_config(case_id, component="procurement-workflow"),
        },
    )


def interrupt_payloads(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize LangGraph interrupt objects for the UI and test harness."""

    payloads = []
    for item in result.get("__interrupt__", []):
        payloads.append({"id": getattr(item, "id", None), "value": getattr(item, "value", item)})
    return payloads
