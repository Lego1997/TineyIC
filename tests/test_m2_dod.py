"""M2 Definition-of-Done acceptance suite (PRD §12 M2 row).

This is the milestone gate for the model layer: it proves, entirely offline
(every provider call served by a wire adapter over a **recorded SSE fixture** —
zero network), the five things M2 must deliver.

(a) **Adapter-agnostic engine.** The *same* mocked debate runs end to end
    through three different wire adapters — ``openai-chat``,
    ``anthropic-messages``, ``openai-compatible`` — selected *purely by
    preset/binding config*, and produces an **identical event-log structure**
    modulo the ``model_ref``/usage payloads. Swapping the provider is a config
    change, nothing else.
(b) **Thinking-gate matrix.** Levels × representative catalog models resolve to
    exactly ``included`` / ``remapped`` / ``omitted`` / ``rejected`` per each
    model's profile (FR-1.3), driven by the *real shipped registry*.
(c) **Heterogeneous committee.** Three personas on three different fake
    providers in one debate: every turn's usage event is attributed to the right
    persona **and** its own ``model_ref`` — no cross-contamination.
(d) **Preset loading + override precedence.** Committee-default < role/persona
    override < per-debate ``--model``/``--thinking`` override; config-source
    precedence (explicit path > env > cwd > builtin); ``default_preset``
    selection.
(e) **Golden fixture holds; deltas are binding-routed-only.** The checked-in M1
    golden log still validates and stays delta-free (it is a legacy run), while
    a binding-routed run adds the streaming ``think_delta``/``talk_delta`` events
    over the identical phase/turn scaffold.

The parity fixtures (``dod_openai_chat.sse`` / ``dod_anthropic_messages.sse`` /
``dod_openai_compatible.sse``) are crafted so all three normalize to the *same*
reasoning + visible text — leaving ``model_ref`` and usage tokens as the only
legitimate per-adapter difference — which is exactly what (a) asserts.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tinyic.debate as debate_module
import tinytroupe.clients as clients_module
from tinyic.data.models import DataPackage
from tinyic.debate.models import Confidence, Vote, VoteChoice
from tinyic.debate.orchestrator import DebateOrchestrator
from tinyic.events import EventLog, read_event_log
from tinyic.models import (
    BindingSpec,
    ModelBinding,
    Preset,
    PresetError,
    StaticCredentialProvider,
    UnsupportedThinkingLevelError,
    build_committee,
    load_preset,
    resolve_binding_thinking,
)
from tinyic.models.adapters.anthropic_messages import AnthropicMessagesAdapter
from tinyic.models.adapters.openai_chat import OpenAIChatAdapter
from tinyic.models.adapters.openai_compatible import OpenAICompatibleAdapter
from tinyic.personas.base import InvestorPersona
from tinytroupe.agent import TinyPerson


FIXTURES = Path(__file__).parent / "fixtures" / "wire"
GOLDEN_PATH = Path(__file__).parent / "fixtures" / "m1_mocked_debate.jsonl"
FIXED_NOW = datetime(2026, 7, 13, 1, 2, 3, tzinfo=timezone.utc)

# model_ref -> (adapter class, base URL, recorded fixture). The three fixtures
# emit the SAME normalized reasoning ("Weighing the numbers.") and visible text
# ("A measured judgment on AAPL.") across three different wire encodings, and
# carry DISTINCT usage so per-adapter attribution is observable.
_DOD_FIXTURES: dict[str, tuple[type, str, str]] = {
    "openai/gpt-5.2": (
        OpenAIChatAdapter,
        "https://api.openai.com/v1",
        "dod_openai_chat.sse",
    ),
    "anthropic/claude-opus-4-8": (
        AnthropicMessagesAdapter,
        "https://api.anthropic.com/v1",
        "dod_anthropic_messages.sse",
    ),
    "ollama/qwen3:32b": (
        OpenAICompatibleAdapter,
        "http://localhost:11434/v1",
        "dod_openai_compatible.sse",
    ),
}

# The (input, output, cached) tokens each fixture reports, for attribution asserts.
_DOD_USAGE: dict[str, tuple[int, int, int]] = {
    "openai/gpt-5.2": (17, 9, 0),
    "anthropic/claude-opus-4-8": (21, 11, 4),
    "ollama/qwen3:32b": (15, 7, 3),
}

_REASONING_TEXT = "Weighing the numbers."
_ANSWER_TEXT = "A measured judgment on AAPL."

_PERSONAS_3 = [
    ("warren_buffett", "Warren Buffett"),
    ("benjamin_graham", "Benjamin Graham"),
    ("charlie_munger", "Charlie Munger"),
]
_REGISTRY_3 = [name for name, _display in _PERSONAS_3]
_PERSONAS_2 = _PERSONAS_3[:2]
_REGISTRY_2 = _REGISTRY_3[:2]


def sse_lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().split("\n")


@dataclass
class FakeResponse:
    """A scripted streaming SSE response (mirrors the adapter unit tests)."""

    status_code: int = 200
    lines: list[str] | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        for key, value in self.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def iter_lines(self):
        yield from (self.lines or [])

    def read_text(self) -> str:
        return "\n".join(self.lines or [])

    def close(self) -> None:  # pragma: no cover - trivial
        pass


class RepeatingTransport:
    """A fake ``HttpTransport`` replaying one SSE fixture on every send.

    A debate calls each persona's transport once per phase, so this returns a
    fresh response for an unbounded number of turns.
    """

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.sent: list = []

    def send(self, request):
        self.sent.append(request)
        return FakeResponse(200, lines=list(self._lines))

    def close(self) -> None:  # pragma: no cover - trivial
        pass


def _dod_transport_factory(binding: ModelBinding, credentials):
    """Map a binding's model to its wire adapter over the recorded fixture.

    The adapter is chosen from the binding's ``model_ref`` alone — i.e. purely
    from preset/binding config, which is the point of DoD (a)/(c).
    """
    adapter_cls, base_url, fixture = _DOD_FIXTURES[binding.model_ref]
    return adapter_cls(
        binding,
        credentials,
        base_url=base_url,
        credential_ref=None,
        http=RepeatingTransport(sse_lines(fixture)),
        sleep=lambda _delay: None,
    )


def _mock_data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        description="Consumer technology company.",
        fetched_at=FIXED_NOW,
    )


def _act_via_binding(self, *, return_actions=False, **_kwargs):
    """A mocked act that routes one call through the active binding client."""
    response = clients_module.client().send_message(
        [{"role": "user", "content": f"{self.name}, give your view."}]
    )
    talk = response["content"]
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


class _UsageCounter:
    """A legacy-client stand-in exposing cumulative counters (M1 path)."""

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


def _legacy_act(counter: _UsageCounter):
    """A mocked act for the legacy (non-routed) path: no streaming, counter usage."""

    def act(self, *, return_actions=False, **_kwargs):
        counter.record_turn()
        cognitive_state = {
            "goals": f"Evaluate AAPL as {self.name}",
            "attention": "Valuation and downside risk",
            "emotions": "Skeptical but engaged",
            "context": ["Investment committee debate"],
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
            {"action": action, "cognitive_state": cognitive_state}
            for action in actions
        ]
        return committed if return_actions else self

    return act


def _mock_votes(orchestrator) -> list[Vote]:
    """Deterministic votes keyed off committee order (adapter-independent)."""
    choices = [VoteChoice.BUY, VoteChoice.BUY, VoteChoice.HOLD]
    return [
        Vote(
            investor=agent.name,
            vote=choices[index % len(choices)],
            confidence=Confidence.HIGH,
            reasoning=["deterministic"],
        )
        for index, agent in enumerate(orchestrator.agents)
    ]


def _mock_memo(debate_result, data_package, **_kwargs):
    """Adapter-independent memo (M4 FR-4.5 synthesis runs the aggregator).

    Mirrors the ``extract_votes`` mock: the synthesis stage now routes through
    the aggregator binding, so it is stubbed here to keep the DoD parity/usage
    assertions about the *debate* turns (not the memo) deterministic and offline.
    """
    from tinyic.debate.models import InvestmentMemo, MemoSection

    def _section(title: str) -> MemoSection:
        return MemoSection(
            title=title,
            content=f"{title}: deterministic synthesis.",
            contributing_personas=["Warren Buffett"],
            supporting_data=["deterministic"],
        )

    return InvestmentMemo(
        ticker=debate_result.ticker,
        company_name=debate_result.company_name,
        executive_summary=_section("Executive Summary"),
        investment_thesis=_section("Investment Thesis"),
        key_risks=_section("Key Risks"),
        valuation_discussion=_section("Valuation Discussion"),
        final_verdict=_section("Final Verdict"),
    )


def _mock_disagreements(debate_result, **_kwargs):
    """Adapter-independent, empty disagreement analysis (see ``_mock_memo``)."""
    from tinyic.debate.models import DisagreementAnalysis

    return DisagreementAnalysis(
        ticker=debate_result.ticker,
        company_name=debate_result.company_name,
        disagreements=[],
    )


def _preset_all(model: str, name: str = "dod-parity") -> Preset:
    """One committee-wide model at ``high`` thinking for every role."""
    return Preset(name=name, default=BindingSpec(model=model, thinking="high"))


def _run_logged_debate(committee, registry_names, log_path, debate_id, *, da=None):
    """Run one debate into a fresh log and return ``(result, events)``.

    ``committee=None`` exercises the legacy (non-binding-routed) path. ``da``
    pins the devil's advocate so a test comparing multiple back-to-back debates
    is not perturbed by the moderator's per-install rotation (FR-4.3).
    """
    with EventLog(debate_id, path=log_path, clock=lambda: FIXED_NOW) as log:
        result = debate_module.run_debate(
            "AAPL",
            registry_names,
            data_package=_mock_data_package(),
            event_log=log,
            committee=committee,
            da=da,
        )
    return result, read_event_log(log_path)


@pytest.fixture
def _binding_debate_mocks(monkeypatch):
    """Route every persona turn through its binding client; deterministic votes.

    The vote extraction and the FR-4.5 memo/disagreement synthesis are stubbed
    (all three would otherwise call the aggregator), so what remains under test is
    the adapter-driven *debate*: turn streaming, per-turn usage attribution, and
    identical event structure across wire adapters.
    """
    monkeypatch.setattr(InvestorPersona, "act", _act_via_binding)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(debate_module, "generate_memo", _mock_memo)
    monkeypatch.setattr(debate_module, "extract_disagreements", _mock_disagreements)


# ==========================================================================
# (a) The same mocked debate via THREE adapters -> identical structure
# ==========================================================================


def _turn_usage(events):
    return [e for e in events if e.type == "usage" and e.payload["purpose"] == "turn"]


def _structural_skeleton(events) -> list[tuple]:
    """Reduce a log to (seq, type, payload) with model/usage-variant fields masked.

    Masked out (the only fields allowed to differ between adapters):
      * debate_started: per-persona ``model_ref``/``auth_profile``,
        ``aggregator``, and the derived ``config_hash``;
      * usage: ``model_ref`` + token counts + optional ``cost_usd``;
      * debate_completed: the per-run ``result_ref`` file path.
    Everything else — event order, seq, turn ids, phases, deltas, votes,
    cognitive state, ``usage_ref`` wiring — must be byte-identical.
    """
    skeleton: list[tuple] = []
    for event in events:
        payload = copy.deepcopy(event.payload)
        if event.type == "debate_started":
            payload["config_hash"] = "<HASH>"
            payload["aggregator"] = "<MODEL>"
            for persona in payload["personas"]:
                persona["model_ref"] = "<MODEL>"
                persona["auth_profile"] = "<AUTH>"
        elif event.type == "usage":
            payload["model_ref"] = "<MODEL>"
            for field_name in ("input_tokens", "output_tokens", "cached_tokens"):
                payload[field_name] = 0
            payload.pop("cost_usd", None)
        elif event.type == "debate_completed":
            payload["result_ref"] = "<RESULT_REF>"
        skeleton.append((event.seq, event.type, payload))
    return skeleton


def test_same_mocked_debate_runs_via_three_adapters_with_identical_structure(
    tmp_path, _binding_debate_mocks
):
    runs: dict[str, list] = {}
    cases = [
        ("adoc", "openai/gpt-5.2"),          # openai-chat
        ("adam", "anthropic/claude-opus-4-8"),  # anthropic-messages
        ("adco", "ollama/qwen3:32b"),        # openai-compatible
    ]
    for suffix, model in cases:
        committee = build_committee(
            _preset_all(model),
            _PERSONAS_3,
            credentials=StaticCredentialProvider({}),
            transport_factory=_dod_transport_factory,
        )
        _result, events = _run_logged_debate(
            committee,
            _REGISTRY_3,
            tmp_path / f"{suffix}.jsonl",
            f"aapl-20260713-{suffix}",
            # Pin the DA so the adapter is the only variable across the three
            # runs; otherwise the per-install rotation gives each a different one.
            da="warren_buffett",
        )
        runs[model] = events

    # Each run really drove a *different* wire adapter: distinct model_ref + usage.
    for model, events in runs.items():
        turn_usages = _turn_usage(events)
        assert {u.payload["model_ref"] for u in turn_usages} == {model}
        expected = _DOD_USAGE[model]
        assert all(
            (u.payload["input_tokens"], u.payload["output_tokens"], u.payload["cached_tokens"])
            == expected
            for u in turn_usages
        )

    # The load-bearing DoD assertion: identical event-log structure across all
    # three adapters, once model_ref/usage payloads are masked.
    skeletons = [_structural_skeleton(events) for events in runs.values()]
    assert skeletons[0] == skeletons[1]
    assert skeletons[0] == skeletons[2]

    # ...and each was a real, complete, error-free end-to-end debate.
    for events in runs.values():
        assert events[0].type == "debate_started"
        assert events[-1].type == "debate_completed"
        assert "debate_error" not in {e.type for e in events}
        # 3 personas x 4 phases of streamed turns.
        assert sum(e.type == "turn_started" for e in events) == 12
        assert sum(e.type == "think_delta" for e in events) == 12
        assert sum(e.type == "talk_delta" for e in events) == 12


# ==========================================================================
# (b) Thinking-gate matrix: levels x representative models (FR-1.3)
# ==========================================================================
#
# Cases are validated against the REAL shipped registry (default_registry), so
# this pins the v1 provider catalog's thinking profiles, not a hand-built one.

# (model_ref, level, runtime, effective, params) -> resolved & included as-is.
_THINKING_INCLUDED = [
    ("openai/gpt-5.2", "high", False, "high", {"reasoning_effort": "high"}),
    ("openai/gpt-5.2", "minimal", False, "minimal", {"reasoning_effort": "minimal"}),
    ("openai/gpt-5.6-sol", "high", False, "high", {"reasoning": {"effort": "high"}}),
    ("anthropic/claude-opus-4-8", "high", False, "high", {"budget_tokens": 8192}),
    ("anthropic/claude-opus-4-8", "max", False, "max", {"budget_tokens": 32768}),
    ("google/gemini-2.5-pro", "medium", False, "medium", {"thinkingBudget": 8192}),
    ("deepseek/deepseek-reasoner", "low", False, "low", {"reasoning_effort": "low"}),
    ("ollama/qwen3:32b", "high", False, "high", {"think": True}),
    ("ollama/qwen3:32b", "off", False, "off", {"think": False}),
    # Unknown model -> the provider's default effort profile (custom/local still work).
    ("openai/gpt-9-experimental", "medium", False, "medium", {"reasoning_effort": "medium"}),
]


@pytest.mark.parametrize("model_ref,level,runtime,effective,params", _THINKING_INCLUDED)
def test_thinking_gate_included(model_ref, level, runtime, effective, params):
    resolution = resolve_binding_thinking(
        ModelBinding(model_ref, thinking_level=level), runtime=runtime
    )
    assert not resolution.omitted
    assert not resolution.remapped
    assert resolution.effective is not None
    assert resolution.effective.value == effective
    assert resolution.params == params


# (model_ref, level, effective, params) -> remapped to nearest at runtime.
_THINKING_REMAPPED = [
    ("openai/gpt-5.2", "max", "xhigh", {"reasoning_effort": "xhigh"}),
    ("openai/gpt-5.2", "off", "minimal", {"reasoning_effort": "minimal"}),
    ("openai/gpt-5.6-sol", "max", "xhigh", {"reasoning": {"effort": "xhigh"}}),
    ("anthropic/claude-opus-4-8", "minimal", "low", {"budget_tokens": 2048}),
    ("anthropic/claude-opus-4-8", "off", "low", {"budget_tokens": 2048}),
    ("google/gemini-2.5-pro", "xhigh", "high", {"thinkingBudget": 24576}),
    ("deepseek/deepseek-reasoner", "max", "high", {"reasoning_effort": "high"}),
    ("openai/gpt-9-experimental", "xhigh", "high", {"reasoning_effort": "high"}),
]


@pytest.mark.parametrize("model_ref,level,effective,params", _THINKING_REMAPPED)
def test_thinking_gate_remapped_at_runtime_but_rejected_at_config_time(
    model_ref, level, effective, params
):
    binding = ModelBinding(model_ref, thinking_level=level)
    # Runtime override: remap to the nearest supported level rather than fail.
    resolution = resolve_binding_thinking(binding, runtime=True)
    assert resolution.remapped
    assert not resolution.omitted
    assert resolution.effective.value == effective
    assert resolution.params == params
    # The very same level is a hard rejection at config time (never a silent remap).
    with pytest.raises(UnsupportedThinkingLevelError):
        resolve_binding_thinking(binding, runtime=False)


# Grok reasons by default with no thinking knob -> omit at every level/mode.
_THINKING_OMITTED = [("xai/grok-4", "off"), ("xai/grok-4", "high"), ("xai/grok-4", "max")]


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("model_ref,level", _THINKING_OMITTED)
def test_thinking_gate_omitted_for_models_without_a_knob(model_ref, level, runtime):
    resolution = resolve_binding_thinking(
        ModelBinding(model_ref, thinking_level=level), runtime=runtime
    )
    assert resolution.omitted
    assert resolution.effective is None
    assert resolution.params == {}


# (model_ref, level, expected supported set) -> rejected at config time.
_THINKING_REJECTED = [
    ("openai/gpt-5.2", "off", ("minimal", "low", "medium", "high", "xhigh")),
    ("openai/gpt-5.2", "max", ("minimal", "low", "medium", "high", "xhigh")),
    ("anthropic/claude-opus-4-8", "off", ("low", "medium", "high", "xhigh", "max")),
    ("anthropic/claude-opus-4-8", "minimal", ("low", "medium", "high", "xhigh", "max")),
    ("google/gemini-2.5-pro", "xhigh", ("low", "medium", "high")),
    ("deepseek/deepseek-reasoner", "xhigh", ("low", "medium", "high")),
    ("openai/gpt-9-experimental", "max", ("low", "medium", "high")),
]


@pytest.mark.parametrize("model_ref,level,supported", _THINKING_REJECTED)
def test_thinking_gate_rejected_at_config_time_lists_valid_levels(
    model_ref, level, supported
):
    with pytest.raises(UnsupportedThinkingLevelError) as excinfo:
        resolve_binding_thinking(
            ModelBinding(model_ref, thinking_level=level), runtime=False
        )
    error = excinfo.value
    assert error.reason_code == "unsupported_thinking_level"
    assert error.requested.value == level
    assert tuple(lvl.value for lvl in error.supported) == supported
    # The doctor-facing message names the requested level and the valid set.
    message = str(error)
    assert level in message
    for valid_level in supported:
        assert valid_level in message


# ==========================================================================
# (c) Heterogeneous committee: 3 personas, 3 providers, per-turn attribution
# ==========================================================================


def _heterogeneous_preset() -> Preset:
    return Preset(
        name="dod-heterogeneous",
        default=BindingSpec(model="openai/gpt-5.2", thinking="high"),
        personas={
            "warren_buffett": BindingSpec(model="openai/gpt-5.2"),
            "benjamin_graham": BindingSpec(model="anthropic/claude-opus-4-8"),
            "charlie_munger": BindingSpec(model="ollama/qwen3:32b"),
        },
        aggregator=BindingSpec(model="openai/gpt-5.2"),
    )


def test_heterogeneous_committee_attributes_each_turn_to_its_persona_and_model(
    tmp_path, _binding_debate_mocks
):
    expected_model = {
        "Warren Buffett": "openai/gpt-5.2",
        "Benjamin Graham": "anthropic/claude-opus-4-8",
        "Charlie Munger": "ollama/qwen3:32b",
    }
    committee = build_committee(
        _heterogeneous_preset(),
        _PERSONAS_3,
        credentials=StaticCredentialProvider({}),
        transport_factory=_dod_transport_factory,
    )
    result, events = _run_logged_debate(
        committee, _REGISTRY_3, tmp_path / "hetero.jsonl", "aapl-20260713-hetr"
    )

    turn_usages = _turn_usage(events)
    assert len(turn_usages) == 12  # 3 personas x 4 phases, one usage event each

    # Every turn usage event is attributed to the RIGHT persona and its own model,
    # with that provider's own token counts (no cross-contamination).
    for usage in turn_usages:
        persona = usage.payload["persona"]
        model_ref = usage.payload["model_ref"]
        assert model_ref == expected_model[persona]
        expected_tokens = _DOD_USAGE[model_ref]
        assert (
            usage.payload["input_tokens"],
            usage.payload["output_tokens"],
            usage.payload["cached_tokens"],
        ) == expected_tokens

    assert Counter(u.payload["persona"] for u in turn_usages) == {
        "Warren Buffett": 4,
        "Benjamin Graham": 4,
        "Charlie Munger": 4,
    }

    # Each turn_completed references its OWN turn's usage event seq.
    usage_seq_by_turn = {u.payload["turn_id"]: u.seq for u in turn_usages}
    for turn_completed in [e for e in events if e.type == "turn_completed"]:
        turn_id = turn_completed.payload["turn_id"]
        assert turn_completed.payload["usage_ref"] == usage_seq_by_turn[turn_id]

    # The per-persona heterogeneity is visible in the public debate_started event.
    started = next(e for e in events if e.type == "debate_started")
    assert {p["name"]: p["model_ref"] for p in started.payload["personas"]} == expected_model
    assert started.payload["preset"] == "dod-heterogeneous"

    # The debate cost rollup is model-attributed across all three providers.
    by_model = result.cost_stats["base_stats"]["by_model"]
    assert set(by_model) == set(expected_model.values())
    assert by_model["openai/gpt-5.2"]["input_tokens"] == 4 * 17
    assert by_model["anthropic/claude-opus-4-8"]["input_tokens"] == 4 * 21
    assert by_model["ollama/qwen3:32b"]["input_tokens"] == 4 * 15


# ==========================================================================
# (d) Preset loading + override precedence (FR-1.4 / FR-6.1)
# ==========================================================================


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tinyic.toml"
    path.write_text(body, encoding="utf-8")
    return path


_PRECEDENCE_TOML = """
default_preset = "p"

