"""Assemble the single ``tinyic result`` document from a recorded event log.

The append-only JSONL stream is the authoritative debate record; this module is
the read-side that fulfils the schema's compatibility promise #3 — *"``tinyic
result <id> --json`` returns a single document assembled from the log (scorecard,
memo, disagreements, usage rollup) — agents that don't want streaming can consume
only this."* It is a pure function over parsed events, so it works on any log a
renderer could replay, including a truncated one (a crash mid-debate), which it
reports with an explicit ``status`` rather than raising.

It also owns run discovery for ``tinyic runs list``: the runs directory is the
same ``~/.tinyic/runs`` (``TINYIC_RUNS_DIR`` override) that :class:`EventLog`
writes to, so listing and result assembly never diverge from where debates land.
"""

from __future__ import annotations

import os
from pathlib import Path

from tinyic.tui.events import Event, read_events

__all__ = [
    "RESULT_SCHEMA_VERSION",
    "runs_dir",
    "resolve_run_path",
    "list_runs",
    "debate_status",
    "exit_code_for_status",
    "assemble_result",
    "load_result",
]

RESULT_SCHEMA_VERSION = 1

_MEMO_SECTION_ORDER = (
    "executive_summary",
    "investment_thesis",
    "key_risks",
    "valuation_discussion",
    "final_verdict",
)


def runs_dir() -> Path:
    """The directory debates are written to (mirrors :class:`EventLog`)."""
    return Path(
        os.environ.get("TINYIC_RUNS_DIR", str(Path.home() / ".tinyic" / "runs"))
    ).expanduser()


def resolve_run_path(id_or_path: str) -> Path:
    """Resolve a debate id or a path to a run log's file path.

    A value that already points at an existing file is used verbatim (so
    ``tinyic result ./run.jsonl`` works); otherwise it is treated as a debate id
    and looked up as ``<runs_dir>/<id>.jsonl``.
    """
    candidate = Path(id_or_path).expanduser()
    if candidate.is_file():
        return candidate
    if id_or_path.endswith(".jsonl"):
        return candidate
    return runs_dir() / f"{id_or_path}.jsonl"


def debate_status(events: list[Event]) -> str:
    """Classify a debate's terminal state from its event stream.

    * ``complete`` — ends with ``debate_completed``.
    * ``partial`` — ends with ``debate_error`` after at least one phase completed.
    * ``error`` — ends with ``debate_error`` with no phase completed (nothing ran).
    * ``incomplete`` — no terminal event (a truncated / crashed log).
    """
    if not events:
        return "incomplete"
    terminal = events[-1]
    phases_completed = sum(1 for e in events if e.type == "phase_completed")
    if terminal.type == "debate_completed":
        return "complete"
    if terminal.type == "debate_error":
        return "partial" if phases_completed >= 1 else "error"
    return "incomplete"


def exit_code_for_status(status: str) -> int:
    """Map a debate status to the FR-6.2 process exit code (0 / 2 / 3)."""
    if status == "complete":
        return 0
    if status == "partial":
        return 2
    # ``error`` (nothing completed) and ``incomplete`` (never finished) both mean
    # the debate did not produce a usable result: a setup/failed-to-run outcome.
    return 3


