"""Trusted, local supplier knowledge used by the evidence agent.

The local demo uses only documents committed in this package. A production system
would replace these with permission-filtered supplier, legal, and policy sources.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Protocol

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter


DEMO_DOCUMENTS = [
    Document(
        page_content=(
            "Supplier: Atlas Office Systems. Supplier ID: SUP-001. Status: existing and active. "
            "Legal pack: tax registration, beneficial-owner declaration, signed master agreement v3. "
            "Last refreshed: 2026-06-20. Applicable categories: equipment and stationery."
        ),
        metadata={"document_id": "supplier-atlas-profile", "supplier": "Atlas Office Systems", "type": "supplier_profile"},
    ),
    Document(
        page_content=(
            "Supplier: GreenClean LLC. Supplier ID: SUP-014. Status: existing and active. "
            "Legal pack: tax registration, supplier compliance clearance 2026-01, service agreement v2. "
            "Applicable categories: cleaning service and repairs."
        ),
        metadata={"document_id": "supplier-greenclean-profile", "supplier": "GreenClean LLC", "type": "supplier_profile"},
    ),
    Document(
        page_content=(
            "New supplier checklist: commercial offer, tax registration, beneficial-owner declaration, "
            "payment account confirmation, supplier compliance review request, conflict-of-interest declaration, "
            "and legal agreement template are required before procurement start."
        ),
        metadata={"document_id": "policy-new-supplier-checklist", "type": "policy"},
    ),
    Document(
        page_content=(
            "Agreement drafting policy: use only approved templates. Do not invent commercial terms, "
            "signatories, tax IDs, delivery terms, or payment terms. Missing values must be labelled "
            "as requiring Logistics or Legal input."
        ),
        metadata={"document_id": "policy-agreement-drafting", "type": "policy"},
    ),
]


class KnowledgeBase(Protocol):
    """The narrow retrieval contract used by the LangChain agents."""

    def search(self, query: str, *, supplier_name: str | None = None, k: int = 4) -> list[Document]: ...

    @staticmethod
    def format_citations(documents: Iterable[Document]) -> str: ...


@dataclass
class LocalKnowledgeBase:
    """A small, transparent retrieval layer with safe local fallback."""

    documents: list[Document]
    vector_store: InMemoryVectorStore | None = None

    @classmethod
    def demo(cls) -> "LocalKnowledgeBase":
        splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
        documents = splitter.split_documents(DEMO_DOCUMENTS)
        # Semantic RAG is opt-in because it sends the trusted local snippets to
        # the embedding provider. Without a key, the demo stays entirely local.
        if os.getenv("OPENAI_API_KEY"):
            embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
            return cls(documents=documents, vector_store=InMemoryVectorStore.from_documents(documents, embeddings))
        return cls(documents=documents)

    def search(self, query: str, *, supplier_name: str | None = None, k: int = 4) -> list[Document]:
        """Return relevant trusted snippets using deterministic local matching.

        This keeps the default demo fully local. The same method is the boundary
        where a production pgvector/Chroma retriever can be installed.
        """

        if self.vector_store:
            return self.vector_store.similarity_search(query, k=k)

        terms = {token.lower() for token in query.replace("/", " ").split() if len(token) > 2}
        if supplier_name:
            terms.update(token.lower() for token in supplier_name.split() if len(token) > 2)

        def score(document: Document) -> int:
            content = document.page_content.lower()
            metadata = " ".join(str(value).lower() for value in document.metadata.values())
            return sum(term in content or term in metadata for term in terms)

        ranked = sorted(self.documents, key=score, reverse=True)
        return [document for document in ranked if score(document) > 0][:k]

    @staticmethod
    def format_citations(documents: Iterable[Document]) -> str:
        parts = []
        for document in documents:
            document_id = document.metadata.get("document_id", "unknown-document")
            parts.append(f"[{document_id}] {document.page_content}")
        return "\n\n".join(parts) or "No matching trusted evidence found."


def configured_knowledge_base() -> KnowledgeBase:
    """Use pgvector only when the trusted server environment is configured.

    Keeping the deterministic local fallback makes the existing laptop demo
    usable without credentials and avoids sending content to an embedding model
    unless the operator deliberately configures that integration.
    """

    from .supabase_store import SupabaseKnowledgeBase, SupabaseSettings

    settings = SupabaseSettings.from_environment()
    if settings and os.getenv("OPENAI_API_KEY"):
        return SupabaseKnowledgeBase(settings)
    return LocalKnowledgeBase.demo()