[presets.p]
model = "openai/gpt-5.2"
thinking = "high"
auth_profile = "openai:work"

[presets.p.params]
temperature = 0.3

[presets.p.aggregator]
model = "anthropic/claude-opus-4-8"

[presets.p.moderator]
thinking = "minimal"

[presets.p.personas.benjamin_graham]
model = "deepseek/deepseek-reasoner"
thinking = "low"
"""


def test_preset_field_precedence_role_and_persona_override_committee_default(tmp_path):
    preset = load_preset(path=_write_config(tmp_path, _PRECEDENCE_TOML))

    # Unlisted persona: inherits EVERY field from the committee default.
    buffett = preset.persona_binding("warren_buffett")
    assert (
        buffett.model_ref,
        buffett.thinking_level.value,
        buffett.auth_profile,
        buffett.params,
    ) == ("openai/gpt-5.2", "high", "openai:work", {"temperature": 0.3})

    # Listed persona: model+thinking win over the default; auth+params inherited.
    graham = preset.persona_binding("benjamin_graham")
    assert (
        graham.model_ref,
        graham.thinking_level.value,
        graham.auth_profile,
        graham.params,
    ) == ("deepseek/deepseek-reasoner", "low", "openai:work", {"temperature": 0.3})

    # Aggregator: model wins; thinking/auth/params inherited.
    aggregator = preset.aggregator_binding()
    assert (
        aggregator.model_ref,
        aggregator.thinking_level.value,
        aggregator.auth_profile,
        aggregator.params,
    ) == ("anthropic/claude-opus-4-8", "high", "openai:work", {"temperature": 0.3})

    # Moderator: thinking wins; model/auth/params inherited. (A supported level
    # for gpt-5.2 -- "off" is now rejected by config-time validation, FR-1.3.)
    moderator = preset.moderator_binding()
    assert (
        moderator.model_ref,
        moderator.thinking_level.value,
        moderator.auth_profile,
        moderator.params,
    ) == ("openai/gpt-5.2", "minimal", "openai:work", {"temperature": 0.3})


def test_per_debate_override_is_highest_precedence(tmp_path):
    base = load_preset(path=_write_config(tmp_path, _PRECEDENCE_TOML))

    # --model/--thinking force one choice across EVERY role, beating default + pins.
    forced = base.with_overrides(model="ollama/qwen3:32b", thinking="minimal")
    for binding in (
        forced.persona_binding("warren_buffett"),  # inherited default
        forced.persona_binding("benjamin_graham"),  # had its own model+thinking pin
        forced.aggregator_binding(),  # had its own model pin
        forced.moderator_binding(),  # had its own thinking pin
    ):
        assert binding.model_ref == "ollama/qwen3:32b"
        assert binding.thinking_level.value == "minimal"

    # Auth profile + params survive the blunt override (only model/thinking forced).
    graham = forced.persona_binding("benjamin_graham")
    assert graham.auth_profile == "openai:work"
    assert graham.params == {"temperature": 0.3}

    # A single-dimension override leaves the other dimension's pins intact.
    thinking_only = base.with_overrides(thinking="off")
    graham_pin = thinking_only.persona_binding("benjamin_graham")
    assert graham_pin.model_ref == "deepseek/deepseek-reasoner"  # model pin preserved
    assert graham_pin.thinking_level.value == "off"  # thinking forced
    assert thinking_only.aggregator_binding().model_ref == "anthropic/claude-opus-4-8"


def test_default_preset_selection_precedence(tmp_path):
    path = _write_config(
        tmp_path,
        """
        default_preset = "beta"

        [presets.alpha]
        model = "openai/gpt-5.2"

        [presets.beta]
        model = "anthropic/claude-opus-4-8"
        """,
    )
    # No name -> the config's default_preset.
    assert load_preset(path=path).name == "beta"
    assert (
        load_preset(path=path).persona_binding("warren_buffett").model_ref
        == "anthropic/claude-opus-4-8"
    )
    # An explicit name beats default_preset.
    assert load_preset("alpha", path=path).name == "alpha"
    assert (
        load_preset("alpha", path=path).persona_binding("warren_buffett").model_ref
        == "openai/gpt-5.2"
    )
    # An unknown name is an actionable error listing the defined presets.
    with pytest.raises(PresetError) as excinfo:
        load_preset("ghost", path=path)
    assert "alpha" in str(excinfo.value) and "beta" in str(excinfo.value)


def test_config_source_precedence_explicit_path_over_env_over_cwd(tmp_path, monkeypatch):
    def config_in(dirname: str, model: str) -> Path:
        directory = tmp_path / dirname
        directory.mkdir()
        path = directory / "tinyic.toml"
        path.write_text(f'[presets.default]\nmodel = "{model}"\n', encoding="utf-8")
        return path

    explicit = config_in("explicit", "openai/gpt-5.2")
    env_config = config_in("env", "anthropic/claude-opus-4-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "tinyic.toml").write_text(
        '[presets.default]\nmodel = "ollama/qwen3:32b"\n', encoding="utf-8"
    )

    monkeypatch.setenv("TINYIC_CONFIG", str(env_config))
    monkeypatch.chdir(cwd)

    # Explicit path arg wins over the env var and cwd.
    assert (
        load_preset(path=explicit).persona_binding("warren_buffett").model_ref
        == "openai/gpt-5.2"
    )
    # The env var wins over cwd.
    assert (
        load_preset().persona_binding("warren_buffett").model_ref
        == "anthropic/claude-opus-4-8"
    )
    # cwd/tinyic.toml is the last file source before the built-in default.
    monkeypatch.delenv("TINYIC_CONFIG", raising=False)
    assert (
        load_preset().persona_binding("warren_buffett").model_ref == "ollama/qwen3:32b"
    )


def test_missing_config_everywhere_falls_back_to_builtin_default(tmp_path, monkeypatch):
    monkeypatch.delenv("TINYIC_CONFIG", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    preset = load_preset()
    assert preset.name == "default"
    # FR-1.4 built-in default: one strong model everywhere.
    assert preset.persona_binding("warren_buffett").model_ref == "openai/gpt-5.2"
    assert preset.aggregator_binding().model_ref == "openai/gpt-5.2"
    assert preset.moderator_binding().model_ref == "openai/gpt-5.2"


# ==========================================================================
# (e) Golden fixture holds; deltas are binding-routed-only
# ==========================================================================


def test_golden_m1_fixture_still_validates_and_is_delta_free():
    events = read_event_log(GOLDEN_PATH)
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"
    # The M1 golden is a legacy (non-binding-routed) run: it carries no streaming
    # deltas, and M2's model layer must not have changed that.
    assert not any(e.type in {"think_delta", "talk_delta"} for e in events)
    # It remains a well-formed, contiguous v1 stream.
    seqs = [e.seq for e in events]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_delta_events_only_appear_for_binding_routed_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(TinyPerson, "communication_display", False)
    monkeypatch.setattr(debate_module, "extract_votes", _mock_votes)
    monkeypatch.setattr(DebateOrchestrator, "get_cost_stats", lambda _self: {})

    # Legacy path: no committee; usage from the process-global client counter.
    counter = _UsageCounter()
    with monkeypatch.context() as patched:
        patched.setattr(clients_module, "client", lambda: counter)
        patched.setattr(InvestorPersona, "act", _legacy_act(counter))
        _legacy_result, legacy = _run_logged_debate(
            None, _REGISTRY_2, tmp_path / "legacy.jsonl", "aapl-20260713-lgcy"
        )

    # Binding-routed path: a committee of fake adapters over the same debate.
    committee = build_committee(
        _preset_all("openai/gpt-5.2"),
        _PERSONAS_2,
        credentials=StaticCredentialProvider({}),
        transport_factory=_dod_transport_factory,
    )
    with monkeypatch.context() as patched:
        patched.setattr(InvestorPersona, "act", _act_via_binding)
        _routed_result, routed = _run_logged_debate(
            committee, _REGISTRY_2, tmp_path / "routed.jsonl", "aapl-20260713-rout"
        )

    # The contract: streaming deltas are EXCLUSIVE to the binding-routed run.
    assert not any(e.type in {"think_delta", "talk_delta"} for e in legacy)
    assert sum(e.type == "think_delta" for e in routed) == 8  # 2 personas x 4 phases
    assert sum(e.type == "talk_delta" for e in routed) == 8

    # ...yet both runs share the identical phase/turn scaffold.
    def turn_ids(events):
        return [e.payload["turn_id"] for e in events if e.type == "turn_started"]

    def phases(events):
        return [e.payload["phase"] for e in events if e.type == "phase_started"]

    assert turn_ids(legacy) == turn_ids(routed)
    assert phases(legacy) == phases(routed) == [
        "opening",
        "cross_exam",
        "rebuttal",
        "verdict",
    ]

    # Both remain complete, replayable logs with one usage event per turn.
    for events in (legacy, routed):
        assert events[0].type == "debate_started"
        assert events[-1].type == "debate_completed"
        assert sum(e.type == "usage" and e.payload["purpose"] == "turn" for e in events) == 8

    # The routed deltas carry the streamed reasoning/text (the visible-thinking feed).
    assert {e.payload["text"] for e in routed if e.type == "think_delta"} == {_REASONING_TEXT}
    assert {e.payload["text"] for e in routed if e.type == "talk_delta"} == {_ANSWER_TEXT}