def _usage_rollup(events: list[Event]) -> dict:
    """Sum ``usage`` events into per-purpose / per-model / total token+cost rollups."""
    totals = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    cost_total = 0.0
    has_cost = False
    by_purpose: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for event in events:
        if event.type != "usage":
            continue
        payload = event.payload
        inp = int(payload.get("input_tokens", 0) or 0)
        out = int(payload.get("output_tokens", 0) or 0)
        cached = int(payload.get("cached_tokens", 0) or 0)
        totals["input_tokens"] += inp
        totals["output_tokens"] += out
        totals["cached_tokens"] += cached
        cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)):
            cost_total += float(cost)
            has_cost = True
        for bucket_key, mapping in (
            (str(payload.get("purpose", "unknown")), by_purpose),
            (str(payload.get("model_ref", "unknown")), by_model),
        ):
            bucket = mapping.setdefault(
                bucket_key,
                {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            bucket["input_tokens"] += inp
            bucket["output_tokens"] += out
            bucket["cached_tokens"] += cached
            if isinstance(cost, (int, float)):
                bucket["cost_usd"] = round(bucket["cost_usd"] + float(cost), 8)
    total = dict(totals)
    total["cost_usd"] = round(cost_total, 8) if has_cost else None
    # Subscription-lane message-window snapshots (dollar-free meters).
    windows = [
        e.payload for e in events if e.type == "usage_window"
    ]
    return {
        "total": total,
        "by_purpose": by_purpose,
        "by_model": by_model,
        "windows": windows,
    }


def assemble_result(events: list[Event]) -> dict:
    """Assemble the ``tinyic result`` document from parsed events.

    Tolerant by construction: missing sections simply stay empty / null, and a
    truncated log yields ``status="incomplete"`` with whatever was recorded.
    """
    started = next((e for e in events if e.type == "debate_started"), None)
    completed = next((e for e in events if e.type == "debate_completed"), None)
    error = next((e for e in events if e.type == "debate_error"), None)
    scorecard_event = next(
        (e for e in events if e.type == "scorecard"), None
    )

    votes = [dict(e.payload) for e in events if e.type == "vote_recorded"]
    disagreements = [dict(e.payload) for e in events if e.type == "disagreement"]
    collapse = [dict(e.payload) for e in events if e.type == "collapse_metric"]

    memo_sections = {
        e.payload.get("section"): {
            "content": e.payload.get("content"),
            "contributing_personas": list(
                e.payload.get("contributing_personas", []) or []
            ),
            "supporting_data": list(e.payload.get("supporting_data", []) or []),
        }
        for e in events
        if e.type == "memo_section"
    }
    memo = {
        section: memo_sections[section]
        for section in _MEMO_SECTION_ORDER
        if section in memo_sections
    }

    debate_id = ""
    for event in events:
        if event.debate_id:
            debate_id = event.debate_id
            break

    document: dict = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "debate_id": debate_id,
        "status": debate_status(events),
    }
    if started is not None:
        payload = started.payload
        document.update(
            {
                "ticker": payload.get("ticker"),
                "company_name": payload.get("company_name"),
                "preset": payload.get("preset"),
                "personas": list(payload.get("personas", []) or []),
                "moderator": payload.get("moderator"),
                "aggregator": payload.get("aggregator"),
                "caps": payload.get("caps"),
                "tinyic_version": payload.get("tinyic_version"),
            }
        )
    if completed is not None:
        document["phases_completed"] = list(
            completed.payload.get("phases_completed", []) or []
        )
        document["duration_s"] = completed.payload.get("duration_s")
    else:
        document["phases_completed"] = [
            e.payload.get("phase")
            for e in events
            if e.type == "phase_completed"
        ]

    if scorecard_event is not None:
        payload = scorecard_event.payload
        document["scorecard"] = {
            "consensus": payload.get("consensus"),
            "bull_count": payload.get("bull_count"),
            "bear_count": payload.get("bear_count"),
            "hold_count": payload.get("hold_count"),
            "votes": list(payload.get("votes", []) or []),
        }
    else:
        document["scorecard"] = None

    document["votes"] = votes
    document["memo"] = memo
    document["disagreements"] = disagreements
    document["collapse_metrics"] = collapse
    document["usage"] = _usage_rollup(events)
    if error is not None:
        document["error"] = {
            "stage": error.payload.get("stage"),
            "message": error.payload.get("message"),
            "recoverable": error.payload.get("recoverable"),
        }
    return document


def load_result(id_or_path: str) -> dict:
    """Read a run log by id/path and return its assembled result document.

    Raises ``FileNotFoundError`` when no matching run exists — callers map that to
    the CLI's setup-error exit code.
    """
    path = resolve_run_path(id_or_path)
    if not path.is_file():
        raise FileNotFoundError(f"no debate run found for {id_or_path!r} (looked at {path})")
    events = read_events(path)
    document = assemble_result(events)
    if not document.get("debate_id"):
        document["debate_id"] = path.stem
    return document


def list_runs(directory: str | Path | None = None) -> list[dict]:
    """Summarize every recorded run, newest-modified first.

    Each entry is a small, secret-free summary suitable for ``tinyic runs list``:
    ``debate_id``, ``ticker``, ``company_name``, ``status``, ``consensus``,
    ``phases_completed`` count, ``started_at`` and ``path``. A malformed or empty
    log is summarized as best-effort rather than skipping it silently.
    """
    base = Path(directory).expanduser() if directory is not None else runs_dir()
    if not base.is_dir():
        return []
    summaries: list[tuple[float, dict]] = []
    for path in base.glob("*.jsonl"):
        try:
            events = read_events(path)
        except Exception:  # pragma: no cover - defensive against unreadable files
            events = []
        started = next((e for e in events if e.type == "debate_started"), None)
        scorecard_event = next((e for e in events if e.type == "scorecard"), None)
        debate_id = next((e.debate_id for e in events if e.debate_id), path.stem)
        summary = {
            "debate_id": debate_id,
            "path": str(path),
            "ticker": started.payload.get("ticker") if started else None,
            "company_name": started.payload.get("company_name") if started else None,
            "preset": started.payload.get("preset") if started else None,
            "status": debate_status(events),
            "phases_completed": sum(
                1 for e in events if e.type == "phase_completed"
            ),
            "started_at": events[0].ts if events else None,
            "consensus": (
                scorecard_event.payload.get("consensus")
                if scorecard_event is not None
                else None
            ),
        }
        try:
            mtime = path.stat().st_mtime
        except OSError:  # pragma: no cover - race with deletion
            mtime = 0.0
        summaries.append((mtime, summary))
    summaries.sort(key=lambda item: item[0], reverse=True)
    return [summary for _mtime, summary in summaries]
