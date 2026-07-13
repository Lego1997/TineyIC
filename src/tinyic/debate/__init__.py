"""Debate engine for structured multi-agent investment debates."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from tinyic import __version__ as tinyic_version
from tinyic.events import EventLog, make_debate_id
from .models import DebatePhase, VoteChoice, Confidence, Vote, Scorecard, DebateResult
from .models import InvestmentMemo, MemoSection, DisagreementAnalysis, Disagreement
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


def _debate_started_payload(ticker: str, company_name: str, personas) -> dict:
    """Build non-secret effective-run metadata for the schema-v1 envelope."""
    from tinytroupe import config_manager

    model_ref = _canonical_model_ref()
    provider = model_ref.split("/", 1)[0]
    thinking_level = str(config_manager.get("reasoning_effort") or "off")
    caps = {"opening": 1, "cross_exam": 1, "rebuttal": 1, "verdict": 1}
    persona_records = [
        {
            "name": (
                str(persona.name)
                if hasattr(persona, "name")
                else str(persona).replace("_", " ").title()
            ),
            "model_ref": model_ref,
            "auth_profile": f"{provider}:default",
            "thinking_level": thinking_level,
            # Temperament becomes configured in M4. M1 records the absence
            # explicitly rather than inventing a behavioral classification.
            "temperament": "unspecified",
        }
        for persona in personas
    ]
    effective_config = {
        "preset": "default",
        "personas": persona_records,
        "moderator": "rules",
        "aggregator": model_ref,
        "caps": caps,
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
        "source": "extracted",
    }


def _emit_aggregate_usage(
    event_log: EventLog,
    *,
    purpose: str,
    usage_delta: dict,
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
    model_ref = _canonical_model_ref()
    cost = estimate_model_keyed_cost(
        {model_ref: usage_delta}, MODEL_PRICES_USD_PER_MILLION
    )
    payload = {
        "purpose": purpose,
        "model_ref": model_ref,
        "input_tokens": int(usage_delta.get("input_tokens", 0)),
        "output_tokens": int(usage_delta.get("output_tokens", 0)),
        "cached_tokens": 0,
    }
    if cost is not None:
        payload["cost_usd"] = round(cost, 8)
    event_log.emit("usage", payload)


def run_debate(
    ticker: str,
    persona_names: list[str],
    data_package=None,
    session: Session | None = None,
    event_log: EventLog | None = None,
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
    usage_baseline: dict = {}
    personas = []
    orchestrator = None
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
        )
        orchestrator.run_debate()

        stage = "extraction"
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
        # debate model alongside the scoped snapshot so later rendering never
        # prices a switched model as GPT-5.2 by accident.
        cost_stats["model_ref"] = _canonical_model_ref()

        result = DebateResult(
            ticker=ticker,
            company_name=data_package.company_name,
            scorecard=scorecard,
            phases_completed=orchestrator._phase_history,
            transcript=transcript,
            cost_stats=cost_stats,
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
