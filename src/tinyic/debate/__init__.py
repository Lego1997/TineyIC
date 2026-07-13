"""Debate engine for structured multi-agent investment debates."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from tinyic import __version__ as tinyic_version
from tinyic.events import EventLog, make_debate_id
from .models import DebatePhase, VoteChoice, Confidence, Vote, Scorecard, DebateResult
from .models import InvestmentMemo, MemoSection, DisagreementAnalysis, Disagreement
from .moderator import Moderator
from .orchestrator import CANONICAL_PHASE_NAMES, DebateOrchestrator
from .extraction import extract_votes, build_scorecard
from .memo import generate_memo, extract_disagreements

from tinyic.constants import MIN_PERSONAS
from tinyic.usage import (
    MODEL_PRICES_USD_PER_MILLION,
    diff_cost_counters,
    estimate_model_keyed_cost,
    scope_world_cost_stats,
    snapshot_cost_counters,
)
from tinytroupe.session import Session

logger = logging.getLogger(__name__)


def _canonical_model_ref() -> str:
    """Return the temporary M1 model identifier used before ModelBinding."""
    from tinytroupe import config_manager

    model = str(config_manager.get("model") or "unknown")
    if "/" in model:
        return model
    provider = str(config_manager.get("api_type") or "openai")
    return f"{provider}/{model}"


def _iso_millis(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _persona_display_name(persona) -> str:
    return (
        str(persona.name)
        if hasattr(persona, "name")
        else str(persona).replace("_", " ").title()
    )


def _persona_temperament(persona) -> str:
    """Read a persona's anti-sycophancy temperament (FR-4.3), balanced fallback."""
    value = getattr(persona, "temperament", None)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return "balanced"


