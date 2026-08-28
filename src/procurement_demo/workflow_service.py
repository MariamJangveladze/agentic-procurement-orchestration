"""Trusted service boundary between authenticated UI requests and LangGraph."""

from __future__ import annotations

from typing import Any, Protocol

from .graph import CaseState, invoke_case, resume_case
from .models import ProcurementRequest


class CaseStore(Protocol):
    """The durable operations required by a workflow service."""

    def create_case(self, *, request: ProcurementRequest, state: dict[str, Any], requester_user_id: str) -> str: ...

    def sync_graph_state(self, *, case_number: str, state: dict[str, Any]) -> None: ...


class DurableWorkflowService:
    """Persist every graph start/resume from a trusted API endpoint.

    The calling API must validate the Supabase JWT and validate that the acting
    user owns the requested HITL action before it calls ``resume``. This class
    deliberately does not accept browser keys or decide an approval itself.
    """

    def __init__(self, graph: Any, store: CaseStore) -> None:
        self.graph = graph
        self.store = store

    def start(self, *, initial_state: CaseState, request: ProcurementRequest, requester_user_id: str) -> dict[str, Any]:
        self.store.create_case(request=request, state=initial_state, requester_user_id=requester_user_id)
        result = invoke_case(self.graph, initial_state)
        self.store.sync_graph_state(case_number=initial_state["case_id"], state=result)
        return result

    def resume(self, *, case_number: str, response: object) -> dict[str, Any]:
        result = resume_case(self.graph, case_number, response)
        self.store.sync_graph_state(case_number=case_number, state=result)
        return result
