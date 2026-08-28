"""One-time setup for production LangGraph checkpoint tables."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from procurement_demo.checkpointing import setup_postgres_checkpoints


def main() -> None:
    load_dotenv()
    database_url = os.getenv("LANGGRAPH_DATABASE_URL")
    if not database_url:
        raise SystemExit("LANGGRAPH_DATABASE_URL is required.")
    setup_postgres_checkpoints(database_url, os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "langgraph"))
    print("LangGraph Postgres checkpoint tables are ready.")


if __name__ == "__main__":
    main()
