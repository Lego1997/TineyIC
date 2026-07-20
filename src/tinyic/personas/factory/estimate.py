"""Shared research-lane planning and cost estimation (CLI + studio).

Moved verbatim out of ``persona_cli`` so the CLI cost gate and the studio
estimate endpoint price a research run from one source of truth. The Google
budget-safe lane comment in :func:`configured_bindings` is the reason the
fallback list exists at all.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DEFAULT_SEARCH_BUDGET",
    "FIXED_INPUT_TOKEN_ESTIMATE",
    "FIXED_OUTPUT_TOKEN_ESTIMATE",
    "KIMI_DEFAULT_SEARCH_BUDGET",
    "PER_SEARCH_INPUT_TOKEN_ESTIMATE",
    "PER_SEARCH_OUTPUT_TOKEN_ESTIMATE",
    "RECOMMENDED_RESEARCH_MODELS",
    "SYNTHESIS_CALLS",
    "close_backend",
    "configured_bindings",
    "estimated_cost",
    "planned_budget",
    "priced_search_calls",
    "reason_code",
]

SYNTHESIS_CALLS = 7  # five dossier sections + persona synthesis + verification
FIXED_INPUT_TOKEN_ESTIMATE = 60_000
FIXED_OUTPUT_TOKEN_ESTIMATE = 12_000
PER_SEARCH_INPUT_TOKEN_ESTIMATE = 2_000
PER_SEARCH_OUTPUT_TOKEN_ESTIMATE = 1_000
DEFAULT_SEARCH_BUDGET = 12
KIMI_DEFAULT_SEARCH_BUDGET = 16
RECOMMENDED_RESEARCH_MODELS = (
    ("openai", "openai/gpt-5.6-sol"),
    ("grok", "grok/grok-4.5"),
    ("google", "google/gemini-2.5-flash"),
    ("kimi", "kimi/kimi-k2.6"),
)


def reason_code(exc: BaseException, fallback: str = "persona_command_error") -> str:
    """Read an exception's ``reason_code`` attribute, with a safe fallback."""
    code = getattr(exc, "reason_code", None)
    return str(code) if isinstance(code, str) and code else fallback


def close_backend(backend: Any) -> None:
    """Best-effort close of a research backend's transport."""
    close = getattr(backend, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def configured_bindings(preset: Any, binding_class: Any) -> tuple[Any, ...]:
    """Return configured lanes plus one recommended fallback per provider.

    Preset bindings retain precedence within their provider.  Fallbacks ensure
    an onboarded Grok, Google, or Kimi API key remains usable even when the
    ordinary committee preset happens to be OpenAI-only.
    """
    bindings: list[Any] = []
    if getattr(preset.default, "model", None):
        bindings.append(
            preset.default.to_binding(where=f"preset {preset.name!r} default")
        )
    for persona_name in sorted(preset.personas):
        bindings.append(preset.persona_binding(persona_name))
    bindings.append(preset.aggregator_binding())
    bindings.append(preset.moderator_binding())

    distinct: list[Any] = []
    seen: set[tuple[str, str | None]] = set()
    for binding in bindings:
        key = (str(binding.model_ref), binding.auth_profile)
        if key not in seen:
            seen.add(key)
            distinct.append(binding)
    configured_providers = {str(binding.provider).casefold() for binding in distinct}
    for provider, model_ref in RECOMMENDED_RESEARCH_MODELS:
        if provider in configured_providers:
            # Configured Google debate bindings normally use Gemini 3, which
            # cannot enforce persona research's billable-search ceiling. Keep
            # their preset precedence, then append the budget-safe 2.5 lane so
            # selection can skip an ineligible Gemini 3 binding.
            if provider != "google" or any(
                str(binding.model_ref) == model_ref for binding in distinct
            ):
                continue
        distinct.append(binding_class(model_ref))
    return tuple(distinct)


def planned_budget(provider: str, max_searches: int | None) -> int:
    """Effective per-run search budget for *provider* (Kimi bills echo rounds)."""
    if max_searches is not None:
        return max_searches
    return KIMI_DEFAULT_SEARCH_BUDGET if provider == "kimi" else DEFAULT_SEARCH_BUDGET


def priced_search_calls(
    provider: str, planned_query_count: int, effective_max_searches: int
) -> int:
    """Billable search units: Gemini 2.5 bills one grounded prompt per request."""
    if provider == "google":
        return min(effective_max_searches, planned_query_count)
    return effective_max_searches


def estimated_cost(
    model_ref: str,
    provider: str,
    planned_searches: int,
    *,
    search_fees: dict[str, float] | Any,
    price_table: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Estimate list-price spend from the frozen search fee and token budget."""
    input_tokens = (
        FIXED_INPUT_TOKEN_ESTIMATE
        + planned_searches * PER_SEARCH_INPUT_TOKEN_ESTIMATE
    )
    output_tokens = (
        FIXED_OUTPUT_TOKEN_ESTIMATE
        + planned_searches * PER_SEARCH_OUTPUT_TOKEN_ESTIMATE
    )
    search_fee = float(search_fees.get(provider, 0.0)) * planned_searches
    rates = price_table.get(model_ref)
    token_cost = None
    total_cost = None
    if rates is not None:
        token_cost = (
            input_tokens / 1_000_000 * float(rates["input"])
            + output_tokens / 1_000_000 * float(rates["output"])
        )
        total_cost = search_fee + token_cost
    return {
        "planned_searches": planned_searches,
        "planned_calls": planned_searches + SYNTHESIS_CALLS,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "search_fee_usd": round(search_fee, 6),
        "token_cost_usd": None if token_cost is None else round(token_cost, 6),
        "total_cost_usd": None if total_cost is None else round(total_cost, 6),
    }
