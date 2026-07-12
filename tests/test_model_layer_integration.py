"""Offline acceptance tests for M2 stage 3 — engine integration (FR-1.1/4/5).

Everything here is fully offline: every provider call is served by a wire
adapter driven by a **fake HTTP transport replaying recorded SSE fixtures**
(``tests/fixtures/wire/``), exactly as ``tests/test_model_adapters.py`` does —
there is zero network.  Coverage:

* preset loading + resolution (``tinyic.toml`` schema, inheritance, overrides);
* the ``BindingClient`` shim (routing a call through an adapter, usage capture,
  streaming delta sinks, legacy-compatible return shape);
* a full mocked debate routed through a **mixed-provider committee** (three wire
  formats at once — the M2 DoD), asserting per-persona model heterogeneity,
  ``think_delta``/``talk_delta`` streaming, per-call usage attribution, and that
  extraction routes through the aggregator binding;
* memo synthesis routing through the aggregator binding.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import tinyic.debate as debate_module
import tinytroupe.clients as clients_module
from tinyic.data.models import DataPackage
from tinyic.debate.models import DebateResult, Scorecard
from tinyic.models import (
    BindingClient,
    BindingSpec,
    Committee,
    ModelBinding,
    Preset,
    PresetError,
    StaticCredentialProvider,
    activate,
    active_client,
    build_committee,
    load_config,
    load_preset,
    validate_preset_thinking,
)
from tinyic.models.adapters._http import HttpRequest
from tinyic.models.adapters.anthropic_messages import AnthropicMessagesAdapter
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter
from tinyic.models.adapters.openai_responses import OpenAIResponsesAdapter
from tinyic.models.types import ReasoningDelta, TextDelta, Usage
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson
from datetime import datetime, timezone


FIXTURES = Path(__file__).parent / "fixtures" / "wire"
FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)

# A one-line openai-chat SSE stream whose visible content is an extraction
# verdict JSON, so the aggregator's routed extraction calls return real votes.
_EXTRACTION_JSON = (
    '{\\"vote\\":\\"BUY\\",\\"confidence\\":\\"HIGH\\",'
    '\\"reasoning\\":[\\"durable moat\\"],\\"key_risks\\":[\\"valuation\\"],'
    '\\"changed_mind\\":false}'
)
EXTRACTION_SSE = (
    'data: {"choices":[{"index":0,"delta":{"content":"' + _EXTRACTION_JSON + '"},'
    '"finish_reason":"stop"}]}\n'
    '\n'
    'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":30,'
    '"total_tokens":80}}\n'
    '\n'
    'data: [DONE]\n'
).split("\n")


def sse_lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().split("\n")


def _sse(*data_objects) -> list[str]:
    """Build ``data: <json>`` SSE lines (blank-line separated), then ``[DONE]``.

    Using ``json.dumps`` keeps the embedded JSON action envelope correctly
    escaped without hand-written backslashes.
    """
    lines: list[str] = []
    for obj in data_objects:
        lines.append("data: " + json.dumps(obj))
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return lines


# Buffett's turn on the openai-chat fixture: native reasoning streams "Let me" +
# " think." (2 ReasoningDeltas), and the visible completion is the act-loop JSON
# action envelope streamed in two fragments so the scanner emits the TALK content
# "Answer: 42" as two talk_deltas ("Answer" + ": 42") — proving prose extraction,
# not raw-envelope leakage (finding 3).
_BUFFETT_ENVELOPE_SSE = _sse(
    {"choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "Let me"}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"reasoning_content": " think."}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": '{"action": {"type": "TALK", "content": "Answer'}, "finish_reason": None}]},
    {"choices": [{"index": 0, "delta": {"content": ': 42", "target": ""}}'}, "finish_reason": "stop"}]},
    {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}},
)


@dataclass
class FakeResponse:
    """A scripted streaming SSE response (mirrors test_model_adapters)."""

    status_code: int = 200
    lines: list[str] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def iter_lines(self):
        yield from (self.lines or [])

    def read_text(self) -> str:
        return "\n".join(self.lines or [])

    def close(self) -> None:
        self.closed = True


class RepeatingTransport:
    """A fake ``HttpTransport`` that replays one SSE fixture on every send.

    A debate calls each persona's transport once per phase, so — unlike the
    single-shot ScriptedTransport in the adapter unit tests — this returns a
    fresh response for an unbounded number of turns.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.sent: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> FakeResponse:
        self.sent.append(request)
        return FakeResponse(200, lines=list(self._lines))

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _adapter_for(binding: ModelBinding, _creds):
    """Map a binding's model to a wire adapter over its recorded fixture."""
    model_ref = binding.model_ref
    common = dict(credential_ref=None, retry=None, sleep=lambda _d: None)
    if model_ref == "openai/gpt-5.2":
        return OpenAIChatAdapter(
            binding, _creds, base_url="https://api.openai.com/v1",
            http=RepeatingTransport(sse_lines("openai_chat_reasoning_stream.sse")),
            **common,
        )
    if model_ref == "anthropic/claude-opus-4-8":
        return AnthropicMessagesAdapter(
            binding, _creds, base_url="https://api.anthropic.com/v1",
            http=RepeatingTransport(sse_lines("anthropic_messages_stream.sse")),
            **common,
        )
    if model_ref == "openai/gpt-5.6-sol":
        return OpenAIResponsesAdapter(
            binding, _creds, base_url="https://api.openai.com/v1",
            http=RepeatingTransport(sse_lines("openai_responses_stream.sse")),
            **common,
        )
    if model_ref == "deepseek/deepseek-reasoner":
        return OpenAIChatAdapter(
            binding, _creds, base_url="https://api.deepseek.com",
            http=RepeatingTransport(EXTRACTION_SSE),
            **common,
        )
    raise AssertionError(f"unexpected model in test committee: {model_ref}")