def _debate_started_payload(
    ticker: str,
    company_name: str,
    personas,
    committee=None,
    *,
    caps: dict | None = None,
    moderator_ref: str = "rules",
) -> dict:
    """Build non-secret effective-run metadata for the schema-v1 envelope.

    When a ``committee`` (resolved model plan) is present, each persona's
    ``model_ref``/``auth_profile``/``thinking_level`` reflect its own binding —
    making per-persona heterogeneity visible in the public event — and the
    aggregator ``model_ref`` and preset name come from the committee. Without a
    committee the legacy single-model config is recorded (M1 behavior).

    ``caps`` is the moderator's resolved per-phase exchange ceiling (FR-4.2) and
    ``moderator_ref`` its identity; ``temperament`` is read per persona (FR-4.3).
    """
    from tinytroupe import config_manager
    from .moderator import DEFAULT_EXCHANGE_CAPS

    resolved_caps = dict(caps) if caps else dict(DEFAULT_EXCHANGE_CAPS)
    if committee is not None:
        persona_records = []
        for persona in personas:
            display = _persona_display_name(persona)
            binding = committee.persona_bindings.get(display)
            if binding is None:
                binding = committee.aggregator_binding
            persona_records.append(
                {
                    "name": display,
                    "model_ref": binding.model_ref,
                    "auth_profile": binding.auth_profile
                    or f"{binding.provider}:default",
                    "thinking_level": binding.thinking_level.value,
                    "temperament": _persona_temperament(persona),
                }
            )
        effective_config = {
            "preset": getattr(committee, "preset_name", "default"),
            "personas": persona_records,
            "moderator": moderator_ref,
            "aggregator": committee.aggregator_binding.model_ref,
            "caps": resolved_caps,
        }
    else:
        model_ref = _canonical_model_ref()
        provider = model_ref.split("/", 1)[0]
        thinking_level = str(config_manager.get("reasoning_effort") or "off")
        persona_records = [
            {
                "name": _persona_display_name(persona),
                "model_ref": model_ref,
                "auth_profile": f"{provider}:default",
                "thinking_level": thinking_level,
                "temperament": _persona_temperament(persona),
            }
            for persona in personas
        ]
        effective_config = {
            "preset": "default",
            "personas": persona_records,
            "moderator": moderator_ref,
            "aggregator": model_ref,
            "caps": resolved_caps,
        }
    config_hash = hashlib.sha256(
        json.dumps(
            effective_config, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "ticker": ticker,
        "company_name": company_name,
        **effective_config,
        "config_hash": f"sha256:{config_hash}",
        "tinyic_version": tinyic_version,
    }


def _data_ready_payload(data_package) -> dict:
    """Translate today's graceful-degradation package into source statuses."""
    warning_terms = {
        "financials": ("financial",),
        "filing_10k": ("10-k",),
        "filing_10q": ("10-q",),
        "news": ("news",),
        "social": ("twitter", "social", "x/"),
        "research": ("research",),
    }
    source_attributes = (
        ("financials", "financials"),
        ("filing_10k", "filing_10k"),
        ("filing_10q", "filing_10q"),
        ("news", "news"),
        ("social", "social"),
        ("research", "research_brief"),
    )
    warnings = [
        str(warning)
        for warning in (getattr(data_package, "warnings", None) or [])
    ]
    sources = []
    for source_name, attribute in source_attributes:
        value = getattr(data_package, attribute, None)
        warning = next(
            (
                item
                for item in warnings
                if any(
                    term in item.casefold()
                    for term in warning_terms[source_name]
                )
            ),
            None,
        )
        status = "ok" if value is not None else "unavailable"
        if value is not None and warning is not None:
            status = "degraded"
        elif (
            value is None
            and warning is not None
            and "not configured" in warning.casefold()
        ):
            status = "disabled_no_credential"
        source = {"name": source_name, "status": status}
        if warning is not None:
            source["warning"] = warning
        sources.append(source)

    financials = getattr(data_package, "financials", None)
    financials_summary = (
        financials.to_context_dict() if financials is not None else {}
    )
    fetched_at = getattr(data_package, "fetched_at", None)
    if not isinstance(fetched_at, datetime):
        fetched_at = datetime.now(timezone.utc)
    return {
        "sources": sources,
        "financials_summary": financials_summary,
        "description": getattr(data_package, "description", None) or "",
        "fetched_at": _iso_millis(fetched_at),
    }


def _vote_payload(vote: Vote) -> dict:
    return {
        "persona": vote.investor,
        "vote": vote.vote.value,
        "confidence": vote.confidence.value,
        "reasoning": vote.reasoning,
        "key_risks": vote.key_risks,
        "changed_mind": vote.changed_mind,
        # FR-4.4: a verdict parsed from the persona's mandated block is
        # ``structured``; an LLM-extraction fallback is ``extracted``.
        "source": vote.source,
    }


def _client_cached_tokens(client) -> int:
    """Provider cached-input tokens a binding client has accumulated, if any.

    The legacy snapshot/diff counters (``usage.COUNTER_FIELDS``) do not carry
    cached tokens, so the binding-routed aggregate/extraction path reads them
    straight off the client's own model-attributed stats.
    """
    getter = getattr(client, "get_cost_stats", None)
    if not callable(getter):
        return 0
    stats = getter()
    if not isinstance(stats, dict):
        return 0
    return int(stats.get("cached_tokens", 0) or 0)


def _emit_aggregate_usage(
    event_log: EventLog,
    *,
    purpose: str,
    usage_delta: dict,
    model_ref: str | None = None,
    cached_tokens: int = 0,
) -> None:
    if not any(
        usage_delta.get(field, 0)
        for field in (
            "input_tokens",
            "output_tokens",
            "model_calls",
            "cached_calls",
        )
    ):
        return
    if model_ref is None:
        model_ref = _canonical_model_ref()
    cost = estimate_model_keyed_cost(
        {model_ref: usage_delta}, MODEL_PRICES_USD_PER_MILLION
    )
    payload = {
        "purpose": purpose,
        "model_ref": model_ref,
        "input_tokens": int(usage_delta.get("input_tokens", 0)),
        "output_tokens": int(usage_delta.get("output_tokens", 0)),
        "cached_tokens": int(cached_tokens),
    }
    if cost is not None:
        payload["cost_usd"] = round(cost, 8)
    event_log.emit("usage", payload)


_MEMO_SECTION_KEYS = (
    "executive_summary",
    "investment_thesis",
    "key_risks",
    "valuation_discussion",
    "final_verdict",
)


def _emit_synthesis(
    event_log: EventLog,
    *,
    result: DebateResult,
    data_package,
    moderator,
    committee,
) -> None:
    """Run FR-4.5 synthesis through the aggregator and emit its artifacts.

    Routes the memo/disagreement writer through the committee's aggregator
    binding (capturing its native per-call usage as a ``memo`` usage event),
    grounds both on the moderator's structured records, attaches the results to
    ``result``, and emits the ``collapse_metric`` / ``disagreement`` /
    ``memo_section`` events. Collapse metrics are LLM-independent (derived from
    the theses + final votes), so they are emitted whenever a trajectory exists.
    """
    from tinyic.models.routing import activate as _activate_binding

    recorded_theses = getattr(moderator, "recorded_theses", None)
    theses = dict(recorded_theses) if isinstance(recorded_theses, dict) else {}

    aggregator_client = committee.aggregator
    synthesis_before = snapshot_cost_counters(aggregator_client)
    cached_before = _client_cached_tokens(aggregator_client)
    with _activate_binding(aggregator_client):
        memo = generate_memo(result, data_package, theses=theses)
        disagreement_analysis = extract_disagreements(result, theses=theses)
    synthesis_after = snapshot_cost_counters(aggregator_client)
    cached_after = _client_cached_tokens(aggregator_client)
    _emit_aggregate_usage(
        event_log,
        purpose="memo",
        usage_delta=diff_cost_counters(synthesis_after, synthesis_before),
        model_ref=committee.aggregator_binding.model_ref,
        cached_tokens=max(0, cached_after - cached_before),
    )

    result.memo = memo
    result.disagreement_analysis = disagreement_analysis

    collapse_summary = disagreement_analysis.collapse_summary
    if collapse_summary is not None:
        for shift in collapse_summary.shifts:
            event_log.emit(
                "collapse_metric",
                {
                    # The trajectory is measured opening thesis -> final verdict,
                    # so the collapse is realized at the verdict phase.
                    "persona": shift.persona,
                    "phase": "verdict",
                    "stance_before": shift.stance_before,
                    "stance_after": shift.stance_after,
                    "caved": shift.caved,
                    "note": shift.note,
                },
            )

    for disagreement in disagreement_analysis.disagreements:
        event_log.emit(
            "disagreement",
            {
                "dimension": disagreement.dimension,
                "description": disagreement.description,
                # Each side carries its transcript evidence_quote, as before.
                "sides": list(disagreement.sides),
                "resolution": disagreement.resolution,
            },
        )

    for section_key in _MEMO_SECTION_KEYS:
        section = getattr(memo, section_key)
        event_log.emit(
            "memo_section",
            {
                "section": section_key,
                "content": section.content,
                "contributing_personas": list(section.contributing_personas),
                "supporting_data": list(section.supporting_data),
            },
        )


def run_debate(
    ticker: str,
    persona_names: list[str],
    data_package=None,
    session: Session | None = None,
    event_log: EventLog | None = None,
    *,
    preset: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    da: str | None = None,
    committee=None,
    config_path=None,
    credentials=None,
) -> DebateResult:
    """Run a complete investment committee debate.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        persona_names: List of persona registry names (min 2).
        data_package: Pre-built DataPackage, or None to fetch live data.
        session: Optional caller-owned registry scope. When omitted, this
            function creates and closes an isolated session for the debate.
        event_log: Optional caller-owned event sink. When omitted, a new log
            is written under ``~/.tinyic/runs/<debate_id>.jsonl``.
        preset: Named committee preset from ``tinyic.toml`` (FR-1.4). When set
            (or ``model``/``thinking`` overrides are given, or ``committee`` is
            passed), each persona routes through its own ``ModelBinding`` and
            the aggregator handles extraction/memo. When all of these are
            ``None`` (the default), the legacy single-``client()`` path runs
            unchanged — the smallest-interpretation M2 seam; full legacy removal
            is M6.
        model: Per-debate ``provider/model`` override applied to every role.
        thinking: Per-debate thinking-level override applied to every role.
        da: Devil's-advocate override (``--da``), a persona display or registry
            name that pins the cross-exam devil's advocate; ``None`` uses the
            moderator's persisted rotation (FR-4.3).
        committee: Pre-resolved ``tinyic.models.Committee`` (programmatic/tests);
            takes precedence over ``preset``/``model``/``thinking``.
        config_path: Location of ``tinyic.toml`` (defaults to cwd / env).
        credentials: ``CredentialProvider`` for building the committee's
            transports (defaults to the environment credential provider).

    Returns:
        DebateResult with scorecard, transcript, and phase history.

    Raises:
        ValueError: If fewer than MIN_PERSONAS persona names provided.
    """
    if len(persona_names) < MIN_PERSONAS:
        raise ValueError(
            f"At least {MIN_PERSONAS} persona names required, got {len(persona_names)}"
        )

    from tinyic.personas.registry import load_persona
    from tinyic.data.pipeline import build_data_package as _build_data_package
    from tinytroupe.clients import client as resolve_client

    owns_session = session is None
    debate_session = session
    resolved_client = None
    resolved_committee = committee
    usage_baseline: dict = {}
    personas = []
    orchestrator = None
    moderator = None
    preset_caps = None
    active_event_log = event_log or EventLog(make_debate_id(ticker))
    owns_event_log = event_log is None
    run_started_at = None
    stage = "setup"
    research_usage: dict = {}

    try:
        if debate_session is None:
            debate_session = Session()
        resolved_client = resolve_client()
        usage_baseline = snapshot_cost_counters(resolved_client)

        for name in persona_names:
            personas.append(load_persona(name, session=debate_session))

        if resolved_committee is None and (
            preset is not None or model is not None or thinking is not None
        ):
            from tinyic.models import build_committee, load_preset

            resolved_preset = load_preset(preset, config_path)
            has_runtime_override = model is not None or thinking is not None
            if has_runtime_override:
                resolved_preset = resolved_preset.with_overrides(
                    model=model, thinking=thinking
                )
            persona_pairs = list(
                zip(persona_names, [persona.name for persona in personas])
            )
            resolved_committee = build_committee(
                resolved_preset,
                persona_pairs,
                credentials=credentials,
                # The config was strict-validated at load_preset; a per-debate
                # --model/--thinking override is a runtime choice the adapter
                # remaps per call (FR-1.3), so it must not fail committee build.
                validate_thinking=not has_runtime_override,
            )
            preset_caps = resolved_preset.caps

        # The moderator (FR-4.1) is a system component, not a debating voice. It
        # owns the exchange caps (FR-4.2, range-checked here), the devil's-advocate
        # rotation + override (FR-4.3), phase gating, and steering delivery. Built
        # before debate_started so the emitted caps/moderator reflect the real
        # plan, and validated against the committee so a bad --da fails fast.
        moderator = Moderator(
            caps=preset_caps,
            da_override=da,
            binding_client=(
                resolved_committee.moderator
                if resolved_committee is not None
                else None
            ),
        )
        moderator.validate_override(personas)

        if data_package is None:
            stage = "data"
            research_before = snapshot_cost_counters(resolved_client)
            data_package = _build_data_package(ticker)
            research_after = snapshot_cost_counters(resolved_client)
            research_usage = diff_cost_counters(
                research_after, research_before
            )

        run_started_at = active_event_log.now()
        active_event_log.emit(
            "debate_started",
            _debate_started_payload(
                data_package.ticker,
                data_package.company_name,
                personas,
                committee=resolved_committee,
                caps=moderator.exchange_caps,
                moderator_ref=moderator.moderator_ref,
            ),
        )
        active_event_log.emit(
            "data_ready", _data_ready_payload(data_package)
        )
        _emit_aggregate_usage(
            active_event_log,
            purpose="research",
            usage_delta=research_usage,
        )

        stage = "debate"
        orchestrator = DebateOrchestrator(
            name=f"IC-{ticker}",
            personas=personas,
            data_package=data_package,
            session=debate_session,
            event_log=active_event_log,
            committee=resolved_committee,
            moderator=moderator,
        )
        orchestrator.run_debate()

        stage = "extraction"
        if resolved_committee is not None:
            # Vote extraction is aggregation work: route it through the
            # aggregator binding and capture its native per-call usage.
            from tinyic.models.routing import activate as _activate_binding

            aggregator_client = resolved_committee.aggregator
            extraction_before = snapshot_cost_counters(aggregator_client)
            cached_before = _client_cached_tokens(aggregator_client)
            with _activate_binding(aggregator_client):
                votes = extract_votes(orchestrator)
            extraction_after = snapshot_cost_counters(aggregator_client)
            cached_after = _client_cached_tokens(aggregator_client)
            extraction_usage = diff_cost_counters(
                extraction_after, extraction_before
            )
            _emit_aggregate_usage(
                active_event_log,
                purpose="extraction",
                usage_delta=extraction_usage,
                model_ref=resolved_committee.aggregator_binding.model_ref,
                cached_tokens=max(0, cached_after - cached_before),
            )
        else:
            extraction_before = snapshot_cost_counters(resolved_client)
            votes = extract_votes(orchestrator)
            extraction_after = snapshot_cost_counters(resolved_client)
            extraction_usage = diff_cost_counters(
                extraction_after, extraction_before
            )
            _emit_aggregate_usage(
                active_event_log,
                purpose="extraction",
                usage_delta=extraction_usage,
            )

        stage = "finalization"
        scorecard = build_scorecard(votes, ticker, data_package.company_name)
        vote_payloads = [_vote_payload(vote) for vote in votes]
        for payload in vote_payloads:
            active_event_log.emit("vote_recorded", payload)
        active_event_log.emit(
            "scorecard",
            {
                "votes": vote_payloads,
                "consensus": (
                    scorecard.consensus.value
                    if scorecard.consensus is not None
                    else None
                ),
                "bull_count": scorecard.bull_count,
                "bear_count": scorecard.bear_count,
                "hold_count": scorecard.hold_count,
            },
        )

        transcript = orchestrator.pretty_current_interactions(
            max_content_length=None
        )
        if resolved_committee is not None:
            # Binding-routed usage is captured per binding client at the adapter
            # boundary; the debate rollup sums those model-attributed counters
            # directly (no process-global snapshot diff, closing M1's
            # concurrent-contamination handoff).
            cost_stats = {
                "base_stats": resolved_committee.aggregate_cost_stats(),
                "model_ref": resolved_committee.aggregator_binding.model_ref,
            }
        else:
            if callable(getattr(resolved_client, "get_cost_stats", None)):
                world_cost_stats = orchestrator.get_cost_stats()
            else:
                # Legacy OpenAI-compatible clients (notably upstream Ollama) do
                # not expose counters. Instrumentation must remain non-load-
                # bearing until M2 adapters provide attributed usage uniformly.
                world_cost_stats = {
                    "base_stats": snapshot_cost_counters(resolved_client),
                    "num_agents": len(orchestrator.agents),
                    "num_steps": len(orchestrator._phase_history),
                }
            cost_stats = scope_world_cost_stats(
                world_cost_stats, usage_baseline
            )
            # Legacy counters are not model-attributed. Capture the effective
            # debate model alongside the scoped snapshot so later rendering
            # never prices a switched model as GPT-5.2 by accident.
            cost_stats["model_ref"] = _canonical_model_ref()

        result = DebateResult(
            ticker=ticker,
            company_name=data_package.company_name,
            scorecard=scorecard,
            phases_completed=orchestrator._phase_history,
            transcript=transcript,
            cost_stats=cost_stats,
        )

        # --- FR-4.5: MoA memo + disagreement analytics ---------------------
        # The memo/disagreement writer IS the committee's aggregator binding, so
        # this synthesis stage exists only when the model layer is in play. It
        # grounds the aggregator on the structured records (theses + verdicts),
        # emits the memo/disagreement artifacts, and computes the collapse
        # metrics (per-persona stance trajectory + "caved" flags). The legacy
        # client-only path keeps its prior behavior (memo produced by the
        # caller), so its recorded logs are byte-unchanged.
        if resolved_committee is not None:
            stage = "synthesis"
            _emit_synthesis(
                active_event_log,
                result=result,
                data_package=data_package,
                moderator=moderator,
                committee=resolved_committee,
            )

        canonical_by_value = {
            phase.value: CANONICAL_PHASE_NAMES[phase]
            for phase in CANONICAL_PHASE_NAMES
        }
        phases_completed = [
            canonical_by_value[phase]
            for phase in orchestrator._phase_history
            if phase in canonical_by_value
        ]
        ended_at = active_event_log.now()
        duration_s = max(
            0.0, (ended_at - run_started_at).total_seconds()
        )
        active_event_log.emit(
            "debate_completed",
            {
                "phases_completed": phases_completed,
                "duration_s": round(duration_s, 3),
                # M1's result document is the authoritative event document;
                # M6 assembles the single JSON result view from this path.
                "result_ref": str(active_event_log.path.resolve()),
            },
        )
        return result
    except Exception as exc:
        if not active_event_log.terminal:
            try:
                if not active_event_log.started:
                    failed_ticker = str(
                        getattr(data_package, "ticker", None) or ticker
                    ).upper()
                    failed_company = str(
                        getattr(data_package, "company_name", None)
                        or failed_ticker
                    )
                    # If setup/data resolution fails before normal lifecycle
                    # emission, persist the intended committee and a
                    # provisional ticker-derived company name. The terminal
                    # error then makes the failed run replayable.
                    active_event_log.emit(
                        "debate_started",
                        _debate_started_payload(
                            failed_ticker,
                            failed_company,
                            personas or persona_names,
                            committee=resolved_committee,
                            caps=(
                                moderator.exchange_caps
                                if moderator is not None
                                else None
                            ),
                            moderator_ref=(
                                moderator.moderator_ref
                                if moderator is not None
                                else "rules"
                            ),
                        ),
                    )
                active_event_log.emit(
                    "debate_error",
                    {
                        "stage": stage,
                        # Raw provider/auth exceptions can contain request or
                        # credential material. The detailed exception remains
                        # in application logs, never in the public event file.
                        "message": (
                            f"{type(exc).__name__} while running {stage}"
                        ),
                        "recoverable": False,
                    },
                )
            except Exception:
                logger.exception("Could not persist terminal debate_error")
        raise
    finally:
        try:
            if orchestrator is not None:
                orchestrator.dispose()
            if debate_session is not None:
                for persona in personas:
                    debate_session.unregister_agent(persona)
                if owns_session:
                    debate_session.close()
        finally:
            if owns_event_log and active_event_log is not None:
                active_event_log.close()


def get_debate_cost_stats(result: DebateResult) -> dict:
    """Get formatted cost statistics from a completed debate.

    Returns dict with token counts and estimated USD cost.
    Returns empty stats dict if cost_stats not available.
    """
    if not result.cost_stats:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_calls": 0,
            "cached_calls": 0,
            "cached_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

    stats = result.cost_stats
    base = stats.get("base_stats", stats)  # handle both TinyWorld and raw formats

    by_model = base.get("by_model")
    if isinstance(by_model, dict):
        estimated_cost = estimate_model_keyed_cost(
            by_model, MODEL_PRICES_USD_PER_MILLION
        )
    else:
        model_ref = (
            base.get("model_ref")
            or base.get("model")
            or stats.get("model_ref")
        )
        if model_ref is None:
            # Compatibility for legacy TinyWorld counters, which predate model
            # attribution and were always priced as the configured GPT-5.2.
            model_ref = "openai/gpt-5.2"
        estimated_cost = estimate_model_keyed_cost(
            {
                model_ref: {
                    "input_tokens": base.get("input_tokens", 0),
                    "output_tokens": base.get("output_tokens", 0),
                }
            },
            MODEL_PRICES_USD_PER_MILLION,
        )

    return {
        "input_tokens": base.get("input_tokens", 0),
        "output_tokens": base.get("output_tokens", 0),
        "total_tokens": base.get("total_tokens", 0),
        "model_calls": base.get("model_calls", 0),
        "cached_calls": base.get("cached_calls", 0),
        "cached_tokens": base.get("cached_tokens", 0),
        "estimated_cost_usd": (
            round(estimated_cost, 4) if estimated_cost is not None else None
        ),
    }


__all__ = [
    "DebateOrchestrator",
    "DebatePhase",
    "VoteChoice",
    "Confidence",
    "Vote",
    "Scorecard",
    "DebateResult",
    "InvestmentMemo",
    "MemoSection",
    "DisagreementAnalysis",
    "Disagreement",
    "extract_votes",
    "build_scorecard",
    "run_debate",
    "get_debate_cost_stats",
    "generate_memo",
    "extract_disagreements",
]
