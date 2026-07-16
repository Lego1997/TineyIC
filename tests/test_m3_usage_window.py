"""Offline acceptance tests for subscription message-window accounting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tinyic.auth.usage_window import RollingUsageMeter
from tinyic.debate.control import DebateStopRequested, RunControl
from tinyic.events import EventEnvelope, EventLog, read_event_log
from tinyic.models import BindingClient, ModelBinding
from tinyic.models.types import FinalMessage, Usage, UsageWindow
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic import debate as debate_module
from tinyic.debate import get_debate_cost_stats
from tinyic.usage import MODEL_PRICES_USD_PER_MILLION, estimate_model_keyed_cost


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 13, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_rolling_meter_counts_successes_per_profile_and_prunes_after_five_hours():
    clock = _Clock()
    meter = RollingUsageMeter(clock=clock, window=timedelta(hours=5))

    first = meter.record("openai:chatgpt", estimate=100)
    clock.now += timedelta(hours=4, minutes=59)
    second = meter.record("openai:chatgpt", estimate=100)
    other = meter.record("anthropic:max", estimate=40)

    assert first.window_used_msgs == 1
    assert second.window_used_msgs == 2
    assert other.window_used_msgs == 1

    clock.now += timedelta(minutes=2)
    snapshot = meter.snapshot("openai:chatgpt", estimate=100)
    assert snapshot.window_used_msgs == 1
    assert snapshot.auth_profile == "openai:chatgpt"
    assert snapshot.window_estimate_msgs == 100
    assert snapshot.resets_at is not None


def test_usage_metadata_distinguishes_subscription_from_key_lanes():
    key = Usage(10, 2, 0)
    subscription = Usage(
        10,
        2,
        0,
        auth_profile="openai:chatgpt",
        lane="subscription",
    )

    assert key.lane == "api_key"
    assert key.auth_profile is None
    assert subscription.lane == "subscription"
    assert subscription.auth_profile == "openai:chatgpt"


def test_subscription_window_is_a_schema_valid_v1_event(tmp_path):
    path = tmp_path / "window.jsonl"
    log = EventLog("aapl-20260713-auth", path=path)
    snapshot = UsageWindow(
        auth_profile="openai:chatgpt",
        window_used_msgs=3,
        window_estimate_msgs=100,
        resets_at="2026-07-13T05:00:00.000Z",
    )

    envelope = EventEnvelope.model_validate(
        {
            "v": 1,
            "seq": 1,
            "ts": "2026-07-13T00:00:00.000Z",
            "debate_id": "aapl-20260713-auth",
            "type": "usage_window",
            "payload": snapshot.as_payload(),
        }
    )
    assert envelope.type == "usage_window"

    log.emit(
        "debate_started",
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "preset": "default",
            "personas": [],
            "moderator": "rules",
            "aggregator": "openai/gpt-5.6-sol",
            "caps": {},
            "config_hash": "sha256:test",
            "tinyic_version": "test",
        },
    )
    log.emit("usage_window", snapshot.as_payload())
    log.emit(
        "debate_completed",
        {"phases_completed": [], "duration_s": 0, "result_ref": str(path)},
    )
    log.close()

    events = read_event_log(path)
    assert len(events) == 3
    assert events[1].type == "usage_window"
    assert events[1].payload == {
        "auth_profile": "openai:chatgpt",
        "lane": "subscription",
        "window_used_msgs": 3,
        "window_estimate_msgs": 100,
        "resets_at": "2026-07-13T05:00:00.000Z",
    }


def test_binding_client_surfaces_window_and_excludes_subscription_from_billable():
    window = UsageWindow("openai:chatgpt", 1, 100)

    class _SubscriptionTransport:
        def generate(self, _request):
            usage = Usage(
                10,
                2,
                0,
                auth_profile="openai:chatgpt",
                lane="subscription",
            )
            yield usage
            yield FinalMessage("ok", usage=usage)
            yield window

    seen = []
    binding = ModelBinding("openai/gpt-5.6-sol", auth_profile="openai:chatgpt")
    client = BindingClient(
        binding,
        _SubscriptionTransport(),
        on_usage_window=seen.append,
    )
    client.send_message([{"role": "user", "content": "hello"}])

    assert seen == [window]
    assert client.get_cost_stats()["model_calls"] == 1
    assert client.get_billable_cost_stats()["model_calls"] == 0


def test_binding_client_keeps_key_lane_usage_billable():
    class _KeyTransport:
        def generate(self, _request):
            usage = Usage(10, 2, 0, auth_profile="openai:key")
            yield FinalMessage("ok", usage=usage)

    binding = ModelBinding("openai/gpt-5.2", auth_profile="openai:key")
    client = BindingClient(binding, _KeyTransport())
    client.send_message([{"role": "user", "content": "hello"}])

    assert client.get_billable_cost_stats()["model_calls"] == 1


def _started_log(tmp_path):
    log = EventLog("aapl-20260713-meter", path=tmp_path / "meter.jsonl")
    log.emit(
        "debate_started",
        {
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "preset": "default",
            "personas": [],
            "moderator": "rules",
            "aggregator": "openai/gpt-5.6-sol",
            "caps": {},
            "config_hash": "sha256:test",
            "tinyic_version": "test",
        },
    )
    return log


def test_orchestrator_emits_window_from_bound_call_before_turn_terminal(tmp_path):
    log = _started_log(tmp_path)
    orchestrator = object.__new__(DebateOrchestrator)
    orchestrator.event_log = log
    orchestrator._handle_actions = lambda _agent, _actions: None
    usage = Usage(
        10,
        2,
        auth_profile="openai:chatgpt",
        lane="subscription",
    )
    window = UsageWindow("openai:chatgpt", 1, 100)
    client = SimpleNamespace(
        binding=ModelBinding(
            "openai/gpt-5.6-sol", auth_profile="openai:chatgpt"
        ),
        on_usage=None,
        on_usage_window=None,
        on_reasoning=None,
        on_text=None,
        on_talk=None,
        on_think=None,
    )

    class _Agent:
        def act(self, *, return_actions):
            assert return_actions is True
            client.on_usage(usage, client.binding.model_ref)
            client.on_usage_window(window)
            return []

        def pop_latest_actions(self):
            return []

    _committed, _latest, aggregate = orchestrator._run_bound_turn(
        _Agent(), client, "turn-0001"
    )
    window_event = read_event_log(log.path)[1]

    assert window_event.type == "usage_window"
    assert aggregate["lane"] == "subscription"
    assert aggregate["auth_profile"] == "openai:chatgpt"
    assert client.on_usage_window is None
    log.close()


def test_subscription_turn_usage_never_gets_platform_dollar_cost(tmp_path):
    log = _started_log(tmp_path)
    orchestrator = object.__new__(DebateOrchestrator)
    orchestrator.event_log = log
    usage_event = orchestrator._emit_binding_usage(
        SimpleNamespace(name="Warren Buffett"),
        "turn-0001",
        {
            "input_tokens": 1000,
            "output_tokens": 100,
            "cached_tokens": 0,
            "model_ref": "openai/gpt-5.2",
            "lane": "subscription",
            "auth_profile": "openai:chatgpt",
            "calls": 1,
        },
    )

    assert usage_event is not None
    assert "cost_usd" not in read_event_log(log.path)[1].payload
    log.close()


def test_subscription_call_without_token_counts_still_emits_usage(tmp_path):
    log = _started_log(tmp_path)
    orchestrator = object.__new__(DebateOrchestrator)
    orchestrator.event_log = log

    usage_ref = orchestrator._emit_binding_usage(
        SimpleNamespace(name="Warren Buffett"),
        "turn-0001",
        {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "model_ref": "openai/gpt-5.6-sol",
            "lane": "subscription",
            "calls": 1,
        },
    )

    event = read_event_log(log.path)[1]
    assert usage_ref == event.seq
    assert event.type == "usage"
    assert event.payload["input_tokens"] == 0
    assert event.payload["output_tokens"] == 0
    assert "cost_usd" not in event.payload
    log.close()


def test_mixed_lane_turn_prices_only_api_key_usage(tmp_path):
    aggregate = DebateOrchestrator._aggregate_turn_usage(
        [
            Usage(
                1000,
                100,
                auth_profile="openai:chatgpt",
                lane="subscription",
            ),
            Usage(
                2000,
                200,
                auth_profile="openai:overflow",
                lane="api_key",
            ),
        ],
        "openai/gpt-5.2",
    )
    assert aggregate["input_tokens"] == 3000
    assert aggregate["billable_input_tokens"] == 2000
    assert aggregate["billable_output_tokens"] == 200

    log = _started_log(tmp_path)
    orchestrator = object.__new__(DebateOrchestrator)
    orchestrator.event_log = log
    orchestrator._emit_binding_usage(
        SimpleNamespace(name="Warren Buffett"), "turn-0001", aggregate
    )

    event = read_event_log(log.path)[1]
    expected = estimate_model_keyed_cost(
        {
            "openai/gpt-5.2": {
                "input_tokens": 2000,
                "output_tokens": 200,
            }
        },
        MODEL_PRICES_USD_PER_MILLION,
    )
    assert event.payload["cost_usd"] == round(expected, 8)
    assert event.payload["input_tokens"] == 3000
    assert event.payload["output_tokens"] == 300
    log.close()


def test_subscription_aggregate_usage_never_gets_platform_dollar_cost(tmp_path):
    log = _started_log(tmp_path)

    debate_module._emit_aggregate_usage(
        log,
        purpose="extraction",
        usage_delta={
            "input_tokens": 1000,
            "output_tokens": 100,
            "model_calls": 1,
            "cached_calls": 0,
        },
        model_ref="openai/gpt-5.2",
        billable=False,
    )

    event = read_event_log(log.path)[1]
    assert event.type == "usage"
    assert "cost_usd" not in event.payload
    log.close()


def _synthesis_artifacts():
    section = SimpleNamespace(
        content="memo", contributing_personas=[], supporting_data=[]
    )
    memo = SimpleNamespace(
        **{key: section for key in debate_module._MEMO_SECTION_KEYS}
    )
    analysis = SimpleNamespace(collapse_summary=None, disagreements=[])
    return memo, analysis


class _SynthesisUsageTransport:
    def __init__(self, usages):
        self.usages = iter(usages)

    def generate(self, _request):
        usage = next(self.usages)
        yield usage
        yield FinalMessage("{}", usage=usage)
        if usage.lane == "subscription":
            yield UsageWindow(usage.auth_profile or "subscription", 1, 100)


def _run_synthesis_usage_case(tmp_path, monkeypatch, usages):
    binding = ModelBinding(
        "openai/gpt-5.2", auth_profile="openai:chatgpt"
    )
    prior_windows = []
    prior_sink = prior_windows.append
    aggregator = BindingClient(
        binding,
        _SynthesisUsageTransport(usages),
        on_usage_window=prior_sink,
    )
    committee = SimpleNamespace(
        aggregator=aggregator,
        aggregator_binding=binding,
    )
    memo, analysis = _synthesis_artifacts()
    monkeypatch.setattr(
        debate_module,
        "generate_memo",
        lambda *_args, **_kwargs: (
            aggregator.send_message([{"role": "user", "content": "memo"}]),
            memo,
        )[1],
    )
    monkeypatch.setattr(
        debate_module,
        "extract_disagreements",
        lambda *_args, **_kwargs: (
            aggregator.send_message(
                [{"role": "user", "content": "disagreements"}]
            ),
            analysis,
        )[1],
    )
    result = SimpleNamespace(memo=None, disagreement_analysis=None)
    log = _started_log(tmp_path)
    debate_module._emit_synthesis(
        log,
        result=result,
        data_package=SimpleNamespace(),
        moderator=SimpleNamespace(recorded_theses={}),
        committee=committee,
    )
    events = read_event_log(log.path)
    log.close()
    assert aggregator.on_usage_window is prior_sink
    return events, prior_windows


def test_subscription_synthesis_emits_window_without_platform_cost(
    tmp_path, monkeypatch
):
    usages = [
        Usage(
            1000,
            100,
            auth_profile="openai:chatgpt",
            lane="subscription",
        ),
        Usage(
            500,
            50,
            auth_profile="openai:chatgpt",
            lane="subscription",
        ),
    ]
    events, prior_windows = _run_synthesis_usage_case(
        tmp_path, monkeypatch, usages
    )

    usage = next(
        event
        for event in events
        if event.type == "usage" and event.payload["purpose"] == "memo"
    )
    assert usage.payload["input_tokens"] == 1500
    assert usage.payload["output_tokens"] == 150
    assert "cost_usd" not in usage.payload
    assert sum(event.type == "usage_window" for event in events) == 2
    assert len(prior_windows) == 2


def test_mixed_synthesis_prices_only_api_key_calls(tmp_path, monkeypatch):
    usages = [
        Usage(
            1000,
            100,
            auth_profile="openai:chatgpt",
            lane="subscription",
        ),
        Usage(2000, 200, auth_profile="openai:key", lane="api_key"),
    ]
    events, _prior_windows = _run_synthesis_usage_case(
        tmp_path, monkeypatch, usages
    )

    usage = next(
        event
        for event in events
        if event.type == "usage" and event.payload["purpose"] == "memo"
    )
    expected = estimate_model_keyed_cost(
        {
            "openai/gpt-5.2": {
                "input_tokens": 2000,
                "output_tokens": 200,
            }
        },
        MODEL_PRICES_USD_PER_MILLION,
    )
    assert usage.payload["input_tokens"] == 3000
    assert usage.payload["output_tokens"] == 300
    assert usage.payload["cost_usd"] == round(expected, 8)


def test_prestopped_synthesis_preserves_existing_window_sink(tmp_path):
    binding = ModelBinding("openai/gpt-5.2")
    prior_windows = []
    prior_sink = prior_windows.append
    aggregator = BindingClient(
        binding,
        _SynthesisUsageTransport([]),
        on_usage_window=prior_sink,
    )
    control = RunControl()
    control.stop()
    log = _started_log(tmp_path)

    with pytest.raises(DebateStopRequested):
        debate_module._emit_synthesis(
            log,
            result=SimpleNamespace(),
            data_package=SimpleNamespace(),
            moderator=SimpleNamespace(recorded_theses={}),
            committee=SimpleNamespace(
                aggregator=aggregator,
                aggregator_binding=binding,
            ),
            phase_gate=control,
        )

    assert aggregator.on_usage_window is prior_sink
    assert prior_windows == []
    log.close()


def test_subscription_rollup_is_not_priced_from_model_ref_alone():
    result = SimpleNamespace(
        cost_stats={
            "base_stats": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "model_calls": 1,
                "cached_calls": 0,
                "cached_tokens": 0,
                "by_model": {
                    "openai/gpt-5.2": {
                        "input_tokens": 1000,
                        "output_tokens": 100,
                    }
                },
                "billable_by_model": {},
            }
        }
    )

    stats = get_debate_cost_stats(result)
    assert stats["estimated_cost_usd"] == 0.0
