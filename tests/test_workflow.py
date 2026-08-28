from procurement_demo.graph import build_graph, interrupt_payloads, invoke_case, new_case_input, resume_case
from procurement_demo.budget import FixedBudgetProvider
from procurement_demo.models import ProcurementRequest


def _request(*, procurement_type="material", subcategory="equipment"):
    return ProcurementRequest(
        procurement_type=procurement_type,
        subcategory=subcategory,
        description="Procure laptops for the new service desk team.",
        deadline="2026-08-15",
        requester_name="Nino Beridze",
        requester_department="Operations",
    )


def _research(amount):
    return {"research": {"supplier_name": "Atlas Office Systems", "is_existing_supplier": True, "estimated_cost_gel": amount, "offer_reference": "OFFER-1", "notes": ""}}


def _graph():
    return build_graph(budget_provider=FixedBudgetProvider(20_000))


def test_happy_path_closes_case_with_ceo_and_parallel_human_reviews():
    graph = _graph()
    request = _request()
    initial = new_case_input(request)
    result = invoke_case(graph, initial)

    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(
        graph,
        initial["case_id"],
        _research(8500),
    )
    pending = interrupt_payloads(result)
    assert pending[0]["value"]["kind"] == "logistics_authorization"
    assert pending[0]["value"]["role"] == "director_of_logistics"
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    pending = interrupt_payloads(result)
    assert len(pending) == 2
    assert {item["value"]["role"] for item in pending} == {"finance", "legal"}

    result = resume_case(
        graph,
        initial["case_id"],
        {item["id"]: {"decision": "approve", "comment": "Approved"} for item in pending},
    )
    pending = interrupt_payloads(result)
    assert pending[0]["value"]["kind"] == "ceo_approval"
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], {"action": "record_delivery"})
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], {"action": "confirm_received"})

    assert result["status"] == "closed"
    assert result["model_used"] is False
    assert any(event["event"] == "case_closed" for event in result["audit_events"])


def test_budget_failure_returns_the_case_to_logistics():
    graph = _graph()
    request = _request(procurement_type="service", subcategory="repairs")
    initial = new_case_input(request, budget_available_gel=1000)
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(
        graph,
        initial["case_id"],
        _research(9000),
    )

    assert result["status"] == "out_of_budget"
    # The case stays alive and owned: Logistics can research a cheaper supplier.
    assert interrupt_payloads(result)[0]["value"]["kind"] == "logistics_preparation"

    result = resume_case(graph, initial["case_id"], _research(900))
    assert result["status"] == "pending_logistics_authorization"


def test_rework_loop_rereads_the_budget_source_instead_of_its_own_snapshot():
    """The second budget_check must consult the provider, not the state it wrote.

    Inferring a test override from `available_gel` would silently freeze the
    budget at the first reading once the rework loop re-enters this node.
    """

    graph = build_graph(budget_provider=FixedBudgetProvider(5_000))
    initial = new_case_input(_request())
    invoke_case(graph, initial)
    resume_case(graph, initial["case_id"], {"decision": "approve"})

    result = resume_case(graph, initial["case_id"], _research(9_000))
    assert result["status"] == "out_of_budget"
    assert result["budget"]["source"] == "test-fixed-budget-provider"

    result = resume_case(graph, initial["case_id"], _research(1_000))
    assert result["status"] == "pending_logistics_authorization"
    # Re-read from the provider, not carried over from the previous snapshot.
    assert result["budget"]["source"] == "test-fixed-budget-provider"
    assert result["budget"]["available_gel"] == 5_000


def test_under_500_requires_head_of_logistics_and_not_ceo():
    graph = _graph()
    initial = new_case_input(_request())
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(499.99))
    pending = interrupt_payloads(result)
    assert pending[0]["value"]["role"] == "head_of_logistics"
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    pending = interrupt_payloads(result)
    result = resume_case(graph, initial["case_id"], {item["id"]: {"decision": "approve"} for item in pending})
    assert interrupt_payloads(result)[0]["value"]["kind"] == "delivery_record"


def test_500_requires_director_of_logistics():
    graph = _graph()
    initial = new_case_input(_request())
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(500))
    assert interrupt_payloads(result)[0]["value"]["role"] == "director_of_logistics"


def test_20000_stops_at_unconfigured_board_flow():
    graph = _graph()
    initial = new_case_input(_request(), budget_available_gel=25000)
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(20000))
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    pending = interrupt_payloads(result)
    result = resume_case(graph, initial["case_id"], {item["id"]: {"decision": "approve"} for item in pending})
    assert result["status"] == "pending_board_flow_configuration"


def test_ceo_tender_request_returns_5000_to_20000_case_to_logistics():
    graph = _graph()
    initial = new_case_input(_request())
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(5000))
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    pending = interrupt_payloads(result)
    result = resume_case(graph, initial["case_id"], {item["id"]: {"decision": "approve"} for item in pending})
    pending = interrupt_payloads(result)
    assert pending[0]["value"]["kind"] == "ceo_approval"
    result = resume_case(graph, initial["case_id"], {"decision": "tender_request", "comment": "Tender required."})

    assert result["status"] == "pending_tender_preparation"
    assert any(event["event"] == "ceo_requested_tender" for event in result["audit_events"])