def _events_api():
    module = importlib.import_module("tinyic.events")
    return module.EventLog, module.read_event_log


def _mock_data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        description="Consumer technology company.",
        fetched_at=FIXED_NOW,
    )


# ==========================================================================
# Preset loading + resolution (FR-1.4)
# ==========================================================================


def test_builtin_default_preset_is_one_strong_model_everywhere(tmp_path, monkeypatch):
    # With no tinyic.toml discoverable, the loader yields the built-in default.
    monkeypatch.delenv("TINYIC_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    preset = load_preset()
    assert preset.name == "default"
    assert preset.persona_binding("warren_buffett").model_ref == "openai/gpt-5.2"
    assert preset.aggregator_binding().model_ref == "openai/gpt-5.2"
    assert preset.moderator_binding().model_ref == "openai/gpt-5.2"


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tinyic.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_preset_resolution_inheritance_and_overrides(tmp_path):
    path = _write_config(
        tmp_path,
        """
        default_preset = "mixed"

        [presets.mixed]
        model = "openai/gpt-5.2"
        thinking = "high"
        auth_profile = "openai:default"

        [presets.mixed.params]
        temperature = 0.7

        [presets.mixed.aggregator]
        model = "anthropic/claude-opus-4-8"

        [presets.mixed.moderator]
        model = "openai/gpt-5.2"
        thinking = "minimal"

        [presets.mixed.personas.benjamin_graham]
        model = "deepseek/deepseek-reasoner"
        thinking = "low"
        """,
    )
    preset = load_preset(path=path)
    assert preset.name == "mixed"

    # unlisted persona inherits the committee default fully
    buffett = preset.persona_binding("warren_buffett")
    assert buffett.model_ref == "openai/gpt-5.2"
    assert buffett.thinking_level.value == "high"
    assert buffett.auth_profile == "openai:default"
    assert buffett.params == {"temperature": 0.7}

    # listed persona overrides model + thinking, inherits auth + params
    graham = preset.persona_binding("benjamin_graham")
    assert graham.model_ref == "deepseek/deepseek-reasoner"
    assert graham.thinking_level.value == "low"
    assert graham.auth_profile == "openai:default"
    assert graham.params == {"temperature": 0.7}

    # aggregator overrides model, inherits thinking/auth/params
    aggregator = preset.aggregator_binding()
    assert aggregator.model_ref == "anthropic/claude-opus-4-8"
    assert aggregator.thinking_level.value == "high"

    # moderator overrides thinking (a gpt-5.2-supported level; "off" is rejected
    # at config time by FR-1.3 validation)
    assert preset.moderator_binding().thinking_level.value == "minimal"


def test_preset_with_overrides_forces_model_and_thinking_across_roles(tmp_path):
    path = _write_config(
        tmp_path,
        """
        [presets.default]
        model = "openai/gpt-5.2"
        thinking = "high"

        [presets.default.aggregator]
        model = "anthropic/claude-opus-4-8"

        [presets.default.personas.benjamin_graham]
        model = "deepseek/deepseek-reasoner"
        """,
    )
    preset = load_preset(path=path).with_overrides(
        model="ollama/qwen3:32b", thinking="off"
    )
    assert preset.persona_binding("warren_buffett").model_ref == "ollama/qwen3:32b"
    assert preset.persona_binding("benjamin_graham").model_ref == "ollama/qwen3:32b"
    assert preset.aggregator_binding().model_ref == "ollama/qwen3:32b"
    for role in (
        preset.persona_binding("warren_buffett"),
        preset.aggregator_binding(),
        preset.moderator_binding(),
    ):
        assert role.thinking_level.value == "off"


def test_preset_errors_are_actionable(tmp_path):
    bad_thinking = _write_config(
        tmp_path, '[presets.default]\nmodel = "openai/gpt-5.2"\nthinking = "turbo"\n'
    )
    with pytest.raises(PresetError):
        load_preset(path=bad_thinking)

    no_model = _write_config(tmp_path, '[presets.default]\nthinking = "high"\n')
    with pytest.raises(PresetError):
        load_preset(path=no_model).persona_binding("warren_buffett")

    with pytest.raises(PresetError):
        load_preset("ghost", path=_write_config(
            tmp_path, '[presets.default]\nmodel = "openai/gpt-5.2"\n'
        ))


def test_repo_sample_tinyic_toml_default_matches_builtin():
    root = Path(__file__).resolve().parents[1] / "tinyic.toml"
    preset = load_preset(path=root)
    assert preset.name == "default"
    assert preset.persona_binding("warren_buffett").model_ref == "openai/gpt-5.2"
    # the sample also documents a heterogeneous mixed-provider committee
    heterogeneous = load_preset("heterogeneous", path=root)
    assert (
        heterogeneous.persona_binding("benjamin_graham").model_ref
        == "deepseek/deepseek-reasoner"
    )
    assert (
        heterogeneous.aggregator_binding().model_ref
        == "anthropic/claude-opus-4-8"
    )


def test_every_shipped_sample_preset_passes_strict_thinking_validation():
    """Finding 2: all presets in the repo's ``tinyic.toml`` must be valid.

    Guards against shipping a preset that pins a level the model rejects (like
    the pre-fix ``heterogeneous`` moderator ``gpt-5.2`` + ``off``), which
    config-time validation (FR-1.3) now refuses at load.
    """
    root = Path(__file__).resolve().parents[1] / "tinyic.toml"
    presets = load_config(root)["presets"]
    assert set(presets) >= {"default", "heterogeneous"}
    for preset in presets.values():
        # Must not raise: every role/persona level is model-supported.
        validate_preset_thinking(preset)


def test_load_preset_rejects_unsupported_thinking_naming_preset_role_model_level(
    tmp_path,
):
    """Finding 1: a config-time level a model rejects fails fast at load."""
    path = _write_config(
        tmp_path,
        """
        default_preset = "bad"

        [presets.bad]
        model = "openai/gpt-5.2"
        thinking = "high"

        [presets.bad.moderator]
        thinking = "off"
        """,
    )
    with pytest.raises(PresetError) as excinfo:
        load_preset("bad", path=path)
    message = str(excinfo.value)
    # The error names the preset, the offending role, the model, and the level,
    # plus the valid set (so `tinyic doctor` and users can act on it).
    assert "bad" in message
    assert "moderator" in message
    assert "openai/gpt-5.2" in message
    assert "off" in message
    for level in ("minimal", "low", "medium", "high", "xhigh"):
        assert level in message


def test_build_committee_rejects_unsupported_thinking_level():
    """Finding 1: committee build strict-validates a programmatic preset too."""
    preset = Preset(
        name="bad-committee",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
        personas={
            # gpt-5.2 does not support "max" (tops out at xhigh).
            "warren_buffett": BindingSpec(thinking="max"),
        },
    )
    with pytest.raises(PresetError) as excinfo:
        build_committee(
            preset,
            [("warren_buffett", "Warren Buffett")],
            credentials=StaticCredentialProvider({}),
            transport_factory=_adapter_for,
        )
    message = str(excinfo.value)
    assert "warren_buffett" in message and "max" in message


def test_build_committee_skips_validation_for_runtime_override():
    """Finding 1: a per-debate override keeps runtime remap (never config-raise).

    ``validate_thinking=False`` is how the product path lets a ``--thinking``
    override that a model would reject at config time be remapped per call by the
    adapter instead of failing the build.
    """
    preset = Preset(
        name="override",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
    ).with_overrides(thinking="off")  # off is unsupported by gpt-5.2 at config time

    committee = build_committee(
        preset,
        [("warren_buffett", "Warren Buffett"), ("charlie_munger", "Charlie Munger")],
        credentials=StaticCredentialProvider({}),
        transport_factory=_adapter_for,
        validate_thinking=False,
    )
    # Built without raising; the binding still carries the requested off level
    # (the adapter remaps it to the nearest supported level at call time).
    assert committee.persona_bindings["Warren Buffett"].thinking_level.value == "off"


# ==========================================================================
# BindingClient shim (FR-1.1/1.5)
# ==========================================================================


def _binding_client(model_ref, *, stream=True):
    binding = ModelBinding(model_ref)
    adapter = _adapter_for(binding, StaticCredentialProvider({}))
    return BindingClient(binding, adapter, stream=stream)


def test_binding_client_returns_legacy_shape_and_captures_usage():
    client = _binding_client("openai/gpt-5.2")
    response = client.send_message([{"role": "user", "content": "hi"}])
    assert response == {"role": "assistant", "content": "Answer: 42"}
    stats = client.get_cost_stats()
    assert stats["input_tokens"] == 20
    assert stats["output_tokens"] == 8
    assert stats["model_calls"] == 1
    assert stats["by_model"] == {
        "openai/gpt-5.2": {
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
            "model_calls": 1,
            "cached_calls": 0,
        }
    }


def test_binding_client_streams_reasoning_and_text_to_sinks():
    client = _binding_client("openai/gpt-5.2")
    reasoning: list[str] = []
    text: list[str] = []
    usages: list[Usage] = []
    client.on_reasoning = reasoning.append
    client.on_text = text.append
    client.on_usage = lambda usage, model_ref: usages.append((usage, model_ref))

    client.send_message([{"role": "user", "content": "hi"}])

    assert reasoning == ["Let me", " think."]
    assert text == ["Answer", ": 42"]
    assert usages == [(Usage(20, 8, 0), "openai/gpt-5.2")]


def test_binding_client_appends_schema_instruction_for_response_format():
    from pydantic import BaseModel

    class Verdict(BaseModel):
        vote: str

    binding = ModelBinding("openai/gpt-5.2")
    adapter = _adapter_for(binding, StaticCredentialProvider({}))
    client = BindingClient(binding, adapter)
    client.send_message(
        [{"role": "system", "content": "be terse"}], response_format=Verdict
    )
    sent = adapter._http.sent[0]  # type: ignore[attr-defined]
    messages = sent.body["messages"]
    assert messages[0] == {"role": "system", "content": "be terse"}
    assert messages[-1]["role"] == "system"
    assert "vote" in messages[-1]["content"]  # the schema is in the prompt


def test_binding_client_degraded_lane_reports_no_usage():
    # A usage-free stream: instrumentation must not fabricate usage.
    binding = ModelBinding("openai/gpt-5.2")
    adapter = OpenAIChatAdapter(
        binding,
        StaticCredentialProvider({}),
        base_url="https://api.openai.com/v1",
        credential_ref=None,
        http=RepeatingTransport(sse_lines("openai_chat_no_usage.sse")),
        sleep=lambda _d: None,
    )
    client = BindingClient(binding, adapter)
    seen: list = []
    client.on_usage = lambda usage, model_ref: seen.append(usage)
    response = client.send_message([{"role": "user", "content": "hi"}])
    assert response["content"] == "No usage here"
    assert seen == []
    assert client.get_cost_stats()["model_calls"] == 0


# ==========================================================================
# Committee build (FR-1.4)
# ==========================================================================


def _mixed_preset() -> Preset:
    return Preset(
        name="test-mixed",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
        personas={
            "warren_buffett": BindingSpec(model="openai/gpt-5.2"),
            "benjamin_graham": BindingSpec(model="anthropic/claude-opus-4-8"),
            "charlie_munger": BindingSpec(model="openai/gpt-5.6-sol"),
        },
        aggregator=BindingSpec(model="deepseek/deepseek-reasoner"),
    )


def _debate_adapter_for(binding, creds):
    """Transport factory for the routed *debate*.

    Buffett (openai/gpt-5.2) speaks the act-loop JSON action envelope so the
    streaming scanner is exercised end to end; every other role reuses the plain
    fixtures via :func:`_adapter_for` (whose raw-fragment prose still drives the
    ``BindingClient`` sink unit tests unchanged).
    """
    if binding.model_ref == "openai/gpt-5.2":
        return OpenAIChatAdapter(
            binding,
            creds,
            base_url="https://api.openai.com/v1",
            credential_ref=None,
            retry=None,
            sleep=lambda _d: None,
            http=RepeatingTransport(_BUFFETT_ENVELOPE_SSE),
        )
    return _adapter_for(binding, creds)


def _mixed_committee() -> Committee:
    personas = [
        ("warren_buffett", "Warren Buffett"),
        ("benjamin_graham", "Benjamin Graham"),
        ("charlie_munger", "Charlie Munger"),
    ]
    return build_committee(
        _mixed_preset(),
        personas,
        credentials=StaticCredentialProvider({}),
        transport_factory=_debate_adapter_for,
    )


def test_build_committee_keys_clients_by_display_name_and_resolves_roles():
    committee = _mixed_committee()
    assert set(committee.persona_clients) == {
        "Warren Buffett",
        "Benjamin Graham",
        "Charlie Munger",
    }
    assert committee.preset_name == "test-mixed"
    assert (
        committee.persona_bindings["Benjamin Graham"].model_ref
        == "anthropic/claude-opus-4-8"
    )
    assert committee.aggregator_binding.model_ref == "deepseek/deepseek-reasoner"
    assert committee.client_for("Warren Buffett") is committee.persona_clients[
        "Warren Buffett"
    ]


# ==========================================================================
# Full mocked debate over a mixed-provider committee (M2 DoD)
# ==========================================================================


def _act_via_binding(self, *, return_actions=False, **_kwargs):
    """A mocked act that routes one call through the active binding client.

    Like the real act loop, the visible completion is a JSON action envelope, so
    the spoken text is the action's ``content`` (not the raw envelope). Falls
    back to the raw content for any non-envelope fixture.
    """
    response = clients_module.client().send_message(
        [{"role": "user", "content": f"{self.name}, give your view."}]
    )
    raw = response["content"]
    try:
        talk = json.loads(raw)["action"]["content"]
    except (ValueError, KeyError, TypeError):
        talk = raw
    cognitive_state = {
        "goals": f"Evaluate AAPL as {self.name}",
        "attention": "Valuation and downside risk",
        "emotions": "Skeptical but engaged",
        "context": ["Investment committee debate"],
    }
    actions = [
        {"type": "THINK", "content": f"{self.name} weighs the evidence.", "target": ""},
        {"type": "TALK", "content": talk, "target": ""},
        {"type": "DONE", "content": "", "target": ""},
    ]
    self._actions_buffer.extend(actions)
    committed = [
        {"action": action, "cognitive_state": cognitive_state} for action in actions
    ]
    return committed if return_actions else self


@pytest.fixture
def _mocked_committee_debate(monkeypatch):
    monkeypatch.setattr(InvestorPersona, "act", _act_via_binding)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)


