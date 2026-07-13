"""M1 acceptance tests for the public TinyIC v1 JSONL event stream.

These tests intentionally describe the smallest event API needed by the
approved schema.  M1 emits completed THINK/TALK actions with their full text;
token-level ``*_delta`` events are deferred until the M2 provider adapters.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tinyic.debate as debate_module
import tinytroupe.clients as clients_module
from tinyic.data.models import DataPackage
from tinyic.debate.models import Confidence, Vote, VoteChoice
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson
from tinytroupe.session import Session


DEBATE_ID = "aapl-20260713-a3f2"
FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "m1_mocked_debate.jsonl"


def _events_api():
    """Load the proposed minimal M1 API lazily so all red tests collect."""
    module = importlib.import_module("tinyic.events")
    return module.EventEnvelope, module.EventLog, module.read_event_log


def test_debate_id_matches_documented_filename_contract():
    """Run IDs use ticker, UTC date, and a filesystem-safe short suffix."""
    module = importlib.import_module("tinyic.events")

    debate_id = module.make_debate_id(
        "BRK.B", now=FIXED_NOW, suffix="A3f2"
    )

    assert debate_id == "brk.b-20260713-a3f2"


def _started_payload() -> dict:
    return {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "preset": "default",
        "personas": [
            {
                "name": "Warren Buffett",
                "model_ref": "openai/gpt-5.2",
                "auth_profile": "openai:default",
                "thinking_level": "xhigh",
                "temperament": "independent",
            },
            {
                "name": "Benjamin Graham",
                "model_ref": "openai/gpt-5.2",
                "auth_profile": "openai:default",
                "thinking_level": "xhigh",
                "temperament": "contrarian",
            },
        ],
        "moderator": "rules",
        "aggregator": "openai/gpt-5.2",
        "caps": {
            "opening": 1,
            "cross_exam": 2,
            "rebuttal": 1,
            "verdict": 1,
        },
        "config_hash": "sha256:test-config",
        "tinyic_version": "0.1.0",
    }


def _data_ready_payload() -> dict:
    return {
        "sources": [
            {"name": "financials", "status": "ok"},
            {
                "name": "social",
                "status": "disabled_no_credential",
                "warning": "XAI_API_KEY is not configured",
            },
        ],
        "financials_summary": {"pe_ratio": {"value": 31.2, "unit": "x"}},
        "description": "Consumer technology company.",
        "fetched_at": "2026-07-13T01:02:03.000Z",
    }


def _completed_payload() -> dict:
    return {
        "phases_completed": ["opening", "cross_exam", "rebuttal", "verdict"],
        "duration_s": 12.5,
        "result_ref": "/tmp/aapl-result.json",
    }


def _new_log(tmp_path: Path):
    _, EventLog, _ = _events_api()
    return EventLog(
        debate_id=DEBATE_ID,
        path=tmp_path / "override" / f"{DEBATE_ID}.jsonl",
        clock=lambda: FIXED_NOW,
    )


def test_event_envelope_validates_v1_and_known_payload_schema():
    """Envelope fields and required known-event payload fields are validated."""
    EventEnvelope, _, _ = _events_api()
    raw = {
        "v": 1,
        "seq": 1,
        "ts": "2026-07-13T01:02:03.000Z",
        "debate_id": DEBATE_ID,
        "type": "debate_started",
        "payload": _started_payload(),
    }

    event = EventEnvelope.model_validate(raw)
    assert event.v == 1
    assert event.seq == 1
    assert event.debate_id == DEBATE_ID
    assert event.type == "debate_started"
    assert event.payload["ticker"] == "AAPL"

    missing_seq = dict(raw)
    missing_seq.pop("seq")
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(missing_seq)

    wrong_version = dict(raw, v=2)
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(wrong_version)

    naive_timestamp = dict(raw, ts="2026-07-13T01:02:03.000")
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(naive_timestamp)

    incomplete_payload = dict(raw, payload={"ticker": "AAPL"})
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(incomplete_payload)

    null_required = dict(
        raw, payload={**_started_payload(), "company_name": None}
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(null_required)

    non_finite_duration = dict(
        raw,
        type="debate_completed",
        payload={**_completed_payload(), "duration_s": float("nan")},
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(non_finite_duration)

    invalid_duration_type = dict(
        raw,
        type="debate_completed",
        payload={**_completed_payload(), "duration_s": "eventually"},
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(invalid_duration_type)

    invalid_source = dict(
        raw,
        type="data_ready",
        payload={
            **_data_ready_payload(),
            "sources": [{"name": None, "status": "ok"}],
        },
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(invalid_source)

    invalid_fetched_at = dict(
        raw,
        type="data_ready",
        payload={**_data_ready_payload(), "fetched_at": "yesterday"},
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(invalid_fetched_at)

    invalid_usage_ref = dict(
        raw,
        type="turn_completed",
        payload={
            "turn_id": "turn-0001",
            "persona": "Warren Buffett",
            "phase": "opening",
            "interrupted": False,
            "usage_ref": {"seq": 2},
        },
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(invalid_usage_ref)

    invalid_consensus = dict(
        raw,
        type="scorecard",
        payload={
            "votes": [],
            "consensus": "MAYBE",
            "bull_count": 0,
            "bear_count": 0,
            "hold_count": 0,
        },
    )
    with pytest.raises(ValueError):
        EventEnvelope.model_validate(invalid_consensus)


def test_event_log_assigns_contiguous_sequence_numbers(tmp_path):
    """The writer owns sequence assignment and never leaves gaps."""
    with _new_log(tmp_path) as event_log:
        emitted = [
            event_log.emit("debate_started", _started_payload()),
            event_log.emit("data_ready", _data_ready_payload()),
            event_log.emit(
                "phase_started",
                {"phase": "opening", "index": 0},
            ),
        ]

    seqs = [event.seq for event in emitted]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))

    _, _, read_event_log = _events_api()
    persisted = read_event_log(event_log.path)
    assert [event.seq for event in persisted] == seqs


def test_event_log_uses_path_override_appends_and_flushes_each_event(
    tmp_path, monkeypatch
):
    """An emitted line is immediately readable and later emits preserve it."""
    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))
    event_log = _new_log(tmp_path)

    with event_log:
        event_log.emit("debate_started", _started_payload())
        assert event_log.path == tmp_path / "override" / f"{DEBATE_ID}.jsonl"
        assert event_log.path.is_file()

        # Read through a separate file descriptor before close: emit must flush.
        first_bytes = event_log.path.read_bytes()
        first_lines = first_bytes.decode("utf-8").splitlines()
        assert len(first_lines) == 1
        assert json.loads(first_lines[0])["type"] == "debate_started"

        event_log.emit("data_ready", _data_ready_payload())
        second_bytes = event_log.path.read_bytes()
        assert second_bytes.startswith(first_bytes)
        assert len(second_bytes.decode("utf-8").splitlines()) == 2

    default_path = fake_home / ".tinyic" / "runs" / f"{DEBATE_ID}.jsonl"
    assert not default_path.exists()


def test_fresh_writer_resumes_sequence_and_preserves_terminal_state(tmp_path):
    _, EventLog, read_event_log = _events_api()
    path = tmp_path / f"{DEBATE_ID}.jsonl"

    with EventLog(DEBATE_ID, path=path, clock=lambda: FIXED_NOW) as first:
        first.emit("debate_started", _started_payload())
        first.emit("data_ready", _data_ready_payload())

    with EventLog(DEBATE_ID, path=path, clock=lambda: FIXED_NOW) as resumed:
        event = resumed.emit(
            "phase_started", {"phase": "opening", "index": 0}
        )
        assert event.seq == 3
        resumed.emit("debate_completed", _completed_payload())

    terminal_bytes = path.read_bytes()
    with EventLog(DEBATE_ID, path=path, clock=lambda: FIXED_NOW) as terminal:
        with pytest.raises(RuntimeError, match="terminal"):
            terminal.emit("data_ready", _data_ready_payload())

    assert path.read_bytes() == terminal_bytes
    assert [event.seq for event in read_event_log(path)] == [1, 2, 3, 4]


def test_fresh_writer_rejects_a_log_for_another_debate_id(tmp_path):
    _, EventLog, _ = _events_api()
    path = tmp_path / f"{DEBATE_ID}.jsonl"
    with EventLog(DEBATE_ID, path=path, clock=lambda: FIXED_NOW) as event_log:
        event_log.emit("debate_started", _started_payload())

    with pytest.raises(ValueError, match="belongs to debate"):
        with EventLog(
            "msft-20260713-b4e3", path=path, clock=lambda: FIXED_NOW
        ):
            pass


def test_event_log_defaults_to_private_run_directory(tmp_path, monkeypatch):
    """Without an override, persistence follows the documented home path."""
    fake_home = tmp_path / "home"
    monkeypatch.delenv("TINYIC_RUNS_DIR", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    _, EventLog, _ = _events_api()

    event_log = EventLog(debate_id=DEBATE_ID, clock=lambda: FIXED_NOW)

    assert event_log.path == (
        fake_home / ".tinyic" / "runs" / f"{DEBATE_ID}.jsonl"
    )


def test_minimal_consumer_preserves_order_and_ignores_unknown_events(tmp_path):
    """Consumers preserve append order and tolerate additive v1 extensions."""
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit(
            "future_optional_event",
            {"new_field": "renderers must ignore this", "nested": {"v": 2}},
        )
        event_log.emit("debate_completed", _completed_payload())

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert [event.type for event in events] == [
        "debate_started",
        "future_optional_event",
        "debate_completed",
    ]
    assert events[1].payload["new_field"] == "renderers must ignore this"


@pytest.mark.parametrize(
    ("terminal_type", "terminal_payload"),
    [
        ("debate_completed", _completed_payload()),
        (
            "debate_error",
            {"stage": "debate", "message": "mocked failure", "recoverable": False},
        ),
    ],
)
def test_terminal_event_is_last_and_rejects_further_emission(
    tmp_path, terminal_type, terminal_payload
):
    """Both documented terminal event types close the append contract."""
    with _new_log(tmp_path) as event_log:
        with pytest.raises(ValueError, match="debate_started"):
            event_log.emit("data_ready", _data_ready_payload())

        event_log.emit("debate_started", _started_payload())
        event_log.emit(terminal_type, terminal_payload)
        with pytest.raises(RuntimeError, match="terminal"):
            event_log.emit("data_ready", _data_ready_payload())

    raw_events = [
        json.loads(line)
        for line in event_log.path.read_text(encoding="utf-8").splitlines()
    ]
    assert raw_events[-1]["type"] == terminal_type
    assert [event["seq"] for event in raw_events] == list(
        range(raw_events[0]["seq"], raw_events[0]["seq"] + len(raw_events))
    )


def test_credentials_are_recursively_redacted_before_persistence(tmp_path):
    """Credential-shaped keys and tokens never reach events or JSONL bytes."""
    openai_secret = "sk-proj-THIS_MUST_NOT_LEAK"
    refresh_secret = "refresh-token-THIS_MUST_NOT_LEAK"
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event = event_log.emit(
            "debate_error",
            {
                "stage": "auth",
                "message": f"Authorization: Bearer {openai_secret}",
                "recoverable": False,
                "details": {
                    "api_key": openai_secret,
                    "refresh_token": refresh_secret,
                },
            },
        )

    serialized_event = json.dumps(event.model_dump(mode="json"), sort_keys=True)
    persisted = event_log.path.read_text(encoding="utf-8")
    for secret in (openai_secret, refresh_secret):
        assert secret not in serialized_event
        assert secret not in persisted
    assert "[REDACTED]" in persisted


def test_prompts_provider_bodies_and_common_token_shapes_are_not_events(
    tmp_path,
):
    """Unknown additive fields cannot bypass the event security boundary."""
    google_key = "AIzaSyDUMMY0123456789abcdefghijklmnop"
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signature0123456789"
    )
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit(
            "debate_error",
            {
                "stage": "provider",
                "message": f"request failed for {google_key} and {jwt}",
                "recoverable": False,
                "full_prompt": f"SYSTEM SECRET PROMPT {google_key}",
                "provider_exception": {"body": f"raw body {jwt}"},
                "aliases": [google_key, jwt],
            },
        )

    persisted = event_log.path.read_text(encoding="utf-8")
    assert "SYSTEM SECRET PROMPT" not in persisted
    assert "raw body" not in persisted
    assert google_key not in persisted
    assert jwt not in persisted


def test_configured_opaque_credentials_are_redacted_from_ordinary_text(
    tmp_path, monkeypatch
):
    secret = "opaque-azure-key-123456789"
    monkeypatch.setenv("AZURE_OPENAI_KEY", secret)

    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit(
            "debate_error",
            {
                "stage": "provider",
                "message": f"provider rejected {secret}",
                "recoverable": False,
            },
        )

    assert secret not in event_log.path.read_text(encoding="utf-8")


def test_opaque_credentials_are_redacted_in_all_json_compatible_shapes(
    tmp_path,
):
    """Non-provider-specific secrets cannot bypass structural redaction."""
    secret = "opaque-azure-secret-1234567890"
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit(
            "debate_error",
            {
                "stage": "auth",
                "message": (
                    f"Authorization: Basic {secret} "
                    f"https://provider.test/call?api_key={secret}"
                ),
                "recoverable": False,
                "details": {
                    "token": secret,
                    "memberships": {secret},
                },
                "list_alias": [secret],
            },
        )

    persisted = event_log.path.read_text(encoding="utf-8")
    assert secret not in persisted
    assert persisted.count("[REDACTED]") >= 4


def test_replay_ignores_only_a_crash_truncated_final_json_line(tmp_path):
    """A process crash may leave one incomplete tail after valid events."""
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit("data_ready", _data_ready_payload())

    with event_log.path.open("a", encoding="utf-8") as stream:
        stream.write('{"v":1,"seq":3,"type":"talk_completed"')

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)

    assert [event.type for event in events] == ["debate_started", "data_ready"]

    _, EventLog, _ = _events_api()
    with pytest.raises(ValueError, match="crash-truncated"):
        with EventLog(
            debate_id=DEBATE_ID,
            path=event_log.path,
            clock=lambda: FIXED_NOW,
        ):
            pass


def test_replay_ignores_a_crash_truncated_final_multibyte_character(tmp_path):
    """A torn UTF-8 codepoint in the final append preserves the valid prefix."""
    with _new_log(tmp_path) as event_log:
        event_log.emit("debate_started", _started_payload())
        event_log.emit("data_ready", _data_ready_payload())

    partial_character = "跌".encode("utf-8")[:1]
    with event_log.path.open("ab") as stream:
        stream.write(b'{"v":1,"seq":3,"payload":{"text":"')
        stream.write(partial_character)

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)

    assert [event.type for event in events] == ["debate_started", "data_ready"]


def _mock_data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        description="Consumer technology company.",
        fetched_at=FIXED_NOW,
    )


def test_data_ready_marks_sources_disabled_by_missing_credentials():
    package = _mock_data_package().model_copy(
        update={
            "warnings": [
                "X/Twitter sentiment disabled: XAI_API_KEY not configured",
                "Deep research disabled: OPENAI_API_KEY not configured",
            ]
        }
    )

    payload = debate_module._data_ready_payload(package)
    statuses = {source["name"]: source["status"] for source in payload["sources"]}

    assert statuses["social"] == "disabled_no_credential"
    assert statuses["research"] == "disabled_no_credential"


def _mock_persona_act(self, *, return_actions=False, **_kwargs):
    cognitive_state = {
        "goals": f"Evaluate AAPL as {self.name}",
        "context": ["Investment committee debate"],
        "attention": "Valuation and downside risk",
        "emotions": "Skeptical but engaged",
    }
    actions = [
        {
            "type": "THINK",
            "content": f"{self.name} privately weighs the evidence.",
            "target": "",
        },
        {
            "type": "TALK",
            "content": f"{self.name} gives a complete investment view.",
            "target": "",
        },
        {"type": "DONE", "content": "", "target": ""},
    ]
    self._actions_buffer.extend(actions)
    committed = [
        {"action": action, "cognitive_state": cognitive_state} for action in actions
    ]
    return committed if return_actions else self


class _UsageCounter:
    def __init__(self) -> None:
        self.stats = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_calls": 0,
            "cached_calls": 0,
        }

    def record_turn(self) -> None:
        self.stats["input_tokens"] += 10
        self.stats["output_tokens"] += 2
        self.stats["total_tokens"] += 12
        self.stats["model_calls"] += 1

    def get_cost_stats(self) -> dict:
        return dict(self.stats)


def _mock_votes(_orchestrator: DebateOrchestrator) -> list[Vote]:
    return [
        Vote(
            investor="Warren Buffett",
            vote=VoteChoice.BUY,
            confidence=Confidence.HIGH,
            reasoning=["Durable economics"],
        ),
        Vote(
            investor="Benjamin Graham",
            vote=VoteChoice.HOLD,
            confidence=Confidence.MEDIUM,
            reasoning=["Insufficient margin of safety"],
        ),
    ]


def test_full_mocked_debate_persists_canonical_v1_event_log(
    tmp_path, monkeypatch
):
    """M1 DoD: a full offline debate yields a valid, replayable v1 log."""
    usage_counter = _UsageCounter()

    def act_with_usage(self, **kwargs):
        usage_counter.record_turn()
        return _mock_persona_act(self, **kwargs)

    monkeypatch.setattr(clients_module, "client", lambda: usage_counter)
    monkeypatch.setattr(InvestorPersona, "act", act_with_usage)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    with _new_log(tmp_path) as event_log:
        result = debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_mock_data_package(),
            event_log=event_log,
        )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    types = [event.type for event in events]

    assert result.ticker == "AAPL"
    assert types[0] == "debate_started"
    assert types[1] == "data_ready"
    assert types[-1] == "debate_completed"
    assert "debate_error" not in types
    assert "think_delta" not in types
    assert "talk_delta" not in types

    phase_started = [event for event in events if event.type == "phase_started"]
    phase_completed = [event for event in events if event.type == "phase_completed"]
    assert [event.payload["phase"] for event in phase_started] == [
        "opening",
        "cross_exam",
        "rebuttal",
        "verdict",
    ]
    assert [event.payload["phase"] for event in phase_completed] == [
        "opening",
        "cross_exam",
        "rebuttal",
        "verdict",
    ]
    assert all(event.payload["turn_count"] == 2 for event in phase_completed)
    assert phase_started[1].payload["da_persona"] == "Warren Buffett"

    turn_started = [event for event in events if event.type == "turn_started"]
    turn_completed = [event for event in events if event.type == "turn_completed"]
    think_completed = [event for event in events if event.type == "think_completed"]
    talk_completed = [event for event in events if event.type == "talk_completed"]
    cognitive_states = [event for event in events if event.type == "cognitive_state"]

    assert len(turn_started) == len(turn_completed) == 8
    assert len(think_completed) == len(talk_completed) == 8
    assert len(cognitive_states) == 8
    assert len({event.payload["turn_id"] for event in turn_started}) == 8
    assert all(event.payload["full_text"] for event in think_completed)
    assert all(event.payload["full_text"] for event in talk_completed)
    assert all(event.payload["attention"] == "Valuation and downside risk" for event in cognitive_states)
    assert all(event.payload["interrupted"] is False for event in turn_completed)
    usage_events = [event for event in events if event.type == "usage"]
    assert len(usage_events) == 8
    assert all(event.payload["purpose"] == "turn" for event in usage_events)
    assert [event.payload["usage_ref"] for event in turn_completed] == [
        event.seq for event in usage_events
    ]

    assert types.count("vote_recorded") == 2
    assert types.count("scorecard") == 1
    assert [event.seq for event in events] == list(
        range(events[0].seq, events[0].seq + len(events))
    )

    raw_lines = event_log.path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == len(events)
    assert all(json.loads(line)["v"] == 1 for line in raw_lines)


def test_full_mocked_debate_completes_when_client_has_no_usage_interface(
    tmp_path, monkeypatch
):
    """Optional B11 instrumentation cannot break an Ollama-compatible run."""
    no_counter_client = object()

    monkeypatch.setattr(clients_module, "client", lambda: no_counter_client)
    monkeypatch.setattr(InvestorPersona, "act", _mock_persona_act)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    with _new_log(tmp_path) as event_log:
        result = debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_mock_data_package(),
            event_log=event_log,
        )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert result.cost_stats["base_stats"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "cached_calls": 0,
    }
    assert events[-1].type == "debate_completed"


def test_mocked_debate_failure_is_a_sanitized_terminal_event(
    tmp_path, monkeypatch
):
    """Runtime failures terminate the same stream without raw exception data."""
    monkeypatch.setattr(
        DebateOrchestrator,
        "run_debate",
        lambda _self: (_ for _ in ()).throw(
            RuntimeError("Bearer sk-proj-SECRET raw provider response")
        ),
    )

    with _new_log(tmp_path) as event_log:
        with pytest.raises(RuntimeError, match="raw provider response"):
            debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=_mock_data_package(),
                event_log=event_log,
            )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_error"
    assert events[-1].payload == {
        "stage": "debate",
        "message": "RuntimeError while running debate",
        "recoverable": False,
    }
    assert "SECRET" not in event_log.path.read_text(encoding="utf-8")


def test_setup_failure_still_creates_a_replayable_terminal_log(
    tmp_path, monkeypatch
):
    """Once a run is requested, persona setup failures use the same channel."""
    monkeypatch.setattr(
        "tinyic.personas.registry.load_persona",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyError("broken persona with sk-proj-SECRET")
        ),
    )

    with _new_log(tmp_path) as event_log:
        with pytest.raises(KeyError, match="broken persona"):
            debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=_mock_data_package(),
                event_log=event_log,
            )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert [event.type for event in events] == [
        "debate_started",
        "debate_error",
    ]
    assert events[-1].payload == {
        "stage": "setup",
        "message": "KeyError while running setup",
        "recoverable": False,
    }
    assert "SECRET" not in event_log.path.read_text(encoding="utf-8")


def test_data_failure_terminalizes_log_and_cleans_caller_session(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(clients_module, "client", lambda: _UsageCounter())
    monkeypatch.setattr(
        "tinyic.data.pipeline.build_data_package",
        lambda _ticker: (_ for _ in ()).throw(
            RuntimeError("data fetch failed with sk-proj-SECRET")
        ),
    )
    session = Session()

    with _new_log(tmp_path) as event_log:
        with pytest.raises(RuntimeError, match="data fetch failed"):
            debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                session=session,
                event_log=event_log,
            )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert [event.type for event in events] == [
        "debate_started",
        "debate_error",
    ]
    assert events[-1].payload["stage"] == "data"
    assert session.agent_names() == []
    assert not session.closed
    assert "SECRET" not in event_log.path.read_text(encoding="utf-8")
    session.close()


def test_client_resolution_failure_is_a_setup_terminal_event(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        clients_module,
        "client",
        lambda: (_ for _ in ()).throw(RuntimeError("client unavailable")),
    )

    with _new_log(tmp_path) as event_log:
        with pytest.raises(RuntimeError, match="client unavailable"):
            debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=_mock_data_package(),
                event_log=event_log,
            )

    _, _, read_event_log = _events_api()
    events = read_event_log(event_log.path)
    assert [event.type for event in events] == [
        "debate_started",
        "debate_error",
    ]
    assert events[-1].payload["stage"] == "setup"


def test_golden_mocked_debate_log_is_schema_valid_and_replayable():
    """M1 DoD: renderer milestones inherit one deterministic full log."""
    _, _, read_event_log = _events_api()

    events = read_event_log(GOLDEN_PATH)

    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert {event.debate_id for event in events} == {DEBATE_ID}
    assert {event.ts for event in events} == {FIXED_NOW}
    assert sum(event.type == "phase_started" for event in events) == 4
    assert sum(event.type == "turn_started" for event in events) == 8
    assert sum(event.type == "think_completed" for event in events) == 8
    assert sum(event.type == "talk_completed" for event in events) == 8
    assert sum(event.type == "usage" for event in events) == 8
    assert sum(event.type == "vote_recorded" for event in events) == 2
    assert not any(
        event.type in {"think_delta", "talk_delta"} for event in events
    )


def test_mocked_debate_generation_matches_normalized_golden(
    tmp_path, monkeypatch
):
    """The checked-in golden is generated behavior, not a hand-stale sample."""
    usage_counter = _UsageCounter()

    def act_with_usage(self, **kwargs):
        usage_counter.record_turn()
        return _mock_persona_act(self, **kwargs)

    monkeypatch.setattr(clients_module, "client", lambda: usage_counter)
    monkeypatch.setattr(InvestorPersona, "act", act_with_usage)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    with _new_log(tmp_path) as event_log:
        debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            data_package=_mock_data_package(),
            event_log=event_log,
        )

    _, _, read_event_log = _events_api()

    def normalized(path: Path) -> list[dict]:
        documents = [
            event.model_dump(mode="json") for event in read_event_log(path)
        ]
        for document in documents:
            if document["type"] == "debate_completed":
                document["payload"]["result_ref"] = "<RESULT_REF>"
        return documents

    assert normalized(event_log.path) == normalized(GOLDEN_PATH)
