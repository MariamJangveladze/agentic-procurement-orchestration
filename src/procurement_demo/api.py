"""Local demo API: React UI -> trusted LangGraph workflow.

This module intentionally keeps workflow state, budget access, and future
Supabase service credentials outside the browser. It is an in-memory bridge for
the prototype; the production replacement is the durable workflow service.
"""

from __future__ import annotations

import re
import secrets
import os
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .checkpointing import CheckpointRuntime
from .graph import _approval_band, build_graph, interrupt_payloads, invoke_case, new_case_input, resume_case
from .models import ProcurementRequest
from .supabase_store import SupabaseCaseStore, SupabaseSettings
from .workflow_service import DurableWorkflowService


checkpoint_runtime = CheckpointRuntime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Release the Postgres connection when a serverless worker is retired."""

    try:
        yield
    finally:
        checkpoint_runtime.close()


app = FastAPI(title="Procure Core demo API", version="0.1.0", lifespan=lifespan)

# Exact browser origins only. Vercel production must set CORS_ALLOWED_ORIGINS
# to the Request Hub production URL; local development stays available here.
cors_allowed_origins = {
    "http://127.0.0.1:8080",
    "http://localhost:8080",
}
cors_allowed_origins.update(
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(cors_allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Demo-User-Id"],
    max_age=600,
)

graph = build_graph(checkpointer=checkpoint_runtime.get_checkpointer())
# Case state is never cached: a second Vercel instance would keep serving a stale
# pending interrupt and resume the wrong step. Only the id registry is in memory,
# and Supabase is authoritative for it whenever the store is configured.
known_case_ids: set[str] = set()
case_documents: dict[str, list[dict[str, Any]]] = {}
demo_auth_ids: dict[str, str] = {}
settings = SupabaseSettings.from_environment()
case_store = SupabaseCaseStore(settings) if settings else None
workflow_service = DurableWorkflowService(graph, case_store) if case_store else None

# Test-only personas. Production replaces this directory with Supabase Auth,
# profiles.department_id and server-issued role assignments.
DEMO_USERS: dict[str, dict[str, str]] = {
    "nino": {"id": "nino", "name": "Nino Beridze", "role": "requester", "department": "Operations Department"},
    "david": {"id": "david", "name": "David Gakharia", "role": "department_head", "department": "Operations Department"},
    "giorgi": {"id": "giorgi", "name": "Giorgi Makharadze", "role": "logistics", "department": "Administration Department"},
    "tornike": {"id": "tornike", "name": "Tornike Zhvania", "role": "head_of_logistics", "department": "Administration Department"},
    "maia": {"id": "maia", "name": "Maia Kapanadze", "role": "director_of_logistics", "department": "Administration Department"},
    "lika": {"id": "lika", "name": "Lika Kvaratskhelia", "role": "finance", "department": "Financial Management Department"},
    "salome": {"id": "salome", "name": "Salome Abashidze", "role": "legal", "department": "Legal and Compliance Department"},
    "irakli": {"id": "irakli", "name": "Irakli Mchedlishvili", "role": "ceo", "department": "Executive Management"},
}


def current_user(x_demo_user_id: str | None = Header(default=None)) -> dict[str, str]:
    user = DEMO_USERS.get(x_demo_user_id or "nino")
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown test user.")
    return user


def _demo_auth_user_id(user: dict[str, str]) -> str:
    """Provision deterministic, confirmed Auth users for the test-persona mode."""

    if case_store is None:
        raise HTTPException(status_code=503, detail="Supabase server persistence is not configured.")
    if user["id"] in demo_auth_ids:
        return demo_auth_ids[user["id"]]
    email = f"{user['id']}@procure-core.demo"
    try:
        existing = next((item for item in case_store.client.auth.admin.list_users(per_page=100) if item.email == email), None)
        if existing is None:
            created = case_store.client.auth.admin.create_user(
                {"email": email, "email_confirm": True, "password": secrets.token_urlsafe(24), "user_metadata": {"display_name": user["name"]}}
            )
            existing = created.user
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Supabase Admin authentication failed. Set SUPABASE_SERVICE_ROLE_KEY to this project's current secret/service-role key and restart the API.",
        ) from error
    if existing is None:
        raise HTTPException(status_code=500, detail="Unable to provision the test user.")
    user_id = str(existing.id)
    demo_auth_ids[user["id"]] = user_id
    department = (
        case_store.client.table("departments").select("id").eq("name", user["department"]).limit(1).execute().data or []
    )
    case_store.client.table("profiles").update(
        {"display_name": user["name"], "department_id": department[0]["id"] if department else None, "position": user["role"].replace("_", " ").title()}
    ).eq("id", user_id).execute()
    case_store.client.table("user_roles").upsert({"user_id": user_id, "role": user["role"]}).execute()
    return user_id


def _safe_file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:180] or "attachment"


class CreateCaseBody(BaseModel):
    procurement_type: Literal["material", "service"]
    subcategory: str = Field(min_length=1)
    description: str = Field(min_length=10)
    deadline: str


class CaseActionBody(BaseModel):
    decision: str | None = None
    comment: str = ""
    supplier_name: str | None = None
    is_existing_supplier: bool | None = None
    estimated_cost_gel: float | None = Field(default=None, gt=0)
    offer_reference: str | None = None
    notes: str = ""


def _ui_status(status: str) -> str:
    return {
        "pending_head_approval": "waiting_manager",
        "pending_logistics_preparation": "logistics_prep",
        "pending_logistics_authorization": "logistics_prep",
        "pending_control_reviews": "in_review",
        "pending_logistics_rework": "returned",
        "pending_ceo_approval": "in_review",
        "pending_tender_preparation": "returned",
        "tender_prepared": "closed",
        "out_of_budget": "returned",
        "budget_source_unavailable": "returned",
        "pending_board_flow_configuration": "returned",
        "awaiting_delivery": "awaiting_delivery",
        "pending_requester_acceptance": "awaiting_acceptance",
        "closed": "closed",
        "rejected": "rejected",
        "cancelled": "rejected",
    }.get(status, "submitted")


# Statuses that mean the approved path ran to completion. Only these may mark
# every remaining step done; a rejected or cancelled case never reached them.
COMPLETED_STATUSES = {"closed", "tender_prepared"}

CURRENT_STEP_BY_STATUS = {
    "pending_head_approval": "head",
    "pending_logistics_preparation": "logistics",
    "out_of_budget": "logistics",
    "budget_source_unavailable": "logistics",
    "pending_logistics_authorization": "authorization",
    "pending_control_reviews": "controls",
    "pending_logistics_rework": "controls",
    "pending_ceo_approval": "ceo",
    "pending_board_flow_configuration": "board",
    "pending_tender_preparation": "procurement",
    "pending_agreement_review": "procurement",
    "awaiting_delivery": "procurement",
    "pending_requester_acceptance": "procurement",
    "pending_signed_act": "procurement",
}


def _workflow(status: str, research: dict[str, Any]) -> list[dict[str, str]]:
    """Build the step strip for the band this case actually falls in.

    A fixed step list marked approvals as done that the case never required —
    most visibly a CEO approval on a case below 5,000 GEL.
    """

    amount = research.get("estimated_cost_gel")
    band = _approval_band(amount) if amount is not None else None
    steps = [
        ("head", "Department Head approval"),
        ("logistics", "Logistics assessment and budget check"),
        (
            "authorization",
            "Head of Logistics approval" if band == "under_500"
            else "Director of Logistics approval" if band
            else "Logistics authority approval",
        ),
        ("controls", "Finance and Legal review"),
    ]
    # Until Logistics records a cost the band is unknown, so CEO stays visible
    # as an upcoming step rather than being silently dropped or marked done.
    if band is None or band == "5000_to_19999":
        steps.append(("ceo", "CEO approval"))
    if band == "board_required":
        steps.append(("board", "Board of Directors"))
    steps.append(("procurement", "Procurement and delivery"))

    current = CURRENT_STEP_BY_STATUS.get(status)
    if current is not None:
        done_before = next(index for index, (key, _) in enumerate(steps) if key == current)
    elif status in COMPLETED_STATUSES:
        done_before = len(steps)
    else:
        # Rejected or cancelled: the timeline is the record of where it stopped.
        done_before = 0
    return [
        {
            "key": key,
            "label": label,
            "state": "current" if key == current else "done" if index < done_before else "upcoming",
        }
        for index, (key, label) in enumerate(steps)
    ]


# Every interrupt kind the graph can raise needs an owner here, or the case
# pauses with nobody able to resume it. logistics_authorization and
# control_review carry their own "role" in the interrupt payload.
ROLE_BY_KIND = {
    "head_approval": "department_head",
    "logistics_preparation": "logistics",
    "logistics_rework": "logistics",
    "tender_preparation": "logistics",
    "delivery_record": "logistics",
    "signed_act": "logistics",
    "requester_acceptance": "requester",
    "ceo_approval": "ceo",
    "agreement_review": "legal",
}


def _pending_for_user(result: dict[str, Any], user: dict[str, str]) -> list[dict[str, Any]]:
    pending = interrupt_payloads(result)
    matching = [
        item for item in pending
        if item["value"].get("role", ROLE_BY_KIND.get(item["value"]["kind"])) == user["role"]
    ]
    if user["role"] == "department_head":
        return [item for item in matching if result["request"]["requester_department"] == user["department"]]
    if user["role"] == "requester":
        # Read ownership from the durable checkpoint, not from warm-instance memory.
        return [item for item in matching if result["request"]["requester_name"] == user["name"]]
    return matching


# Logistics keeps the case visible while the tender runs outside this prototype.
TENDER_STATUSES = {"pending_tender_preparation", "tender_prepared"}

_APPROVE = {"key": "approve", "label": "Approve", "tone": "primary"}
_REJECT = {"key": "reject", "label": "Reject", "tone": "danger"}

# One card per interrupt kind. Every "key" must appear in that node's
# allowed_decisions, because submit_action validates the two against each other.
# "title" falls back to the interrupt's own title; strings may use {role_title},
# {role_upper} and any other field carried in the interrupt payload.
ACTION_CARDS: dict[str, dict[str, Any]] = {
    "head_approval": {
        "title": "Department Head approval required",
        "requiredRole": "Department Head",
        "decisions": [{"key": "approve", "label": "Approve request", "tone": "primary"}, _REJECT],
    },
    "logistics_preparation": {
        "title": "Logistics assessment required",
        "requiredRole": "Logistics",
        "context": "Add supplier details and the Logistics estimated cost. The system then checks the monthly Google Sheets budget.",
        "decisions": [{"key": "submit_research", "label": "Submit assessment", "tone": "primary"}],
    },
    "logistics_authorization": {
        "requiredRole": "{role_title}",
        "context": "Estimated cost: {estimated_cost_gel:,.2f} GEL",
        "decisions": [_APPROVE, _REJECT],
    },
    "control_review": {
        "requiredRole": "{role_upper}",
        "context": "Decide for your department only. Finance and Legal review independently.",
        "decisions": [
            _APPROVE,
            {"key": "request_information", "label": "Request information", "tone": "neutral"},
            _REJECT,
        ],
    },
    "logistics_rework": {
        "title": "Logistics must address the control findings",
        "requiredRole": "Logistics",
        "context": "Resubmit to send the case back through Finance and Legal.",
        "decisions": [
            {"key": "resubmit", "label": "Resubmit for review", "tone": "primary"},
            {"key": "stop", "label": "Stop case", "tone": "danger"},
        ],
    },
    "ceo_approval": {
        "title": "CEO approval required",
        "requiredRole": "CEO",
        "context": "Estimated cost: {estimated_cost_gel:,.2f} GEL. Tender Request returns the case to Logistics for tender preparation.",
        "decisions": [
            {"key": "approve", "label": "Approve direct procurement", "tone": "primary"},
            {"key": "tender_request", "label": "Tender Request", "tone": "neutral"},
            _REJECT,
        ],
    },
    "tender_preparation": {
        "title": "Tender preparation required",
        "requiredRole": "Logistics",
        "context": "Estimated cost: {estimated_cost_gel:,.2f} GEL. Attach the tender documents, then submit the preparation.",
        "decisions": [
            {"key": "submit_tender_preparation", "label": "Submit tender preparation", "tone": "primary"},
            {"key": "stop", "label": "Stop case", "tone": "danger"},
        ],
    },
    "agreement_review": {
        "title": "Agreement review required",
        "requiredRole": "Legal",
        "decisions": [
            {"key": "approve", "label": "Approve for signature", "tone": "primary"},
            {"key": "revise", "label": "Request revision", "tone": "neutral"},
            _REJECT,
        ],
    },
    "delivery_record": {
        "title": "Delivery record required",
        "requiredRole": "Logistics",
        "decisions": [{"key": "record_delivery", "label": "Record delivery", "tone": "primary"}],
    },
    "requester_acceptance": {
        "title": "Confirm the delivery",
        "requiredRole": "Requester",
        "decisions": [
            {"key": "approve", "label": "Accept delivery", "tone": "primary"},
            {"key": "reject", "label": "Report an issue", "tone": "danger"},
        ],
    },
    "signed_act": {
        "title": "Confirm receipt of the signed acceptance act",
        "requiredRole": "Logistics",
        "decisions": [{"key": "confirm_received", "label": "Confirm receipt", "tone": "primary"}],
    },
}


def _action(result: dict[str, Any], user: dict[str, str]) -> dict[str, Any] | None:
    pending = _pending_for_user(result, user)
    if not pending:
        return None
    value = pending[0]["value"]
    spec = ACTION_CARDS.get(value["kind"])
    if spec is None:
        return None
    role = str(value.get("role", ""))
    fields = {**value, "role_title": role.replace("_", " ").title(), "role_upper": role.upper()}
    return {
        "id": value["kind"],
        "kind": value["kind"],
        "dueIn": "Demo",
        "commentRequiredFor": ["reject", "request_information", "revise", "stop"],
        "title": spec.get("title", value.get("title", "")).format(**fields),
        "requiredRole": spec["requiredRole"].format(**fields),
        "context": spec.get("context", "").format(**fields),
        "decisions": spec["decisions"],
    }


def _present(result: dict[str, Any], user: dict[str, str]) -> dict[str, Any]:
    request = result["request"]
    research = result.get("research") or {}
    status = result["status"]
    owner = (_action(result, user) or {}).get("requiredRole", "Logistics" if status in TENDER_STATUSES else "—")
    return {
        "id": result["case_id"],
        "title": request["description"],
        "amountGel": research.get("estimated_cost_gel"),
        "category": request["procurement_type"].title(),
        "subcategory": request["subcategory"],
        "status": _ui_status(status),
        "workflowStatus": status,
        "requester": request["requester_name"],
        "department": request["requester_department"],
        "deadline": request["deadline"],
        "currentOwner": owner,
        "workflow": _workflow(status, research),
        "action": _action(result, user),
        # Agent output is advisory evidence. modelUsed tells the reviewer whether
        # a model wrote it or the deterministic fallback did.
        "agentOutputs": [
            {"key": key, "label": label, "body": result[key]}
            for key, label in (
                ("supplier_evidence", "Supplier evidence"),
                ("review_pack", "Control review pack"),
                ("agreement_draft", "Agreement draft"),
            )
            if result.get(key)
        ],
        "modelUsed": bool(result.get("model_used", False)),
        "audit": [
            {"id": f"audit-{index}", "event": event["event"].replace("_", " ").title(), "actor": event["actor"], "role": event["actor"], "timestamp": event["timestamp"], "comment": event["detail"]}
            for index, event in enumerate(result.get("audit_events", []))
        ],
        "documents": _documents_for_case(result["case_id"]),
        "budget": result.get("budget"),
    }


@app.get("/api/me")
def get_me(user: dict[str, str] = Depends(current_user)) -> dict[str, str]:
    return user


@app.get("/api/demo-users")
def get_demo_users() -> list[dict[str, str]]:
    return list(DEMO_USERS.values())


def _can_access_case(case_id: str, result: dict[str, Any], user: dict[str, str]) -> bool:
    return (
        result["request"]["requester_name"] == user["name"]
        or bool(_pending_for_user(result, user))
        or (result["status"] in TENDER_STATUSES and user["role"] == "logistics")
    )


def _load_case_from_checkpoint(case_id: str) -> dict[str, Any] | None:
    """Rehydrate state and pending HITL interrupts on any Vercel instance."""

    # ponytail: one checkpoint read per case per request. Add a short-TTL cache
    # only if list_cases latency actually becomes a problem.
    snapshot = graph.get_state({"configurable": {"thread_id": case_id}})
    if not snapshot.values:
        return None
    result = dict(snapshot.values)
    result["__interrupt__"] = tuple(
        interrupt for task in snapshot.tasks for interrupt in task.interrupts
    )
    return result


def _all_case_ids() -> set[str]:
    identifiers = set(known_case_ids)
    if case_store is not None:
        rows = case_store.client.table("procurement_cases").select("case_number").execute().data or []
        identifiers.update(str(row["case_number"]) for row in rows)
    return identifiers


def _documents_for_case(case_id: str) -> list[dict[str, Any]]:
    """Read durable attachment metadata instead of warm-instance cache state."""

    if case_store is None:
        return case_documents.get(case_id, [])
    persisted = (
        case_store.client.table("procurement_cases").select("id").eq("case_number", case_id).single().execute().data
    )
    if not persisted:
        return []
    rows = (
        case_store.client.table("case_documents")
        .select("id, document_type, file_name, byte_size, version, created_at, metadata")
        .eq("case_id", persisted["id"])
        .order("created_at")
        .execute()
        .data
        or []
    )
    return [
        {
            "id": row["id"],
            "name": row["file_name"],
            "type": row["document_type"],
            "typeLabel": row["document_type"].replace("_", " ").title(),
            "status": "received",
            "version": row["version"],
            "uploader": (row.get("metadata") or {}).get("uploaded_by_label", "System"),
            "uploadedAt": row["created_at"],
            "size": f"{row['byte_size'] / 1024:.1f} KB",
        }
        for row in rows
    ]


@app.post("/api/cases")
def create_case(body: CreateCaseBody, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
    request = ProcurementRequest(
        **body.model_dump(),
        requester_name=user["name"],
        requester_department=user["department"],
    )
    initial_state = new_case_input(request)
    if workflow_service is None:
        result = invoke_case(graph, initial_state)
    else:
        result = workflow_service.start(
            initial_state=initial_state,
            request=request,
            requester_user_id=_demo_auth_user_id(user),
        )
    known_case_ids.add(initial_state["case_id"])
    return _present(result, user)


@app.get("/api/cases")
def list_cases(user: dict[str, str] = Depends(current_user)) -> list[dict[str, Any]]:
    cases = []
    for case_id in _all_case_ids():
        result = _load_case_from_checkpoint(case_id)
        if result is not None and _can_access_case(case_id, result, user):
            cases.append(_present(result, user))
    return cases


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
    result = _load_case_from_checkpoint(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Case checkpoint was not found.")
    if not _can_access_case(case_id, result, user):
        raise HTTPException(status_code=403, detail="This case is not assigned to your current role.")
    return _present(result, user)


@app.post("/api/cases/{case_id}/actions")
def submit_action(case_id: str, body: CaseActionBody, user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
    result = _load_case_from_checkpoint(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Case checkpoint was not found.")
    pending = _pending_for_user(result, user)
    if not pending:
        raise HTTPException(status_code=403, detail="This action is not assigned to your current role.")
    kind = pending[0]["value"]["kind"]
    if kind == "logistics_preparation":
        if not all([body.supplier_name, body.offer_reference, body.estimated_cost_gel is not None, body.is_existing_supplier is not None]):
            raise HTTPException(status_code=422, detail="Supplier name, supplier status, estimated cost, and offer reference are required.")
        response: Any = {"research": {"supplier_name": body.supplier_name, "is_existing_supplier": body.is_existing_supplier, "estimated_cost_gel": body.estimated_cost_gel, "offer_reference": body.offer_reference, "notes": body.notes}}
    elif kind == "control_review":
        if body.decision not in {"approve", "reject", "request_information"}:
            raise HTTPException(status_code=422, detail="Invalid control-review decision.")
        response = {item["id"]: {"decision": body.decision, "comment": body.comment} for item in pending}
    else:
        allowed = set(pending[0]["value"].get("allowed_decisions", []))
        if body.decision not in allowed:
            raise HTTPException(status_code=422, detail="Invalid action for this workflow stage.")
        response = {"decision": body.decision, "comment": body.comment}
    result = workflow_service.resume(case_number=case_id, response=response) if workflow_service else resume_case(graph, case_id, response)
    return _present(result, user)


@app.post("/api/cases/{case_id}/documents")
async def upload_case_document(
    case_id: str,
    document_type: Literal["request_attachment", "supplier_offer", "supplier_evidence", "tender_document", "delivery_document", "acceptance_act", "other"] = Form(...),
    file: UploadFile = File(...),
    user: dict[str, str] = Depends(current_user),
) -> dict[str, Any]:
    result = _load_case_from_checkpoint(case_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Case checkpoint was not found.")
    if not _can_access_case(case_id, result, user):
        raise HTTPException(status_code=403, detail="This case is not assigned to your current role.")
    if case_store is None:
        raise HTTPException(status_code=503, detail="Supabase server persistence is not configured.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    if len(content) > 52_428_800:
        raise HTTPException(status_code=413, detail="Attachment exceeds the 50 MB limit.")
    content_type = file.content_type or "application/octet-stream"
    storage_path = f"{case_id}/{uuid4().hex}-{_safe_file_name(file.filename or 'attachment')}"
    case_store.client.storage.from_("case-attachments").upload(storage_path, content, {"content-type": content_type})
    persisted = (
        case_store.client.table("procurement_cases").select("id").eq("case_number", case_id).single().execute().data
    )
    if not persisted:
        raise HTTPException(status_code=500, detail="Durable case record was not found.")
    uploaded_by = _demo_auth_user_id(user)
    row = {
        "case_id": persisted["id"],
        "document_type": document_type,
        "file_name": file.filename or "attachment",
        "content_type": content_type,
        "byte_size": len(content),
        "storage_path": storage_path,
        "uploaded_by": uploaded_by,
        "metadata": {"uploaded_by_label": user["name"]},
    }
    saved = case_store.client.table("case_documents").insert(row).execute().data[0]
    document = {
        "id": saved["id"], "name": saved["file_name"], "type": document_type,
        "typeLabel": document_type.replace("_", " ").title(), "status": "received", "version": saved["version"],
        "uploader": user["name"], "uploadedAt": saved["created_at"], "size": f"{len(content) / 1024:.1f} KB",
    }
    case_documents.setdefault(case_id, []).append(document)
    return document