def _run_mixed_debate(tmp_path):
    EventLog, read_event_log = _events_api()
    committee = _mixed_committee()
    log_path = tmp_path / "mixed.jsonl"
    with EventLog("aapl-20260713-mix1", path=log_path, clock=lambda: FIXED_NOW) as log:
        result = debate_module.run_debate(
            "AAPL",
            ["warren_buffett", "benjamin_graham", "charlie_munger"],
            data_package=_mock_data_package(),
            event_log=log,
            committee=committee,
        )
    return result, read_event_log(log_path), committee


def test_mixed_committee_debate_records_per_persona_heterogeneity(
    tmp_path, _mocked_committee_debate
):
    _result, events, _committee = _run_mixed_debate(tmp_path)
    started = next(e for e in events if e.type == "debate_started")
    by_name = {p["name"]: p for p in started.payload["personas"]}
    assert by_name["Warren Buffett"]["model_ref"] == "openai/gpt-5.2"
    assert by_name["Benjamin Graham"]["model_ref"] == "anthropic/claude-opus-4-8"
    assert by_name["Charlie Munger"]["model_ref"] == "openai/gpt-5.6-sol"
    assert started.payload["preset"] == "test-mixed"
    assert started.payload["aggregator"] == "deepseek/deepseek-reasoner"


