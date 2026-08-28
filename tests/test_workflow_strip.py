"""The case workflow strip must not claim approvals the band never required."""

from procurement_demo.api import _workflow


def states(status: str, amount: float | None) -> dict[str, str]:
    research = {} if amount is None else {"estimated_cost_gel": amount}
    return {step["key"]: step["state"] for step in _workflow(status, research)}


def labels(status: str, amount: float | None) -> dict[str, str]:
    research = {} if amount is None else {"estimated_cost_gel": amount}
    return {step["key"]: step["label"] for step in _workflow(status, research)}


def test_closed_case_below_the_ceo_band_never_shows_a_ceo_step():
    for amount in (450, 4_999):
        assert "ceo" not in states("closed", amount), amount


def test_closed_case_in_the_ceo_band_shows_ceo_as_done():
    assert states("closed", 8_500)["ceo"] == "done"


def test_delivery_stage_does_not_mark_later_steps_done():
    # awaiting_delivery has no explicit mapping before this fix, so every step
    # including a CEO approval that never ran was marked done.
    strip = states("awaiting_delivery", 450)
    assert strip["procurement"] == "current"
    assert strip["controls"] == "done"
    assert "ceo" not in strip


def test_authorization_label_follows_the_band():
    assert labels("pending_logistics_authorization", 450)["authorization"] == "Head of Logistics approval"
    assert labels("pending_logistics_authorization", 4_999)["authorization"] == "Director of Logistics approval"


def test_unknown_amount_keeps_ceo_visible_but_not_done():
    strip = states("pending_head_approval", None)
    assert strip["head"] == "current"
    assert strip["ceo"] == "upcoming"


def test_rejected_case_does_not_mark_every_step_done():
    assert set(states("rejected", 8_500).values()) == {"upcoming"}


def test_board_band_replaces_ceo_with_the_board_step():
    strip = states("pending_board_flow_configuration", 25_000)
    assert strip["board"] == "current"
    assert "ceo" not in strip
