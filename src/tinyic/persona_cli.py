"""CLI boundary for the layered persona registry and cited research factory.

The argument parser imports this module only after dispatch.  Project modules
that transitively load TinyTroupe or keyring are imported inside the command
functions so ``tinyic --help`` and ``tinyic persona --help`` stay lightweight.
All provider progress and diagnostics go to STDERR; list/show JSON is one clean
document on STDOUT.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO


PERSONA_DOCUMENT_SCHEMA_VERSION = 1
_SYNTHESIS_CALLS = 7  # five dossier sections + persona synthesis + verification
_FIXED_INPUT_TOKEN_ESTIMATE = 60_000
_FIXED_OUTPUT_TOKEN_ESTIMATE = 12_000
_PER_SEARCH_INPUT_TOKEN_ESTIMATE = 2_000
_PER_SEARCH_OUTPUT_TOKEN_ESTIMATE = 1_000
_DEFAULT_SEARCH_BUDGET = 12
_KIMI_DEFAULT_SEARCH_BUDGET = 16
_RECOMMENDED_RESEARCH_MODELS = (
    ("openai", "openai/gpt-5.6-sol"),
    ("grok", "grok/grok-4.5"),
    ("google", "google/gemini-3.5-flash"),
    ("kimi", "kimi/kimi-k2.6"),
)


def _quiet_import_registry():
    """Import the registry without leaking TinyTroupe's legacy import banner."""
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        from .personas import registry

    return registry


def _quiet_import_factory():
    """Import factory symbols without dependency chatter on either stream."""
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        from .personas import factory

    return factory


def _reason_code(exc: BaseException, fallback: str = "persona_command_error") -> str:
    code = getattr(exc, "reason_code", None)
    return str(code) if isinstance(code, str) and code else fallback


def _diagnostic(err: TextIO, code: str, message: str) -> None:
    """Write one concise, reason-coded CLI diagnostic."""
    clean = " ".join(str(message).split())
    prefix = f"{code}:"
    if clean.casefold().startswith(prefix.casefold()):
        clean = clean[len(prefix) :].lstrip()
    print(f"tinyic: {code}: {clean}", file=err)


def _source_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(_source_count(item) for item in value.values())
    return 0


def _dossier_candidate(agent_path: Path, slug: str) -> Path:
    return agent_path.with_name(f"{slug}.dossier.md")


def _persona_summary(slug: str, origin: str, path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("persona"), dict):
        raise ValueError("agent file does not contain a persona object")
    persona = raw["persona"]
    tinyic = raw.get("tinyic") if isinstance(raw.get("tinyic"), dict) else {}
    generation = (
        tinyic.get("generation")
        if isinstance(tinyic.get("generation"), dict)
        else {}
    )
    name = str(persona.get("name") or "").strip()
    if not name:
        raise ValueError("agent file does not contain a persona name")
    dossier = _dossier_candidate(path, slug)
    sources = tinyic.get("sources")
    return {
        "slug": slug,
        "name": name,
        "epithet": str(tinyic.get("epithet") or "").strip() or None,
        "temperament": (
            str(tinyic.get("temperament") or raw.get("temperament") or "").strip()
            or None
        ),
        "philosophy_hook": (
            str(tinyic.get("philosophy_hook") or "").strip() or None
        ),
        "origin": origin,
        "source_count": _source_count(sources),
        "quality": str(generation.get("quality") or "").strip() or None,
        "model_ref": str(generation.get("model_ref") or "").strip() or None,
        "generated_date": str(generation.get("date") or "").strip() or None,
        "agent_path": str(path),
        "dossier_path": str(dossier) if dossier.is_file() else None,
    }


def _registry_summaries(registry_module: Any) -> list[dict[str, Any]]:
    entries = registry_module.list_personas(with_origin=True)
    summaries: list[dict[str, Any]] = []
    for entry in entries:
        summaries.append(
            _persona_summary(
                str(entry["slug"]),
                str(entry["origin"]),
                Path(str(entry["path"])),
            )
        )
    return summaries