def test_mixed_committee_debate_streams_deltas_between_turn_boundaries(
    tmp_path, _mocked_committee_debate
):
    _result, events, _committee = _run_mixed_debate(tmp_path)
    types = [e.type for e in events]
    assert "think_delta" in types
    assert "talk_delta" in types

    # The opening turn is Warren Buffett on the openai-chat reasoning fixture.
    first_turn = next(e for e in events if e.type == "turn_started")
    turn_id = first_turn.payload["turn_id"]
    turn_events = [
        e
        for e in events
        if e.payload.get("turn_id") == turn_id
        and e.type
        in {
            "turn_started",
            "think_delta",
            "talk_delta",
            "think_completed",
            "talk_completed",
            "cognitive_state",
            "usage",
            "turn_completed",
        }
    ]
    assert [e.type for e in turn_events] == [
        "turn_started",
        "think_delta",
        "think_delta",
        "talk_delta",
        "talk_delta",
        "think_completed",
        "talk_completed",
        "cognitive_state",
        "usage",
        "turn_completed",
    ]
    think_deltas = [e.payload["text"] for e in turn_events if e.type == "think_delta"]
    talk_deltas = [e.payload["text"] for e in turn_events if e.type == "talk_delta"]
    assert "".join(think_deltas) == "Let me think."
    assert "".join(talk_deltas) == "Answer: 42"
    # completed text is authoritative and unchanged by the delta stream
    talk_completed = next(e for e in turn_events if e.type == "talk_completed")
    assert talk_completed.payload["full_text"] == "Answer: 42"


