"""Offline regression for the ``tinyic result`` usage-cost rollup (TIC-012).

Subscription-lane ``usage`` events omit ``cost_usd`` entirely (it is added only
when a billable cost exists). The total already reports ``None`` in that case;
these tests pin that the per-model / per-purpose buckets carry the *same*
semantics — ``None`` when no event in the bucket carried a numeric cost, else the
rounded sum of the numeric ones — so a pure-subscription bucket never renders a
manufactured ``$0.0000`` that reads as "free".
"""

from __future__ import annotations

from tinyic.result import assemble_result
from tinyic.tui.events import Event

_UNSET = object()


def _usage(
    model_ref: str,
    purpose: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    cost_usd: object = _UNSET,
) -> Event:
    payload: dict = {
        "model_ref": model_ref,
        "purpose": purpose,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_tokens": cached_tokens,
    }
    if cost_usd is not _UNSET:
        payload["cost_usd"] = cost_usd
    return Event(
        v=1,
        seq=1,
        ts="2026-07-15T00:00:00.000Z",
        debate_id="d-usage",
        type="usage",
        payload=payload,
    )


def test_subscription_only_buckets_report_none_cost_not_manufactured_zero():
    events = [
        _usage("openai/gpt-5.6-sol", "turn", input_tokens=100, output_tokens=50),
        _usage("grok/grok-4.5", "turn", input_tokens=200, output_tokens=80),
    ]

    usage = assemble_result(events)["usage"]

    assert usage["total"]["cost_usd"] is None
    assert usage["by_purpose"]["turn"]["cost_usd"] is None
    assert usage["by_model"]["openai/gpt-5.6-sol"]["cost_usd"] is None
    assert usage["by_model"]["grok/grok-4.5"]["cost_usd"] is None
    # Token counters still aggregate normally.
    assert usage["total"]["input_tokens"] == 300
    assert usage["by_model"]["grok/grok-4.5"]["output_tokens"] == 80


def test_mixed_lane_buckets_sum_only_numeric_costs_and_keep_subscription_none():
    events = [
        _usage("openai/gpt-5.6-sol", "turn", cost_usd=0.25),
        _usage("grok/grok-4.5", "turn"),  # subscription lane, no cost_usd key
        _usage("openai/gpt-5.6-sol", "memo", cost_usd=0.05),
    ]

    usage = assemble_result(events)["usage"]

    assert usage["total"]["cost_usd"] == 0.30
    # The API-key model bucket sums both of its billable events.
    assert usage["by_model"]["openai/gpt-5.6-sol"]["cost_usd"] == 0.30
    # The pure-subscription model bucket stays None, never 0.0.
    assert usage["by_model"]["grok/grok-4.5"]["cost_usd"] is None
    # Per-purpose buckets sum only the numeric costs seen for that purpose.
    assert usage["by_purpose"]["turn"]["cost_usd"] == 0.25
    assert usage["by_purpose"]["memo"]["cost_usd"] == 0.05
