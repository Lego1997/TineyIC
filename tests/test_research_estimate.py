"""Direct unit tests for the shared research-estimate helpers."""

from __future__ import annotations

from tinyic.personas.factory import estimate


class _FakeBinding:
    def __init__(self, model_ref, auth_profile=None):
        self.model_ref = model_ref
        self.provider = model_ref.split("/")[0]
        self.auth_profile = auth_profile


class _FakeDefault:
    model = "openai/gpt-5.6-sol"

    def to_binding(self, where=""):
        return _FakeBinding(self.model)


class _FakePreset:
    name = "fake"
    default = _FakeDefault()
    personas = {"warren_buffett": object()}

    def persona_binding(self, _name):
        return _FakeBinding("openai/gpt-5.6-sol")

    def aggregator_binding(self):
        return _FakeBinding("anthropic/claude-opus-4-8")

    def moderator_binding(self):
        return _FakeBinding("openai/gpt-5.6-sol")


def test_planned_budget_defaults_and_override():
    assert estimate.planned_budget("openai", None) == 12
    assert estimate.planned_budget("kimi", None) == 16
    assert estimate.planned_budget("google", 4) == 4


def test_priced_search_calls_google_is_capped_by_plan():
    assert estimate.priced_search_calls("google", planned_query_count=6, effective_max_searches=12) == 6
    assert estimate.priced_search_calls("openai", planned_query_count=6, effective_max_searches=12) == 12


def test_estimated_cost_arithmetic():
    result = estimate.estimated_cost(
        "fake/model",
        "openai",
        12,
        search_fees={"openai": 0.01},
        price_table={"fake/model": {"input": 2.0, "output": 8.0}},
    )
    assert result["planned_searches"] == 12
    assert result["planned_calls"] == 19  # 12 + SYNTHESIS_CALLS
    assert result["input_tokens"] == 84_000
    assert result["output_tokens"] == 24_000
    assert result["search_fee_usd"] == 0.12
    assert result["token_cost_usd"] == 0.36
    assert result["total_cost_usd"] == 0.48


def test_estimated_cost_unknown_price_table():
    result = estimate.estimated_cost(
        "unknown/model", "kimi", 3, search_fees={"kimi": 0.005}, price_table={}
    )
    assert result["token_cost_usd"] is None
    assert result["total_cost_usd"] is None
    assert result["search_fee_usd"] == 0.015


def test_configured_bindings_dedups_and_appends_fallback_lanes():
    bindings = estimate.configured_bindings(_FakePreset(), _FakeBinding)
    refs = [str(binding.model_ref) for binding in bindings]
    assert refs == [
        "openai/gpt-5.6-sol",       # preset default (persona/moderator dedup)
        "anthropic/claude-opus-4-8",  # aggregator
        "grok/grok-4.5",            # fallback lanes in priority order
        "google/gemini-2.5-flash",
        "kimi/kimi-k2.6",
    ]


def test_configured_bindings_appends_budget_safe_google_lane():
    class _GoogleDefault:
        model = "google/gemini-3-pro"

        def to_binding(self, where=""):
            return _FakeBinding(self.model)

    preset = _FakePreset()
    preset.default = _GoogleDefault()
    preset.personas = {}
    preset.aggregator_binding = lambda: _FakeBinding("google/gemini-3-pro")
    preset.moderator_binding = lambda: _FakeBinding("google/gemini-3-pro")
    refs = [str(b.model_ref) for b in estimate.configured_bindings(preset, _FakeBinding)]
    assert refs[0] == "google/gemini-3-pro"
    assert "google/gemini-2.5-flash" in refs  # budget-safe lane appended


def test_reason_code_reads_attribute_with_fallback():
    class _Coded(Exception):
        reason_code = "insufficient_sources"

    assert estimate.reason_code(_Coded()) == "insufficient_sources"
    assert estimate.reason_code(ValueError("x")) == "persona_command_error"
    assert estimate.reason_code(ValueError("x"), "research_failed") == "research_failed"
