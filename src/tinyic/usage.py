"""Per-debate usage accounting and model-keyed price calculation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


COUNTER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "model_calls",
    "cached_calls",
)

# Static application data, intentionally not fetched during an offline run.
# One row per provider-catalog model (tinyic.models.registry), plus any
# research-only binding that needs a preflight estimate, keyed by model_ref in
# USD per million tokens. Local Ollama models are deliberately absent (free);
# an unknown model prices as None rather than borrowing rates.
MODEL_PRICES_AS_OF = "2026-07-14"
MODEL_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    # OpenAI
    "openai/gpt-5.6-sol": {"input": 5.00, "output": 30.00},
    "openai/gpt-5.6-terra": {"input": 2.50, "output": 15.00},
    "openai/gpt-5.6-luna": {"input": 1.00, "output": 6.00},
    "openai/gpt-5.2": {"input": 2.50, "output": 10.00},  # legacy
    # Anthropic
    "anthropic/claude-fable-5": {"input": 10.00, "output": 50.00},
    "anthropic/claude-opus-4-8": {"input": 5.00, "output": 25.00},
    "anthropic/claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "anthropic/claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    # Google (list rates as of the date above)
    "google/gemini-2.5-flash": {"input": 0.30, "output": 2.50},  # research
    "google/gemini-3.5-flash": {"input": 0.45, "output": 3.50},
    "google/gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
    "google/gemini-3.1-flash-lite": {"input": 0.10, "output": 0.40},
    # Grok (xAI)
    "grok/grok-4.5": {"input": 2.00, "output": 6.00},
    "grok/grok-4.3": {"input": 1.25, "output": 2.50},
    "grok/grok-4.20": {"input": 1.25, "output": 2.50},
    # Kimi (Moonshot AI)
    "kimi/kimi-k2.6": {"input": 0.95, "output": 4.00},
    "kimi/kimi-k2.5": {"input": 0.60, "output": 3.00},
}


def snapshot_cost_counters(client: Any) -> dict[str, Any]:
    """Take a detached snapshot when a legacy client exposes counters.

    Usage is instrumentation, not a prerequisite for a provider lane. M2's
    adapters all expose attributed usage; until then an OpenAI-compatible
    client such as the vendored Ollama client may legitimately expose none.
    """
    get_cost_stats = getattr(client, "get_cost_stats", None)
    if not callable(get_cost_stats):
        return _copy_counter_stats({})
    stats = get_cost_stats()
    if not isinstance(stats, Mapping):
        return _copy_counter_stats({})
    return _copy_counter_stats(stats)


def diff_cost_counters(
    after: Mapping[str, Any], before: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the non-negative usage accrued between two snapshots."""
    delta: dict[str, Any] = {
        field: max(0, after.get(field, 0) - before.get(field, 0))
        for field in COUNTER_FIELDS
    }

    after_by_model = after.get("by_model")
    if isinstance(after_by_model, Mapping):
        before_by_model = before.get("by_model", {})
        if not isinstance(before_by_model, Mapping):
            before_by_model = {}

        by_model: dict[str, dict[str, Any]] = {}
        for model_ref, model_after in after_by_model.items():
            if not isinstance(model_after, Mapping):
                continue
            model_before = before_by_model.get(model_ref, {})
            if not isinstance(model_before, Mapping):
                model_before = {}
            model_delta = {
                field: max(
                    0, model_after.get(field, 0) - model_before.get(field, 0)
                )
                for field in COUNTER_FIELDS
            }
            if any(model_delta.values()):
                by_model[str(model_ref)] = model_delta

        delta["by_model"] = by_model

    return delta


def scope_world_cost_stats(
    world_stats: Mapping[str, Any], baseline: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace cumulative world counters and derivatives with debate deltas."""
    result = dict(world_stats)
    cumulative = world_stats.get("base_stats", world_stats)
    if not isinstance(cumulative, Mapping):
        cumulative = {}
    scoped = diff_cost_counters(cumulative, baseline)

    if "base_stats" not in world_stats:
        return scoped

    result["base_stats"] = scoped
    num_agents = result.get("num_agents", 0)
    num_steps = result.get("num_steps", 0)
    result["per_agent"] = _per_unit(scoped, num_agents)
    result["per_step"] = _per_unit(scoped, num_steps)
    result["per_agent_per_step"] = _per_unit(
        scoped, num_agents * num_steps
    )
    return result


def estimate_model_keyed_cost(
    by_model: Mapping[str, Any],
    price_table: Mapping[str, Mapping[str, float]],
) -> float | None:
    """Price attributed tokens, returning ``None`` if any model is unknown."""
    if not by_model:
        return None

    cost = 0.0
    for model_ref, usage in by_model.items():
        rates = price_table.get(model_ref)
        if rates is None or not isinstance(usage, Mapping):
            return None
        cost += usage.get("input_tokens", 0) / 1_000_000 * rates["input"]
        cost += usage.get("output_tokens", 0) / 1_000_000 * rates["output"]
    return cost


def _copy_counter_stats(stats: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {
        field: stats.get(field, 0) for field in COUNTER_FIELDS
    }
    by_model = stats.get("by_model")
    if isinstance(by_model, Mapping):
        copied["by_model"] = {
            str(model_ref): {
                field: usage.get(field, 0) for field in COUNTER_FIELDS
            }
            for model_ref, usage in by_model.items()
            if isinstance(usage, Mapping)
        }
    return copied


def _per_unit(stats: Mapping[str, Any], divisor: int | float) -> dict | None:
    if divisor <= 0:
        return None
    return {
        field: stats.get(field, 0) / divisor for field in COUNTER_FIELDS
    }


__all__ = [
    "MODEL_PRICES_AS_OF",
    "MODEL_PRICES_USD_PER_MILLION",
    "diff_cost_counters",
    "estimate_model_keyed_cost",
    "scope_world_cost_stats",
    "snapshot_cost_counters",
]
