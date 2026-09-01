"""Streamlit showcase for the human-supervised procurement workflow."""

from __future__ import annotations

import streamlit as st

from procurement_demo.graph import (
    build_graph,
    interrupt_payloads,
    invoke_case,
    new_case_input,
    resume_case,
)
from procurement_demo.models import ProcurementRequest

st.set_page_config(page_title="Logistics Procurement Demo", layout="wide")


@st.cache_resource
def workflow():
    return build_graph()


def submit_response(response: object) -> None:
    case_id = st.session_state.case_id
    st.session_state.result = resume_case(workflow(), case_id, response)
    st.rerun()


def decision_form(payload: dict) -> None:
    value = payload["value"]
    kind = value["kind"]
    st.subheader(value["title"])

    if kind == "head_approval":
        st.json(value["request"])
        with st.form("head-approval"):
            decision = st.radio("Decision", ["approve", "reject"], horizontal=True)
            comment = st.text_area("Comment")
            if st.form_submit_button("Submit Head Decision"):
                submit_response({"decision": decision, "comment": comment})
        return

    if kind == "logistics_preparation":
        with st.form("logistics-preparation"):
            supplier_name = st.text_input("Supplier name", value="Atlas Office Systems")
            existing = st.checkbox("Existing approved supplier", value=True)
            amount = st.number_input("Logistics estimated cost (GEL)", min_value=1.0, value=8500.0, step=100.0)
            offer_reference = st.text_input("Offer reference", value="OFFER-2026-001")
            notes = st.text_area("Logistics notes", value="Demo offer validated by Logistics.")
            if st.form_submit_button("Submit Logistics Research"):
                submit_response({"research": {"supplier_name": supplier_name, "is_existing_supplier": existing, "estimated_cost_gel": amount, "offer_reference": offer_reference, "notes": notes}})
        return

    if kind == "logistics_rework":
        st.json(value["findings"])
        with st.form("logistics-rework"):
            action = st.radio("Action", ["resubmit", "stop"], horizontal=True)
            comment = st.text_area("Rework comment")
            if st.form_submit_button("Submit Rework Decision"):
                submit_response({"decision": action, "comment": comment})
        return

    if kind == "tender_preparation":
        with st.form("tender-preparation"):
            comment = st.text_area("Tender preparation reference")
            action = st.radio(
                "Action", ["submit_tender_preparation", "stop"], horizontal=True
            )
            if st.form_submit_button("Submit Tender Decision"):
                submit_response({"decision": action, "comment": comment})
        return

    if kind in {"logistics_authorization", "ceo_approval", "agreement_review", "requester_acceptance"}:
        if kind == "agreement_review":
            st.code(value["draft"], language="text")
            options = ["approve", "revise", "reject"]
        elif kind == "ceo_approval":
            options = ["approve", "tender_request", "reject"]
        else:
            options = ["approve", "reject"]
        with st.form(kind):
            decision = st.radio("Decision", options, horizontal=True)
            comment = st.text_area("Comment")
            if st.form_submit_button("Submit Decision"):
                submit_response({"decision": decision, "comment": comment})
        return

    if kind in {"delivery_record", "signed_act"}:
        label = "Delivery details" if kind == "delivery_record" else "Signed act reference"
        with st.form(kind):
            comment = st.text_area(label)
            if st.form_submit_button("Confirm"):
                submit_response({"decision": "record_delivery" if kind == "delivery_record" else "confirm_received", "comment": comment})
        return

    st.error(f"Unsupported interrupt type: {kind}")


def control_review_forms(payloads: list[dict]) -> None:
    st.subheader("Parallel control reviews")
    st.caption("Finance and Legal must each decide before the case proceeds.")
    resume_map = {}
    with st.form("parallel-controls"):
        for payload in payloads:
            value = payload["value"]
            role = value["role"]
            with st.expander(f"{role.upper()} review", expanded=True):
                st.text(value["review_pack"])
                decision = st.radio(
                    "Decision",
                    ["approve", "request_information", "reject"],
                    horizontal=True,
                    key=f"{role}-decision-{value['round_number']}",
                )
                comment = st.text_area("Comment", key=f"{role}-comment-{value['round_number']}")
                resume_map[payload["id"]] = {"decision": decision, "comment": comment}
        if st.form_submit_button("Submit all control reviews"):
            submit_response(resume_map)


def render_case() -> None:
    result = st.session_state.result
    st.success(f"Case: {st.session_state.case_id} | Status: {result.get('status')}")
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Case data")
        st.json({key: result.get(key) for key in ("request", "research", "budget") if result.get(key)})
    with right:
        st.subheader("Agent output")
        if result.get("supplier_evidence"):
            st.text_area("Supplier evidence", result["supplier_evidence"], height=220, disabled=True)
        if result.get("review_pack"):
            st.text_area("Review pack", result["review_pack"], height=220, disabled=True)
        st.caption("Real LangChain model used" if result.get("model_used") else "Deterministic local fallback used")

    st.subheader("Audit timeline")
    for event in result.get("audit_events", []):
        st.write(f"`{event['timestamp']}` — **{event['actor']}**: {event['event']} — {event['detail']}")

    pending = interrupt_payloads(result)
    if not pending:
        st.info("No pending human action. The case has reached its terminal state.")
        return
    if all(payload["value"].get("kind") == "control_review" for payload in pending):
        control_review_forms(pending)
    elif len(pending) == 1:
        decision_form(pending[0])
    else:
        st.error("Unexpected mixed approval batch. Resume controls as a group in this demo.")


st.title("Logistics Procurement — all value bands")
st.caption("Local LangGraph workflow with LangChain evidence, review-pack and agreement-drafting agents.")

if "result" not in st.session_state:
    with st.form("create-request"):
        st.subheader("1. Register request")
        procurement_type = st.selectbox("Procurement type", ["material", "service"])
        subcategories = {
            "material": ["equipment", "stationery request", "hospitality", "business travel", "vehicle maintenance", "other"],
            "service": ["cleaning service", "repairs", "vehicle maintenance", "other"],
        }
        subcategory = st.selectbox("Subcategory", subcategories[procurement_type])
        description = st.text_area("Description", value="Procure laptops for the new service desk team.")
        deadline = st.date_input("Deadline")
        if st.form_submit_button("Create procurement case"):
            request = ProcurementRequest(
                procurement_type=procurement_type,
                subcategory=subcategory,
                description=description,
                deadline=str(deadline),
                # The demo mirrors authenticated user context. The production
                # UI reads these values from the signed-in Supabase profile.
                requester_name="Nino Beridze",
                requester_department="Operations Department",
            )
            # Budget is an internal workflow concern. The requester never sees
            # or supplies it; production will read the monthly allocation and
            # commitments through the server-side budget adapter.
            initial_state = new_case_input(request)
            st.session_state.case_id = initial_state["case_id"]
            st.session_state.result = invoke_case(workflow(), initial_state)
            st.rerun()
else:
    if st.button("Start a new case"):
        st.session_state.clear()
        st.rerun()
    render_case()
