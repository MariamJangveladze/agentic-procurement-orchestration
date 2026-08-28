"""Regression coverage for the current uncommitted HITL workflow change set."""

from __future__ import annotations

import ast
from pathlib import Path

from procurement_demo.agents import ProcurementAgents
from procurement_demo.budget import FixedBudgetProvider
from procurement_demo.graph import build_graph, interrupt_payloads, invoke_case, new_case_input, resume_case
from procurement_demo.knowledge import DEMO_DOCUMENTS, LocalKnowledgeBase
from procurement_demo.models import Decision, ProcurementRequest, SupplierResearch


def _request() -> ProcurementRequest:
    return ProcurementRequest(
        procurement_type="material",
        subcategory="equipment",
        description="Procure laptops for QA workflow coverage.",
        deadline="2026-08-15",
        requester_name="Nino Beridze",
        requester_department="Operations Department",
    )


def _research(cost: float) -> dict[str, object]:
    return {
        "research": {
            "supplier_name": "Atlas Office Systems",
            "is_existing_supplier": True,
            "estimated_cost_gel": cost,
            "offer_reference": "QA-OFFER-1",
            "notes": "",
        }
    }


def _graph(*, budget: float = 20_000):
    # Explicit dependencies keep the verification fully offline, regardless of .env.
    return build_graph(
        agents=ProcurementAgents(LocalKnowledgeBase(documents=DEMO_DOCUMENTS), model_name=None),
        budget_provider=FixedBudgetProvider(budget),
    )


def _resume_reviews(graph, case_id: str, result: dict, decision: str) -> dict:
    pending = interrupt_payloads(result)
    assert {item["value"]["role"] for item in pending} == {"finance", "legal"}
    return resume_case(graph, case_id, {item["id"]: {"decision": decision} for item in pending})


def _to_control_reviews(graph, initial: dict, cost: float) -> dict:
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(cost))
    assert interrupt_payloads(result)[0]["value"]["kind"] == "logistics_authorization"
    return resume_case(graph, initial["case_id"], {"decision": "approve"})


def _graph_interrupt_specs() -> dict[str, set[str]]:
    """Read graph literals so every graph interrupt is covered, including branches."""
    tree = ast.parse(Path("src/procurement_demo/graph.py").read_text())
    specs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "interrupt":
            continue
        payload = node.args[0]
        assert isinstance(payload, ast.Dict)
        values = {key.value: value for key, value in zip(payload.keys, payload.values) if isinstance(key, ast.Constant)}
        kind = values["kind"]
        allowed = values["allowed_decisions"]
        assert isinstance(kind, ast.Constant) and isinstance(kind.value, str)
        def decision_value(item: ast.AST) -> str:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                return item.value
            if isinstance(item, ast.Attribute) and isinstance(item.value, ast.Name) and item.value.id == "Decision":
                return getattr(Decision, item.attr).value
            if (
                isinstance(item, ast.Attribute)
                and item.attr == "value"
                and isinstance(item.value, ast.Attribute)
                and isinstance(item.value.value, ast.Name)
                and item.value.value.id == "Decision"
            ):
                return getattr(Decision, item.value.attr).value
            raise TypeError(f"Unsupported graph decision expression: {ast.dump(item)}")

        assert isinstance(allowed, ast.List)
        specs[kind.value] = {decision_value(item) for item in allowed.elts}
    return specs


def _api_constants() -> tuple[dict, dict, dict]:
    """Read API tables without importing its deployment-time database setup."""
    tree = ast.parse(Path("src/procurement_demo/api.py").read_text())
    values: dict[str, object] = {}

    def evaluate(node: ast.AST):
        if isinstance(node, ast.Name):
            return values[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.List):
            return [evaluate(item) for item in node.elts]
        if isinstance(node, ast.Dict):
            return {evaluate(key): evaluate(value) for key, value in zip(node.keys, node.values)}
        raise TypeError(f"Unsupported API table expression: {ast.dump(node)}")

    for statement in tree.body:
        target = None
        value = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target, value = statement.targets[0].id, statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name) and statement.value:
            target, value = statement.target.id, statement.value
        if target in {"DEMO_USERS", "ROLE_BY_KIND", "ACTION_CARDS", "_APPROVE", "_REJECT"}:
            values[target] = evaluate(value)
    return values["DEMO_USERS"], values["ROLE_BY_KIND"], values["ACTION_CARDS"]