def list_personas_command(
    *,
    json_mode: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
    registry_module: Any | None = None,
) -> int:
    """Print the stable-sorted layered registry without loading agent objects."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    registry_module = registry_module or _quiet_import_registry()
    try:
        summaries = _registry_summaries(registry_module)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _diagnostic(err, "persona_read_error", str(exc))
        return 3

    if json_mode:
        records = [
            {
                "slug": item["slug"],
                "name": item["name"],
                "epithet": item["epithet"],
                "origin": item["origin"],
            }
            for item in summaries
        ]
        document = {
            "schema_version": PERSONA_DOCUMENT_SCHEMA_VERSION,
            "personas": records,
        }
        print(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            file=out,
        )
        return 0

    if not summaries:
        print("No personas found.", file=out)
        return 0
    print(f"{'SLUG':<24} {'ORIGIN':<9} NAME", file=out)
    for item in summaries:
        print(
            f"{item['slug']:<24} {item['origin']:<9} {item['name']}",
            file=out,
        )
    return 0


def show_persona_command(
    slug: str,
    *,
    json_mode: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
    registry_module: Any | None = None,
) -> int:
    """Print a metadata card for one built-in or user persona."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    registry_module = registry_module or _quiet_import_registry()
    try:
        registry = registry_module.registry_snapshot()
        if slug not in registry:
            _diagnostic(err, "unknown_persona", f"no persona named {slug!r}")
            return 3
        path = Path(registry[slug])
        origin = "built_in" if slug in registry_module.BUILTIN_PERSONAS else "user"
        summary = _persona_summary(slug, origin, path)
    except (KeyError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _diagnostic(err, "persona_read_error", str(exc))
        return 3

    if json_mode:
        document = {
            "schema_version": PERSONA_DOCUMENT_SCHEMA_VERSION,
            "persona": summary,
        }
        print(
            json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            file=out,
        )
        return 0

    print(f"{summary['name']} ({summary['slug']})", file=out)
    if summary["epithet"]:
        print(f"  {summary['epithet']}", file=out)
    print(f"Origin: {summary['origin']}", file=out)
    if summary["temperament"]:
        print(f"Temperament: {summary['temperament']}", file=out)
    if summary["philosophy_hook"]:
        print(f"Philosophy: {summary['philosophy_hook']}", file=out)
    print(f"Sources: {summary['source_count']}", file=out)
    if summary["quality"]:
        print(f"Research quality: {summary['quality']}", file=out)
    if summary["model_ref"]:
        print(f"Research model: {summary['model_ref']}", file=out)
    print(f"Agent: {summary['agent_path']}", file=out)
    print(f"Dossier: {summary['dossier_path'] or 'not available'}", file=out)
    return 0


def _configured_bindings(preset: Any, binding_class: Any) -> tuple[Any, ...]:
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
    for provider, model_ref in _RECOMMENDED_RESEARCH_MODELS:
        if provider not in configured_providers:
            distinct.append(binding_class(model_ref))
    return tuple(distinct)


def _estimated_cost(
    model_ref: str,
    provider: str,
    planned_searches: int,
    *,
    search_fees: dict[str, float] | Any,
    price_table: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Estimate list-price spend from the frozen search fee and token budget."""
    input_tokens = (
        _FIXED_INPUT_TOKEN_ESTIMATE
        + planned_searches * _PER_SEARCH_INPUT_TOKEN_ESTIMATE
    )
    output_tokens = (
        _FIXED_OUTPUT_TOKEN_ESTIMATE
        + planned_searches * _PER_SEARCH_OUTPUT_TOKEN_ESTIMATE
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
        "planned_calls": planned_searches + _SYNTHESIS_CALLS,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "search_fee_usd": round(search_fee, 6),
        "token_cost_usd": None if token_cost is None else round(token_cost, 6),
        "total_cost_usd": None if total_cost is None else round(total_cost, 6),
    }


def _print_cost_estimate(
    err: TextIO, model_ref: str, provider: str, estimate: dict[str, Any]
) -> None:
    total = estimate["total_cost_usd"]
    if total is None:
        amount = (
            f"at least ${estimate['search_fee_usd']:.4f} in search-tool fees; "
            "token price unavailable"
        )
    else:
        amount = f"${total:.4f} list price"
    print(f"Estimated cost: {amount} using {model_ref}", file=err)
    print(
        "  "
        f"{estimate['planned_searches']} searches + {_SYNTHESIS_CALLS} "
        f"synthesis/verification calls; about {estimate['input_tokens']:,} input "
        f"and {estimate['output_tokens']:,} output tokens",
        file=err,
    )


def _is_interactive(stream: TextIO) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except OSError:
        return False


def _confirm(stdin: TextIO, err: TextIO) -> bool:
    err.write("Continue with persona research? [y/N] ")
    err.flush()
    answer = stdin.readline()
    return answer.strip().casefold() in {"y", "yes"}


def _close_backend(backend: Any) -> None:
    close = getattr(backend, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def research_persona(
    name: str,
    *,
    model: str | None = None,
    slug: str | None = None,
    max_searches: int | None = None,
    yes: bool = False,
    force: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
    stdin: TextIO | None = None,
    credentials: Any | None = None,
    credentials_factory: Callable[[], Any] | None = None,
    backend_maker: Callable[..., Any] | None = None,
    backend_selector: Callable[..., Any] | None = None,
    preset_loader: Callable[[], Any] | None = None,
    factory_class: Any | None = None,
) -> int:
    """Run the collision-safe, cost-gated cited research pipeline."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    stdin = sys.stdin if stdin is None else stdin
    factory_module = _quiet_import_factory()
    registry_module = _quiet_import_registry()

    # Collision checks precede credential resolution and backend construction;
    # in particular, no provider call can occur for a protected/existing slug.
    try:
        resolved_slug = factory_module.slugify(slug if slug is not None else name)
    except Exception as exc:
        _diagnostic(err, _reason_code(exc, "invalid_persona_slug"), str(exc))
        return 3
    if resolved_slug in factory_module.PROTECTED_BUILTIN_SLUGS:
        _diagnostic(
            err,
            "builtin_persona_collision",
            f"{resolved_slug!r} is a protected built-in persona; choose --slug",
        )
        return 3
    output_dir = Path(registry_module.personas_dir())
    agent_path = output_dir / f"{resolved_slug}.agent.json"
    dossier_path = output_dir / f"{resolved_slug}.dossier.md"
    if not force and (agent_path.exists() or dossier_path.exists()):
        _diagnostic(
            err,
            "persona_exists",
            f"persona {resolved_slug!r} already exists; rerun with --force to replace it",
        )
        return 3

    backend: Any | None = None
    try:
        # Import the model/auth stack only after local preflight succeeds.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            from .auth import AuthManager
            from .models.binding import ModelBinding
            from .models.presets import load_preset
            from .models.research import (
                SEARCH_TOOL_FEES_USD,
                make_research_backend,
                select_research_backend,
            )
            from .usage import MODEL_PRICES_USD_PER_MILLION

        if credentials is None:
            make_credentials = credentials_factory or AuthManager.from_config
            credentials = make_credentials()
        if model is not None:
            binding = ModelBinding(model)
            make_backend = backend_maker or make_research_backend
            backend_kwargs = (
                {} if max_searches is None else {"max_searches": max_searches}
            )
            backend = make_backend(binding, credentials, **backend_kwargs)
        else:
            load = preset_loader or load_preset
            bindings = _configured_bindings(load(), ModelBinding)
            select_backend = backend_selector or select_research_backend
            backend_kwargs = (
                {} if max_searches is None else {"max_searches": max_searches}
            )
            backend = select_backend(bindings, credentials, **backend_kwargs)
    except Exception as exc:
        code = _reason_code(exc)
        message = str(exc)
        if code == "no_search_capable_lane":
            message = (
                "no verified API-key search lane; run 'tinyic onboard' for "
                "openai, grok, google, or kimi"
            )
        _diagnostic(err, code, message)
        return 3

    try:
        try:
            provider = str(getattr(backend, "provider", "")).casefold()
            effective_max_searches = (
                _KIMI_DEFAULT_SEARCH_BUDGET
                if max_searches is None and provider == "kimi"
                else (
                    _DEFAULT_SEARCH_BUDGET
                    if max_searches is None
                    else max_searches
                )
            )
            plan = factory_module.plan_queries(
                name, max_searches=effective_max_searches
            )
            logical_searches = len(plan.queries)
            # Kimi's budget counts every client/server echo round across the
            # run; the other providers make one billable tool call per logical
            # query. Price the actual bounded unit for each lane.
            priced_search_calls = (
                effective_max_searches if provider == "kimi" else logical_searches
            )
            model_ref = str(getattr(backend, "model_ref", model or "unknown"))
            estimate = _estimated_cost(
                model_ref,
                provider,
                priced_search_calls,
                search_fees=SEARCH_TOOL_FEES_USD,
                price_table=MODEL_PRICES_USD_PER_MILLION,
            )
        except Exception as exc:
            _diagnostic(err, _reason_code(exc, "invalid_research_budget"), str(exc))
            return 3

        _print_cost_estimate(err, model_ref, provider, estimate)
        if not yes:
            if not _is_interactive(stdin):
                _diagnostic(
                    err,
                    "confirmation_required",
                    "non-interactive input cannot confirm cost; rerun with --yes",
                )
                return 3
            if not _confirm(stdin, err):
                _diagnostic(err, "research_cancelled", "persona research cancelled")
                return 3

        def progress(stage: str) -> None:
            label = str(stage).replace("_", " ").replace(":", ": ")
            print(f"tinyic persona: {label}", file=err)

        build_factory = factory_class or factory_module.PersonaFactory
        try:
            result = build_factory(backend, progress=progress).run(
                factory_module.ResearchRequest(
                    investor_name=name,
                    output_dir=output_dir,
                    slug=resolved_slug,
                    max_searches=effective_max_searches,
                    force=force,
                )
            )
        except Exception as exc:
            _diagnostic(err, _reason_code(exc), str(exc))
            return 3

        usage = result.usage
        actual_cost = getattr(usage, "cost_usd", None)
        cost_text = (
            f"${actual_cost:.4f}"
            if isinstance(actual_cost, (int, float))
            else "unavailable"
        )
        print(f"Created persona {result.slug!r} for {result.investor_name}.", file=out)
        print(f"Agent: {result.agent_path}", file=out)
        print(f"Dossier: {result.dossier_path}", file=out)
        print(
            f"Evidence: {result.source_count} sources across "
            f"{result.domain_count} domains ({result.quality})",
            file=out,
        )
        print(
            f"Usage: {getattr(usage, 'calls', 0)} calls; actual cost {cost_text}",
            file=out,
        )
        return 0
    finally:
        _close_backend(backend)


__all__ = [
    "PERSONA_DOCUMENT_SCHEMA_VERSION",
    "list_personas_command",
    "research_persona",
    "show_persona_command",
]
