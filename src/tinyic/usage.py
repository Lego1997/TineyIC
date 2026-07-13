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
# M2's provider catalogs can extend this table while retaining the same
# model-keyed calculation contract. These rates preserve TinyIC's existing
# GPT-5.2 compatibility estimate.
MODEL_PRICES_AS_OF = "2026-07-12"
MODEL_PRICES_USD_PER_MILLION: dict[str, dict[str, float]] = {
    "openai/gpt-5.2": {"input": 2.50, "output": 10.00},
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