def test_every_graph_interrupt_is_claimable_by_exactly_one_demo_persona():
    specs = _graph_interrupt_specs()
    demo_users, role_by_kind, _ = _api_constants()
    # The two role-carrying interrupt types are dynamic; enumerate every value
    # their graph nodes can produce across approval bands and review departments.
    dynamic_roles = {
        "logistics_authorization": {"head_of_logistics", "director_of_logistics"},
        "control_review": {"finance", "legal"},
    }
    demo_roles = [user["role"] for user in demo_users.values()]

    for kind in specs:
        roles = dynamic_roles.get(kind, {role_by_kind.get(kind)})
        assert None not in roles, f"{kind} has no API or payload role owner"
        for role in roles:
            assert demo_roles.count(role) == 1, f"{kind} / {role} is not claimed by exactly one demo persona"


def test_action_card_decisions_are_allowed_by_the_matching_graph_interrupt():
    graph_specs = _graph_interrupt_specs()
    _, _, action_cards = _api_constants()
    assert set(action_cards) == set(graph_specs)
    for kind, card in action_cards.items():
        offered = {decision["key"] for decision in card["decisions"]}
        assert offered <= graph_specs[kind], f"{kind}: {offered - graph_specs[kind]} is offered but not allowed"


def test_rework_reviewer_reject_then_logistics_resubmit_then_approvals_proceeds():
    graph = _graph()
    initial = new_case_input(_request())
    result = _to_control_reviews(graph, initial, 6_000)
    result = _resume_reviews(graph, initial["case_id"], result, "reject")
    assert interrupt_payloads(result)[0]["value"]["kind"] == "logistics_rework"

    result = resume_case(graph, initial["case_id"], {"decision": "resubmit", "comment": "Corrected."})
    assert result["review_round"] == 2
    result = _resume_reviews(graph, initial["case_id"], result, "approve")
    assert interrupt_payloads(result)[0]["value"]["kind"] == "ceo_approval"


def test_out_of_budget_returns_to_logistics_and_resubmission_proceeds():
    graph = _graph(budget=1_000)
    initial = new_case_input(_request())
    result = invoke_case(graph, initial)
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], _research(1_500))
    assert result["status"] == "out_of_budget"
    assert interrupt_payloads(result)[0]["value"]["kind"] == "logistics_preparation"

    result = resume_case(graph, initial["case_id"], _research(900))
    assert result["status"] == "pending_logistics_authorization"


def test_ceo_tender_path_reaches_tender_preparation_and_logistics_can_resume():
    graph = _graph()
    initial = new_case_input(_request())
    result = _to_control_reviews(graph, initial, 6_000)
    result = _resume_reviews(graph, initial["case_id"], result, "approve")
    assert interrupt_payloads(result)[0]["value"]["kind"] == "ceo_approval"

    result = resume_case(graph, initial["case_id"], {"decision": "tender_request"})
    assert result["status"] == "pending_tender_preparation"
    assert interrupt_payloads(result)[0]["value"]["kind"] == "tender_preparation"
    result = resume_case(graph, initial["case_id"], {"decision": "submit_tender_preparation"})
    assert result["status"] == "tender_prepared"


def test_model_disabled_workflow_completes_without_provider_calls():
    graph = _graph()
    initial = new_case_input(_request())
    result = _to_control_reviews(graph, initial, 499)
    result = _resume_reviews(graph, initial["case_id"], result, "approve")
    result = resume_case(graph, initial["case_id"], {"decision": "record_delivery"})
    result = resume_case(graph, initial["case_id"], {"decision": "approve"})
    result = resume_case(graph, initial["case_id"], {"decision": "confirm_received"})
    assert result["status"] == "closed"
    assert result["model_used"] is False


def test_all_agent_entry_points_fall_back_when_the_model_provider_fails(monkeypatch):
    agents = ProcurementAgents(LocalKnowledgeBase(documents=DEMO_DOCUMENTS), model_name="test-provider")
    request = _request()
    research = SupplierResearch.model_validate(_research(100)["research"])

    class BrokenAgent:
        def invoke(self, *args, **kwargs):
            raise ConnectionError("provider unavailable")

    monkeypatch.setattr("procurement_demo.agents.create_agent", lambda **kwargs: BrokenAgent())
    monkeypatch.setattr(agents, "_model", lambda: (_ for _ in ()).throw(ConnectionError("provider unavailable")))

    evidence = agents.prepare_supplier_evidence(request, research, thread_id="QA-1")
    review_pack = agents.prepare_review_pack(request, research, evidence.content, thread_id="QA-1")
    agreement = agents.draft_agreement(request, research, thread_id="QA-1")
    assert not evidence.used_model and "Supplier evidence prepared" in evidence.content
    assert not review_pack.used_model and "Review pack" in review_pack.content
    assert not agreement.used_model and "DRAFT" in agreement.content
