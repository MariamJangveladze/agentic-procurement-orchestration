"""Supabase adapters for durable cases and server-side pgvector retrieval.

These adapters are deliberately used only by the trusted LangGraph service.
The browser authenticates with Supabase Auth and is constrained by RLS; it must
never receive ``SUPABASE_SERVICE_ROLE_KEY`` or an LLM provider credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv
from supabase import Client, create_client

from .models import ProcurementRequest


@dataclass(frozen=True)
class SupabaseSettings:
    """Server-only environment configuration."""

    url: str
    service_role_key: str

    @classmethod
    def from_environment(cls) -> "SupabaseSettings | None":
        load_dotenv()
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return None
        return cls(url=url, service_role_key=key)


def _vector_literal(values: list[float]) -> str:
    """Encode an embedding in pgvector's text format for PostgREST RPC."""

    return "[" + ",".join(f"{value:.8g}" for value in values) + "]"


def _json_projection(value: Any) -> Any:
    """Convert LangGraph runtime values into a JSON-safe case projection.

    ``__interrupt__`` contains LangGraph ``Interrupt`` objects while a workflow
    is waiting for a human.  The checkpoint store retains those objects; the
    Supabase case view only needs a readable, durable projection of the state.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_projection(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_projection(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_projection(value.model_dump(mode="json"))
    if hasattr(value, "value"):
        return _json_projection(value.value)
    return str(value)


def _durable_state_projection(state: dict[str, Any]) -> dict[str, Any]:
    """Omit ephemeral interrupt instructions from the UI/audit projection."""

    return {
        key: _json_projection(value)
        for key, value in state.items()
        if key != "__interrupt__"
    }


class SupabaseCaseStore:
    """Write case state and append-only audit events from a trusted worker."""

    def __init__(self, settings: SupabaseSettings) -> None:
        self.client: Client = create_client(settings.url, settings.service_role_key)

    def create_case(
        self,
        *,
        request: ProcurementRequest,
        state: dict[str, Any],
        requester_user_id: str,
    ) -> str:
        """Create the durable record before starting the graph thread."""

        row = {
            "case_number": state["case_id"],
            "requester_id": requester_user_id,
            "requester_department": request.requester_department,
            "category": request.procurement_type,
            "subcategory": request.subcategory,
            "description": request.description,
            "deadline": request.deadline,
            "estimated_amount_gel": None,
            "status": state["status"],
            "graph_thread_id": state["case_id"],
            "graph_state": _durable_state_projection(state),
        }
        response = self.client.table("procurement_cases").insert(row).execute()
        case = response.data[0]
        self.client.table("case_members").insert(
            {"case_id": case["id"], "user_id": requester_user_id, "role": "requester"}
        ).execute()
        return str(case["id"])

    def sync_graph_state(self, *, case_number: str, state: dict[str, Any]) -> None:
        """Persist the latest checkpoint projection and new audit events.

        LangGraph remains the workflow engine. This table is the durable case
        view consumed by the UI and retained for audit/search.
        """

        lookup = (
            self.client.table("procurement_cases")
            .select("id, version")
            .eq("case_number", case_number)
            .single()
            .execute()
        )
        case = lookup.data
        research = state.get("research") or {}
        self.client.table("procurement_cases").update(
            {
                "status": state["status"],
                "graph_state": _durable_state_projection(state),
                "estimated_amount_gel": research.get("estimated_cost_gel"),
                "version": int(case["version"]) + 1,
                "closed_at": datetime.now(timezone.utc).isoformat()
                if state["status"] in {"closed", "cancelled", "rejected"}
                else None,
            }
        ).eq("id", case["id"]).execute()

        known = (
            self.client.table("case_events")
            .select("event_type, payload")
            .eq("case_id", case["id"])
            .execute()
        ).data or []
        known_keys = {item.get("payload", {}).get("audit_key") for item in known}
        for index, event in enumerate(state.get("audit_events", [])):
            audit_key = f"{case_number}:{index}:{event['event']}:{event['timestamp']}"
            if audit_key in known_keys:
                continue
            self.client.table("case_events").insert(
                {
                    "case_id": case["id"],
                    "event_type": event["event"],
                    "actor_label": event["actor"],
                    "actor_kind": "human" if event["actor"] in {"requester", "logistics", "head_of_department"} else "agent",
                    "payload": {"detail": event["detail"], "audit_key": audit_key},
                    "occurred_at": event["timestamp"],
                }
            ).execute()


class SupabaseKnowledgeBase:
    """Permission-isolated semantic retrieval over Supabase pgvector."""

    def __init__(self, settings: SupabaseSettings, *, model: str = "text-embedding-3-small") -> None:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required for Supabase semantic retrieval.")
        self.client: Client = create_client(settings.url, settings.service_role_key)
        self.embeddings = OpenAIEmbeddings(model=model)

    def search(self, query: str, *, supplier_name: str | None = None, k: int = 4) -> list[Document]:
        prompt = f"{query}\nSupplier: {supplier_name}" if supplier_name else query
        embedding = _vector_literal(self.embeddings.embed_query(prompt))
        response = self.client.rpc(
            "match_knowledge_chunks",
            {"query_embedding": embedding, "match_count": k, "document_types": None},
        ).execute()
        return [
            Document(
                page_content=row["content"],
                metadata={
                    "document_id": row["source_key"],
                    "title": row["title"],
                    "type": row["document_type"],
                    "similarity": row["similarity"],
                    **(row.get("metadata") or {}),
                },
            )
            for row in (response.data or [])
        ]

    @staticmethod
    def format_citations(documents: Iterable[Document]) -> str:
        parts = []
        for document in documents:
            document_id = document.metadata.get("document_id", "unknown-document")
            parts.append(f"[{document_id}] {document.page_content}")
        return "\n\n".join(parts) or "No matching trusted evidence found."