def test_mixed_committee_debate_attributes_usage_per_persona_binding(
    tmp_path, _mocked_committee_debate
):
    _result, events, _committee = _run_mixed_debate(tmp_path)
    turn_usages = [e for e in events if e.type == "usage" and e.payload["purpose"] == "turn"]
    # 3 personas x 4 phases = 12 turn usage events, each natively attributed.
    assert len(turn_usages) == 12
    by_model: dict[str, list] = {}
    for event in turn_usages:
        by_model.setdefault(event.payload["model_ref"], []).append(event.payload)
    assert set(by_model) == {
        "openai/gpt-5.2",
        "anthropic/claude-opus-4-8",
        "openai/gpt-5.6-sol",
    }
    # Buffett (openai-chat fixture): input 20 / output 8 every turn.
    assert all(p["input_tokens"] == 20 and p["output_tokens"] == 8 for p in by_model["openai/gpt-5.2"])
    # Graham (anthropic fixture): input 25 / output 14 / cached 5.
    assert all(
        p["input_tokens"] == 25 and p["output_tokens"] == 14 and p["cached_tokens"] == 5
        for p in by_model["anthropic/claude-opus-4-8"]
    )
    # Munger (responses fixture): input 30 / output 12 / cached 6.
    assert all(
        p["input_tokens"] == 30 and p["output_tokens"] == 12 and p["cached_tokens"] == 6
        for p in by_model["openai/gpt-5.6-sol"]
    )
    # Each turn_completed references its usage event's seq.
    turn_completed = [e for e in events if e.type == "turn_completed"]
    usage_seqs = {e.seq for e in turn_usages}
    assert {e.payload["usage_ref"] for e in turn_completed} == usage_seqs


