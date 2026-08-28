"""Durable LangGraph checkpoint lifecycle for the trusted workflow API."""

from __future__ import annotations

import os
import re
from typing import Any

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

load_dotenv()


class CheckpointRuntime:
    """Keep one checkpointer connection alive for the process lifetime.

    ``LANGGRAPH_DATABASE_URL`` is a server-only direct or session-pooler
    PostgreSQL URL for Supabase. Tests and the local UI-only demo use memory.
    """

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self.database_url = database_url or os.getenv("LANGGRAPH_DATABASE_URL")
        self.schema = schema or os.getenv("LANGGRAPH_CHECKPOINT_SCHEMA", "langgraph")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.schema):
            raise ValueError("LANGGRAPH_CHECKPOINT_SCHEMA must be a valid PostgreSQL identifier.")
        self._connection: Any | None = None
        self._checkpointer: Any | None = None

    @property
    def is_durable(self) -> bool:
        return bool(self.database_url)

    def get_checkpointer(self) -> Any:
        if self._checkpointer is not None:
            return self._checkpointer
        if not self.database_url:
            self._checkpointer = InMemorySaver()
            return self._checkpointer

        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row

        self._connection = Connection.connect(
            self.database_url,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            options=f"-c search_path={self.schema},public",
        )
        self._checkpointer = PostgresSaver(self._connection)
        return self._checkpointer

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._checkpointer = None


def setup_postgres_checkpoints(database_url: str, schema: str = "langgraph") -> None:
    """Create LangGraph's checkpoint tables once, as a deployment operation."""

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("LANGGRAPH_CHECKPOINT_SCHEMA must be a valid PostgreSQL identifier.")
    with Connection.connect(
        database_url,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
        options=f"-c search_path={schema},public",
    ) as connection:
        checkpointer = PostgresSaver(connection)
        checkpointer.setup()
