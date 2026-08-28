"""Keep unit tests local, deterministic, and free of personal credentials."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_external_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_MODEL", "")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
