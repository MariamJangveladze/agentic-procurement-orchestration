from procurement_demo.graph import build_graph, new_case_input
from procurement_demo.budget import FixedBudgetProvider
from procurement_demo.models import ProcurementRequest
from procurement_demo.workflow_service import DurableWorkflowService


class FakeCaseStore:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.synced: list[dict] = []

    def create_case(self, **kwargs) -> str:
        self.created.append(kwargs)
        return "case-db-id"

    def sync_graph_state(self, **kwargs) -> None:
        self.synced.append(kwargs)


def test_durable_service_persists_graph_start_and_resume():
    request = ProcurementRequest(
        procurement_type="material",
        subcategory="equipment",
        description="Procure laptops for the new service desk team.",
        deadline="2026-08-15",
        requester_name="Nino Beridze",
        requester_department="Operations",
    )
    initial = new_case_input(request)
    store = FakeCaseStore()
    service = DurableWorkflowService(build_graph(budget_provider=FixedBudgetProvider(20_000)), store)

    result = service.start(initial_state=initial, request=request, requester_user_id="00000000-0000-0000-0000-000000000001")
    assert result["status"] == "pending_head_approval"
    assert len(store.created) == 1
    assert len(store.synced) == 1

    result = service.resume(case_number=initial["case_id"], response={"decision": "approve"})
    assert result["status"] == "pending_logistics_preparation"
    assert len(store.synced) == 2
