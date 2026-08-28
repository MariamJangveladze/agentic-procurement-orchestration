"""LangChain agents that prepare evidence and drafts without authority to act."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool
from dotenv import load_dotenv

from .knowledge import KnowledgeBase
from .models import ProcurementRequest, SupplierResearch
from .observability import trace_config

load_dotenv()

logger = logging.getLogger(__name__)


@wrap_tool_call
def trusted_evidence_only(request, handler):
    """Permit the evidence agent to use only its read-only retrieval tool."""

    if request.tool_call["name"] != "search_supplier_evidence":
        return "Blocked: this agent may only retrieve trusted supplier evidence."
    return handler(request)


@dataclass
class AgentResult:
    content: str
    used_model: bool


def _message_text(result: dict) -> str:
    message = result["messages"][-1]
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


class ProcurementAgents:
    """Creates fixed-purpose LangChain agents with tightly-scoped tools.

    No tool can mutate a case, submit an approval, reserve budget, or send an
    agreement. Human decisions are owned by the LangGraph workflow.
    """

    def __init__(self, knowledge_base: KnowledgeBase, model_name: str | None = None):
        self.knowledge_base = knowledge_base
        self.model_name = (
            model_name or os.getenv("PROCUREMENT_MODEL", "").strip() or None
        )
        self._chat_model = None

    @property
    def model_enabled(self) -> bool:
        return bool(self.model_name)

    def _model(self):
        if self._chat_model is None:
            self._chat_model = init_chat_model(self.model_name)
        return self._chat_model

    def _complete(
        self, *, system: str, user: str, fallback: str, thread_id: str, component: str
    ) -> AgentResult:
        """Run a single model call that degrades to the deterministic text.

        These two tasks have no tools, so an agent loop would only add latency and
        a recursion limit around a loop that cannot iterate.
        """

        try:
            message = self._model().invoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                config=trace_config(thread_id, component=component, model_enabled=True),
            )
        except Exception:
            # ponytail: any provider failure degrades to the manual path the
            # architecture requires. Narrow this if a specific error needs a retry.
            # Always log it: a silent fallback looks identical to "no model configured".
            logger.exception(
                "Model call failed for %s; using the deterministic fallback.", component
            )
            return AgentResult(content=fallback, used_model=False)
        content = getattr(message, "content", "")
        return AgentResult(
            content=content if isinstance(content, str) else str(content),
            used_model=True,
        )

    def prepare_supplier_evidence(
        self, request: ProcurementRequest, research: SupplierResearch, *, thread_id: str
    ) -> AgentResult:
        evidence = self.knowledge_base.format_citations(
            self.knowledge_base.search(
                f"supplier evidence required documents {request.subcategory}",
                supplier_name=research.supplier_name,
            )
        )
        fallback = (
            f"Supplier evidence prepared for {research.supplier_name}.\n"
            f"Supplier status recorded by Logistics: {'existing' if research.is_existing_supplier else 'new'}.\n"
            f"Logistics estimated cost: {research.estimated_cost_gel:,.2f} GEL; reference: {research.offer_reference}.\n"
            f"Trusted evidence:\n{evidence}\n\n"
            "Human review required: validate the evidence, supplier status, offer and required documents."
        )
        if not self.model_enabled:
            return AgentResult(content=fallback, used_model=False)

        @tool
        def search_supplier_evidence(query: str) -> str:
            """Search trusted, local supplier and procurement-policy documents.

            Use this to ground a supplier evidence summary. The returned text is
            evidence only; it never authorizes a supplier or an approval.

            Args:
                query: Focused evidence question about the supplier or required documents.
            """

            return self.knowledge_base.format_citations(
                self.knowledge_base.search(query, supplier_name=research.supplier_name)
            )

        agent = create_agent(
            model=self.model_name,
            tools=[search_supplier_evidence],
            middleware=[trusted_evidence_only],
            system_prompt=(
                "You are the Procurement Evidence Agent. Prepare a concise, cited evidence summary. "
                "Use only tool results for factual claims. Never approve/reject a supplier, infer missing "
                "legal data, or recommend bypassing controls. End with missing evidence and the human role "
                "that must validate it."
            ),
        )
        prompt = (
            f"Prepare supplier evidence for this request: {request.model_dump_json()}. "
            f"Logistics research: {research.model_dump_json()}."
        )
        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "configurable": {"thread_id": f"{thread_id}:supplier-evidence"},
                    "recursion_limit": 8,
                    **trace_config(
                        thread_id,
                        component="supplier-evidence-agent",
                        model_enabled=True,
                    ),
                },
            )
        except Exception:
            # ponytail: see _complete; the workflow must stay usable without a model.
            logger.exception(
                "Supplier evidence agent failed; using the deterministic fallback."
            )
            return AgentResult(content=fallback, used_model=False)
        return AgentResult(content=_message_text(result), used_model=True)

    def prepare_review_pack(
        self,
        request: ProcurementRequest,
        research: SupplierResearch,
        evidence_summary: str,
        *,
        thread_id: str,
    ) -> AgentResult:
        fallback = (
            "Review pack\n"
            f"Request: {request.description}\n"
            f"Procurement type: {request.procurement_type}/{request.subcategory}\n"
            f"Supplier: {research.supplier_name}; logistics estimated cost: {research.estimated_cost_gel:,.2f} GEL\n"
            f"Supplier evidence:\n{evidence_summary}\n\n"
            "Each department must independently approve, reject, or request information."
        )
        if not self.model_enabled:
            return AgentResult(content=fallback, used_model=False)

        return self._complete(
            system=(
                "You are the Procurement Review Pack Agent. Summarize supplied case facts only. "
                "Do not make an approval recommendation. Clearly distinguish facts, cited evidence, and "
                "missing information. Address Finance and Legal reviewers separately."
            ),
            user=fallback,
            fallback=fallback,
            thread_id=thread_id,
            component="review-pack-agent",
        )

    def draft_agreement(
        self, request: ProcurementRequest, research: SupplierResearch, *, thread_id: str
    ) -> AgentResult:
        fallback = (
            "DRAFT — NOT FOR SIGNATURE\n\n"
            "Supplier Agreement Draft\n"
            f"Supplier: {research.supplier_name}\n"
            f"Subject: {request.description}\n"
            f"Commercial offer reference: {research.offer_reference}\n"
            f"Logistics estimated cost: {research.estimated_cost_gel:,.2f} GEL\n"
            "Tax ID: [REQUIRES LOGISTICS INPUT]\n"
            "Authorized signatories: [REQUIRES LEGAL INPUT]\n"
            "Delivery and payment terms: [REQUIRES APPROVED TEMPLATE / LEGAL INPUT]\n\n"
            "This draft must be reviewed and approved by Logistics and Legal before external signature."
        )
        if not self.model_enabled:
            return AgentResult(content=fallback, used_model=False)

        return self._complete(
            system=(
                "You are the Agreement Drafting Agent. Draft only from supplied facts and retain all "
                "[REQUIRES ... INPUT] placeholders. Never invent legal clauses, signatories, tax IDs, "
                "payment terms, or delivery terms. Mark the document DRAFT — NOT FOR SIGNATURE."
            ),
            user=fallback,
            fallback=fallback,
            thread_id=thread_id,
            component="agreement-agent",
        )
