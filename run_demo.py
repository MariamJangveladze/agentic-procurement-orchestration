"""Run the happy-path approval workflow without the Streamlit UI."""

from procurement_demo.graph import build_graph, interrupt_payloads, invoke_case, new_case_input, resume_case
from procurement_demo.models import ProcurementRequest


def response_for(payload: dict) -> dict:
    kind = payload["value"]["kind"]
    responses = {
        "head_approval": {"decision": "approve", "comment": "Approved for demo."},
        "logistics_preparation": {
            "research": {
                "supplier_name": "Atlas Office Systems",
                "is_existing_supplier": True,
                "estimated_cost_gel": 8500,
                "offer_reference": "OFFER-2026-001",
                "notes": "Local demo supplier research.",
            }
        },
        "logistics_authorization": {"decision": "approve", "comment": "Logistics authority approved."},
        "ceo_approval": {"decision": "approve", "comment": "CEO approved."},
        "delivery_record": {"action": "record_delivery", "comment": "Delivered to Operations."},
        "requester_acceptance": {"decision": "approve", "comment": "Accepted by requester."},
        "signed_act": {"action": "confirm_received", "comment": "Signed acceptance act received."},
    }
    if kind == "control_review":
        return {"decision": "approve", "comment": f"{payload['value']['role']} approved."}
    return responses[kind]


def main() -> None:
    graph = build_graph()
    request = ProcurementRequest(
        procurement_type="material",
        subcategory="equipment",
        description="Procure laptops for the new service desk team.",
        deadline="2026-08-15",
        requester_name="Nino Beridze",
        requester_department="Operations",
    )
    initial = new_case_input(request)
    result = invoke_case(graph, initial)
    while pending := interrupt_payloads(result):
        if len(pending) > 1:
            result = resume_case(graph, initial["case_id"], {item["id"]: response_for(item) for item in pending})
        else:
            result = resume_case(graph, initial["case_id"], response_for(pending[0]))
    print(f"Case {initial['case_id']} finished with status: {result['status']}")
    print("Audit events:")
    for event in result["audit_events"]:
        print(f"- {event['event']}: {event['detail']}")


if __name__ == "__main__":
    main()
