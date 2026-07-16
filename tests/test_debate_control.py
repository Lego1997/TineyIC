"""Focused checks for the web-to-engine run control seam."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import tinyic.debate as debate_module
import tinyic.debate.memo as memo_module
import tinytroupe.clients as clients_module
from tinyic.debate.control import DebateStopRequested, RunControl
from tinyic.debate.extraction import extract_votes
from tinyic.debate.models import DebateResult, Scorecard
from tinyic.debate.moderator import Moderator
from tinyic.events import EventLog, read_event_log


def test_unpaused_control_never_blocks_phase_boundary():
    control = RunControl()
    control.wait_for_phase()
    assert control.snapshot().paused is False


def test_pause_then_next_releases_exactly_one_phase():
    control = RunControl(paused=True)
    reached: list[str] = []

    def worker() -> None:
        control.wait_for_phase()
        reached.append("first")
        control.wait_for_phase()
        reached.append("second")

    thread = threading.Thread(target=worker)
    thread.start()
    assert reached == []
    assert control.next_phase() is True
    thread.join(timeout=0.05)
    assert reached == ["first"]
    assert thread.is_alive()
    assert control.resume() is True
    thread.join(timeout=1)
    assert reached == ["first", "second"]
    assert not thread.is_alive()


def test_stop_wakes_parked_worker_and_raises():
    control = RunControl(paused=True)
    stopped = threading.Event()

    def worker() -> None:
        with pytest.raises(DebateStopRequested):
            control.wait_for_phase()
        stopped.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert control.stop() is True
    assert stopped.wait(1)
    thread.join(timeout=1)
    with pytest.raises(DebateStopRequested):
        control.check_stop()


def test_moderator_accepts_run_control_and_legacy_event():
    moderator = Moderator()
    moderator.gate_phase(RunControl())
    legacy = threading.Event()
    legacy.set()
    moderator.gate_phase(legacy)
    assert not legacy.is_set()


def test_vote_stop_after_first_provider_call_skips_remaining_agents(monkeypatch):
    control = RunControl()
    calls: list[str] = []

    class FakeExtractor:
        def __init__(self, **_kwargs):
            pass

        def extract_results_from_agent(self, agent, **_kwargs):
            calls.append(agent.name)
            control.stop()
            return {"vote": "BUY", "confidence": "HIGH"}

    monkeypatch.setattr(
        "tinyic.debate.extraction.ResultsExtractor", FakeExtractor
    )
    agents = [SimpleNamespace(name=name) for name in ("A", "B", "C")]
    orchestrator = SimpleNamespace(
        agents=agents,
        moderator=SimpleNamespace(recorded_verdicts={}),
        data_package=SimpleNamespace(company_name="Apple", ticker="AAPL"),
    )

    with pytest.raises(DebateStopRequested):
        extract_votes(orchestrator, checkpoint=control.check_stop)
    assert calls == ["A"]


@pytest.mark.parametrize("operation", ["memo", "disagreements"])
def test_synthesis_stop_after_first_window_skips_later_calls(
    monkeypatch, operation
):
    control = RunControl()
    calls = 0

    class FakeClient:
        def send_message(self, _messages, **_kwargs):
            nonlocal calls
            calls += 1
            control.stop()
            return {"content": "{}"}

    monkeypatch.setattr(memo_module, "client", lambda: FakeClient())
    monkeypatch.setattr(memo_module, "TRANSCRIPT_WINDOW_LENGTH", 4)
    result = DebateResult(
        ticker="AAPL",
        company_name="Apple",
        scorecard=Scorecard(ticker="AAPL", company_name="Apple", votes=[]),
        phases_completed=[],
        transcript="three transcript windows",
    )
    data_package = SimpleNamespace(to_context_string=lambda: "financials")

    with pytest.raises(DebateStopRequested):
        if operation == "memo":
            memo_module.generate_memo(
                result,
                data_package,
                checkpoint=control.check_stop,
            )
        else:
            memo_module.extract_disagreements(
                result,
                checkpoint=control.check_stop,
            )
    assert calls == 1


def test_synthesis_stop_still_emits_usage_for_paid_call():
    control = RunControl()

    class FakeAggregator:
        calls = 0

        def get_cost_stats(self):
            return {
                "input_tokens": self.calls * 10,
                "output_tokens": self.calls * 5,
                "total_tokens": self.calls * 15,
                "model_calls": self.calls,
                "cached_calls": 0,
                "cached_tokens": 0,
            }

        def send_message(self, _messages, **_kwargs):
            self.calls += 1
            control.stop()
            return {"content": "{}"}

    class FakeLog:
        def __init__(self):
            self.events = []

        def emit(self, event_type, payload):
            self.events.append((event_type, payload))

    aggregator = FakeAggregator()
    committee = SimpleNamespace(
        aggregator=aggregator,
        aggregator_binding=SimpleNamespace(model_ref="openai/gpt-5.2"),
    )
    result = DebateResult(
        ticker="AAPL",
        company_name="Apple",
        scorecard=Scorecard(ticker="AAPL", company_name="Apple", votes=[]),
        phases_completed=[],
        transcript="one window",
    )
    log = FakeLog()

    with pytest.raises(DebateStopRequested):
        debate_module._emit_synthesis(
            log,
            result=result,
            data_package=SimpleNamespace(to_context_string=lambda: "financials"),
            moderator=SimpleNamespace(recorded_theses={}),
            committee=committee,
            phase_gate=control,
        )
    assert log.events[0][0] == "usage"
    assert log.events[0][1]["purpose"] == "memo"
    assert log.events[0][1]["input_tokens"] == 10


def test_data_stop_preserves_usage_before_terminal_error(tmp_path, monkeypatch):
    control = RunControl()

    class UsageCounter:
        calls = 0

        def get_cost_stats(self):
            return {
                "input_tokens": self.calls * 20,
                "output_tokens": self.calls * 4,
                "total_tokens": self.calls * 24,
                "model_calls": self.calls,
                "cached_calls": 0,
                "cached_tokens": 0,
            }

    counter = UsageCounter()
    monkeypatch.setattr(clients_module, "client", lambda: counter)

    def stopped_data_build(_ticker, *, checkpoint=None):
        counter.calls += 1
        control.stop()
        assert checkpoint is not None
        checkpoint()

    monkeypatch.setattr(
        "tinyic.data.pipeline.build_data_package",
        stopped_data_build,
    )
    log = EventLog("aapl-20260714-dstop", path=tmp_path / "run.jsonl")
    with pytest.raises(DebateStopRequested):
        debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham"],
            event_log=log,
            phase_gate=control,
        )
    log.close()

    events = read_event_log(log.path)
    assert [event.type for event in events] == [
        "debate_started",
        "usage",
        "debate_error",
    ]
    assert events[1].payload["purpose"] == "research"
    assert events[1].payload["input_tokens"] == 20
    assert events[2].payload["stage"] == "stopped"