def test_mixed_committee_debate_routes_extraction_through_aggregator(
    tmp_path, _mocked_committee_debate
):
    result, events, committee = _run_mixed_debate(tmp_path)
    extraction_usage = [
        e for e in events if e.type == "usage" and e.payload["purpose"] == "extraction"
    ]
    assert len(extraction_usage) == 1
    # Three agents extracted through the aggregator binding: 3 x (50 in / 30 out).
    payload = extraction_usage[0].payload
    assert payload["model_ref"] == "deepseek/deepseek-reasoner"
    assert payload["input_tokens"] == 150
    assert payload["output_tokens"] == 90

    # The aggregator returned real BUY verdicts, so the scorecard is a consensus.
    scorecard = next(e for e in events if e.type == "scorecard")
    assert scorecard.payload["consensus"] == "BUY"
    assert scorecard.payload["bull_count"] == 3

    # The debate rollup is model-attributed across all four providers used.
    by_model = result.cost_stats["base_stats"]["by_model"]
    assert set(by_model) == {
        "openai/gpt-5.2",
        "anthropic/claude-opus-4-8",
        "openai/gpt-5.6-sol",
        "deepseek/deepseek-reasoner",
    }
    assert by_model["deepseek/deepseek-reasoner"]["input_tokens"] == 150


