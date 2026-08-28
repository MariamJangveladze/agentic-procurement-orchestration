"""Central, privacy-aware LangSmith trace configuration."""

from __future__ import annotations

WORKFLOW_VERSION = "0.1.0"


def trace_config(case_id: str, *, component: str, model_enabled: bool | None = None) -> dict:
    """Return trace metadata without sending requester, supplier, or document content as metadata.

    LangSmith captures execution inputs/outputs when tracing is enabled. Use only
    fictional or approved anonymized data in this local demo. A production deployment
    must make its own data-residency and redaction decision before enabling it.
    """

    metadata = {
        "case_id": case_id,
        "component": component,
        "workflow_version": WORKFLOW_VERSION,
        "environment": "local-demo",
    }
    if model_enabled is not None:
        metadata["model_enabled"] = model_enabled
    return {
        "tags": ["logistics-procurement", "hitl", "local-demo", f"component:{component}"],
        "metadata": metadata,
    }
