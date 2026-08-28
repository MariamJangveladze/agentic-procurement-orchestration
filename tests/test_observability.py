from procurement_demo.observability import trace_config


def test_trace_metadata_excludes_business_content():
    config = trace_config("PR-123", component="procurement-workflow", model_enabled=False)

    assert "hitl" in config["tags"]
    assert config["metadata"] == {
        "case_id": "PR-123",
        "component": "procurement-workflow",
        "workflow_version": "0.1.0",
        "environment": "local-demo",
        "model_enabled": False,
    }