def test_mixed_committee_debate_completes_cleanly(tmp_path, _mocked_committee_debate):
    result, events, _committee = _run_mixed_debate(tmp_path)
    assert isinstance(result, DebateResult)
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    assert "debate_error" not in {e.type for e in events}


# ==========================================================================
# Memo synthesis routes through the aggregator binding (FR-1.1/4.5)
# ==========================================================================


def test_generate_memo_routes_through_active_aggregator_binding():
    """A memo call inside an activated aggregator scope uses that binding."""
    from tinyic.debate.memo import generate_memo

    # An aggregator whose adapter returns a memo-shaped JSON with usage.
    memo_json = (
        '{\\"executive_summary\\":{\\"content\\":\\"c\\",'
        '\\"contributing_personas\\":[\\"Warren Buffett\\"],'
        '\\"supporting_data\\":[\\"pe\\"]},'
        '\\"investment_thesis\\":{\\"content\\":\\"c\\"},'
        '\\"key_risks\\":{\\"content\\":\\"c\\"},'
        '\\"valuation_discussion\\":{\\"content\\":\\"c\\"},'
        '\\"final_verdict\\":{\\"content\\":\\"c\\"}}'
    )
    memo_sse = (
        'data: {"choices":[{"index":0,"delta":{"content":"' + memo_json + '"},'
        '"finish_reason":"stop"}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":25,'
        '"total_tokens":65}}\n\n'
        'data: [DONE]\n'
    ).split("\n")
    binding = ModelBinding("anthropic/claude-opus-4-8")
    adapter = OpenAIChatAdapter(
        binding,
        StaticCredentialProvider({}),
        base_url="https://api.anthropic.com/v1",
        credential_ref=None,
        http=RepeatingTransport(memo_sse),
        sleep=lambda _d: None,
    )
    aggregator = BindingClient(binding, adapter)

    result = DebateResult(
        ticker="AAPL",
        company_name="Apple Inc.",
        scorecard=Scorecard(ticker="AAPL", company_name="Apple Inc.", votes=[]),
        phases_completed=["opening"],
        transcript="Warren Buffett: buy.",
    )

    assert active_client() is None
    with activate(aggregator):
        memo = generate_memo(result, _mock_data_package())

    # The memo synthesis ran through the aggregator binding, not the legacy
    # client (which has no offline transport), and captured its usage.
    assert memo.executive_summary.content == "c"
    assert aggregator.get_cost_stats()["model_calls"] >= 1
    assert aggregator.get_cost_stats()["input_tokens"] >= 40
