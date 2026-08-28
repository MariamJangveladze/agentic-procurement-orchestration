"""Load the fictional demo policy/supplier corpus into Supabase pgvector.

Run only with synthetic or approved content. This command sends the supplied
text to the configured embedding provider and writes vectors through the
server-side Supabase credential.
"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from dotenv import load_dotenv

from procurement_demo.knowledge import DEMO_DOCUMENTS
from procurement_demo.supabase_store import SupabaseSettings, _vector_literal
from supabase import create_client


def main() -> None:
    load_dotenv()
    settings = SupabaseSettings.from_environment()
    if not settings:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env first.")
    if not __import__("os").getenv("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY to generate semantic embeddings.")

    client = create_client(settings.url, settings.service_role_key)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    splitter = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)

    for source in DEMO_DOCUMENTS:
        source_key = source.metadata["document_id"]
        document = (
            client.table("knowledge_documents")
            .upsert(
                {
                    "source_key": source_key,
                    "title": source_key.replace("-", " ").title(),
                    "document_type": source.metadata.get("type", "reference"),
                    "content": source.page_content,
                    "metadata": source.metadata,
                },
                on_conflict="source_key",
            )
            .execute()
            .data[0]
        )
        client.table("knowledge_chunks").delete().eq("document_id", document["id"]).execute()
        chunks = splitter.split_documents([source])
        vectors = embeddings.embed_documents([chunk.page_content for chunk in chunks])
        client.table("knowledge_chunks").insert(
            [
                {
                    "document_id": document["id"],
                    "chunk_index": index,
                    "content": chunk.page_content,
                    "metadata": chunk.metadata,
                    "embedding": _vector_literal(vector),
                }
                for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
            ]
        ).execute()
        print(f"Indexed {source_key} ({len(chunks)} chunks)")


if __name__ == "__main__":
    main()
