"""Offline CLI contracts for persona research, listing, and inspection."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyic.cli import build_parser, main
from tinyic.models import StaticCredentialProvider
from tinyic.models.presets import BindingSpec, Preset
from tinyic.persona_cli import (
    list_personas_command,
    research_persona,
    show_persona_command,
)
from tinyic.personas.factory import (
    CallUsage,
    DossierSynthesisResponse,
    Evidence,
    PersonaSynthesisResponse,
    SearchResponse,
    VerificationResponse,
)


class FakeBackend:
    provider = "openai"

    def __init__(self, model_ref: str = "openai/gpt-5.6-sol") -> None:
        self.model_ref = model_ref
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RecordingFactory:
    request = None
    backend = None
    progress_messages: list[str] = []

    def __init__(self, backend, *, progress) -> None:
        type(self).backend = backend
        self.progress = progress

    def run(self, request):
        type(self).request = request
        self.progress("planning")
        type(self).progress_messages.append("planning")
        output_dir = Path(request.output_dir)
        return SimpleNamespace(
            investor_name=request.investor_name,
            slug=request.slug,
            agent_path=output_dir / f"{request.slug}.agent.json",
            dossier_path=output_dir / f"{request.slug}.dossier.md",
            source_count=8,
            domain_count=6,
            quality="normal",
            usage=SimpleNamespace(calls=11, cost_usd=0.1234),
        )


class TTYInput(io.StringIO):
    def isatty(self) -> bool:
        return True


class BudgetedKimiBackend:
    """Deterministic Kimi-shaped backend for the public CLI/factory boundary."""

    provider = "kimi"
    model_ref = "kimi/kimi-k2.6"

    def __init__(self, max_rounds: int) -> None:
        self.max_rounds = max_rounds
        self.rounds_used = 0
        self.search_requests = 0
        self.closed = False

    def search(self, request):
        remaining = self.max_rounds - self.rounds_used
        if remaining <= 0:
            return SearchResponse((), budget_exhausted=True)
        # A normal Kimi search takes a tool-call round plus the echoed final
        # answer.  The final bounded response tells the factory not to launch
        # another logical query after the fourth HTTP round.
        rounds = min(2, remaining)
        self.rounds_used += rounds
        self.search_requests += 1
        # The first completed logical query yields enough independent evidence
        # for the ordinary quality gate.  The second consumes the last two paid
        # echo rounds without reaching final prose, matching Kimi's empty
        # budget-exhausted response while retaining that partial call usage.
        evidence = (
            tuple(
                Evidence(
                    f"https://source-{index}.example/research",
                    f"Source {index}",
                    (
                        "Buy only with a margin of safety and demand public evidence."
                        if index == 1
                        else f"Public investment-method evidence {index}."
                    ),
                    query=request.query.text,
                    provider=self.provider,
                    source_type="primary" if index == 1 else "secondary",
                )
                for index in range(1, 5)
            )
            if self.search_requests == 1
            else ()
        )
        return SearchResponse(
            evidence,
            CallUsage(
                "search",
                self.model_ref,
                input_tokens=20 * rounds,
                output_tokens=5 * rounds,
                cost_usd=0.005 * rounds,
                calls=rounds,
            ),
            budget_exhausted=self.rounds_used == self.max_rounds,
        )

    def synthesize_dossier(self, request):
        return DossierSynthesisResponse(
            f"The cited public record supports this {request.section_title.casefold()} summary. [1]",
            CallUsage("synthesis", self.model_ref, 20, 5, cost_usd=0.001),
        )

    def synthesize_persona(self, request):
        epithet = "Evidence-led public-market investor"
        specification = {
            "type": "TinyPerson",
            "persona": {
                "name": request.investor_name,
                "occupation": {"description": epithet},
                "style": "Measured and explicit about uncertainty.",
                "personality": {"traits": ["Patient"]},
                "beliefs": ["I require evidence before conviction."],
                "skills": ["Public-record investment analysis"],
                "preferences": {
                    "interests": ["Business quality"],
                    "likes": ["Cited evidence"],
                    "dislikes": ["Unsupported certainty"],
                },
                "behaviors": {
                    "general": ["Tests downside before discussing upside."]
                },
                "other_facts": [],
            },
            "tinyic": {
                "schema_version": 1,
                "epithet": epithet,
                "philosophy_hook": "Demand evidence and protect the downside.",
                "temperament": "balanced",
                "decision_checklist": [
                    "Understand the business.",
                    "Estimate downside value.",
                    "Demand a margin of safety.",
                ],
                "signal_rules": [],
                "red_flags": [],
                "famous_quotes": [],
                "sources": [dict(item) for item in request.source_records],
                "generation": dict(request.generation),
            },
        }
        return PersonaSynthesisResponse(
            specification,
            CallUsage("synthesis", self.model_ref, 30, 10, cost_usd=0.002),
        )

    def verify(self, request):
        return VerificationResponse(
            {
                claim.claim_id: claim.citations or (1,)
                for claim in request.claims
            },
            CallUsage("verification", self.model_ref, 30, 5, cost_usd=0.001),
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_recording_factory():
    RecordingFactory.request = None
    RecordingFactory.backend = None
    RecordingFactory.progress_messages = []


def _user_agent(path: Path, *, name: str = "Jane Doe") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "TinyPerson",
                "persona": {"name": name},
                "tinyic": {
                    "epithet": "Evidence-led allocator",
                    "temperament": "balanced",
                    "philosophy_hook": "Protect capital before seeking upside.",
                    "sources": [
                        {"url": "https://one.example/source"},
                        {"url": "https://two.example/source"},
                    ],
                    "generation": {
                        "model_ref": "google/gemini-3.5-flash",
                        "date": "2026-07-14",
                        "quality": "thin",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_persona_parser_wires_all_subcommands_and_provider_aware_default():
    parser = build_parser()
    research = parser.parse_args(
        [
            "persona",
            "research",
            "Ada Value",
            "--model",
            "google/gemini-3.5-flash",
            "--slug",
            "ada_sim",
            "--max-searches",
            "4",
            "--yes",
            "--force",
        ]
    )
    assert research.command == "persona"
    assert research.persona_command == "research"
    assert research.name == "Ada Value"
    assert research.model == "google/gemini-3.5-flash"
    assert research.slug == "ada_sim"
    assert research.max_searches == 4
    assert research.yes and research.force
    assert parser.parse_args(["persona", "research", "Ada Value"]).max_searches is None
    assert parser.parse_args(["persona", "list", "--json"]).json_mode is True
    assert parser.parse_args(["persona", "show", "warren_buffett"]).slug == "warren_buffett"


@pytest.mark.parametrize("value", ["0", "17", "many"])
def test_persona_parser_rejects_invalid_search_budget(value: str):
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(
            ["persona", "research", "Ada Value", "--max-searches", value]
        )
    assert caught.value.code == 2


def test_persona_help_does_not_import_command_service(capsys):
    sys.modules.pop("tinyic.persona_cli", None)
    with pytest.raises(SystemExit) as caught:
        build_parser().parse_args(["persona", "--help"])
    assert caught.value.code == 0
    assert "research" in capsys.readouterr().out
    assert "tinyic.persona_cli" not in sys.modules


def test_research_dispatch_forwards_every_cli_option(monkeypatch):
    import tinyic.persona_cli as service

    called = {}

    def fake_research(name, **kwargs):
        called.update(name=name, **kwargs)
        return 17

    monkeypatch.setattr(service, "research_persona", fake_research)
    assert (
        main(
            [
                "persona",
                "research",
                "Ada Value",
                "--model",
                "openai/gpt-5.6-luna",
                "--slug",
                "ada_sim",
                "--max-searches",
                "5",
                "--yes",
                "--force",
            ]
        )
        == 17
    )
    assert called == {
        "name": "Ada Value",
        "model": "openai/gpt-5.6-luna",
        "slug": "ada_sim",
        "max_searches": 5,
        "yes": True,
        "force": True,
    }


def test_persona_list_json_is_stable_sorted_and_origin_tagged(tmp_path, monkeypatch):
    user_dir = tmp_path / "personas"
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(user_dir))
    _user_agent(user_dir / "jane_doe.agent.json")

    out = io.StringIO()
    assert list_personas_command(json_mode=True, out=out, err=io.StringIO()) == 0
    document = json.loads(out.getvalue())

    assert set(document) == {"schema_version", "personas"}
    assert document["schema_version"] == 1
    slugs = [item["slug"] for item in document["personas"]]
    assert slugs == sorted(slugs)
    assert set(document["personas"][0]) == {"slug", "name", "epithet", "origin"}
    records = {item["slug"]: item for item in document["personas"]}
    assert records["warren_buffett"]["origin"] == "built_in"
    assert records["jane_doe"] == {
        "slug": "jane_doe",
        "name": "Jane Doe",
        "epithet": "Evidence-led allocator",
        "origin": "user",
    }


def test_persona_show_json_reads_agent_summary_and_dossier_path(tmp_path, monkeypatch):
    user_dir = tmp_path / "personas"
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(user_dir))
    agent = user_dir / "jane_doe.agent.json"
    dossier = user_dir / "jane_doe.dossier.md"
    _user_agent(agent)
    dossier.write_text("# Jane Doe", encoding="utf-8")

    out = io.StringIO()
    assert show_persona_command("jane_doe", json_mode=True, out=out) == 0
    document = json.loads(out.getvalue())
    assert set(document) == {"schema_version", "persona"}
    assert document["schema_version"] == 1
    assert document["persona"] == {
        "slug": "jane_doe",
        "name": "Jane Doe",
        "epithet": "Evidence-led allocator",
        "temperament": "balanced",
        "philosophy_hook": "Protect capital before seeking upside.",
        "origin": "user",
        "source_count": 2,
        "quality": "thin",
        "model_ref": "google/gemini-3.5-flash",
        "generated_date": "2026-07-14",
        "agent_path": str(agent),
        "dossier_path": str(dossier),
    }


def test_persona_show_unknown_is_concise_exit_three():
    err = io.StringIO()
    assert show_persona_command("not_a_persona", err=err) == 3
    assert "unknown_persona" in err.getvalue()
    assert "Traceback" not in err.getvalue()


def test_persona_show_registry_failure_is_concise_exit_three():
    class UnreadableRegistry:
        BUILTIN_PERSONAS = {}

        @staticmethod
        def registry_snapshot():
            raise OSError("persona directory is unreadable")

    out = io.StringIO()
    err = io.StringIO()
    assert (
        show_persona_command(
            "ada_value",
            out=out,
            err=err,
            registry_module=UnreadableRegistry,
        )
        == 3
    )
    assert out.getvalue() == ""
    assert "persona_read_error" in err.getvalue()
    assert "Traceback" not in err.getvalue()


def test_research_refuses_builtin_collision_before_credentials_or_backend():
    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider setup must not happen")

    err = io.StringIO()
    assert (
        research_persona(
            "Howard Marks",
            slug="howard_marks",
            yes=True,
            err=err,
            credentials_factory=forbidden,
            backend_maker=forbidden,
        )
        == 3
    )
    assert "builtin_persona_collision" in err.getvalue()


def test_research_refuses_existing_user_artifact_before_provider(tmp_path, monkeypatch):
    user_dir = tmp_path / "personas"
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(user_dir))
    _user_agent(user_dir / "ada_sim.agent.json", name="Ada Value")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("provider setup must not happen")

    err = io.StringIO()
    assert (
        research_persona(
            "Ada Value",
            slug="ada_sim",
            yes=True,
            err=err,
            credentials_factory=forbidden,
            backend_selector=forbidden,
        )
        == 3
    )
    assert "persona_exists" in err.getvalue()


def test_research_explicit_model_cost_gate_progress_and_success_summary():
    seen = {}

    def make_backend(binding, credentials, **kwargs):
        seen.update(binding=binding, credentials=credentials, kwargs=kwargs)
        return FakeBackend(binding.model_ref)

    out = io.StringIO()
    err = io.StringIO()
    credential = object()
    assert (
        research_persona(
            "Ada Value",
            model="google/gemini-3.5-flash",
            slug="ada_sim",
            max_searches=4,
            yes=True,
            out=out,
            err=err,
            credentials=credential,
            backend_maker=make_backend,
            factory_class=RecordingFactory,
        )
        == 0
    )

    assert seen["binding"].model_ref == "google/gemini-3.5-flash"
    assert seen["credentials"] is credential
    assert seen["kwargs"] == {"max_searches": 4}
    assert RecordingFactory.request.max_searches == 4
    assert RecordingFactory.backend.closed is True
    assert "Estimated cost:" in err.getvalue()
    assert "tinyic persona: planning" in err.getvalue()
    assert "Created persona 'ada_sim'" in out.getvalue()
    assert "actual cost $0.1234" in out.getvalue()


def test_research_cost_gate_prices_full_declared_search_cap():
    def make_backend(binding, _credentials, **_kwargs):
        return FakeBackend(binding.model_ref)

    err = io.StringIO()
    assert (
        research_persona(
            "Full Cap Estimate",
            model="openai/gpt-5.6-sol",
            slug="full_cap_estimate",
            max_searches=12,
            yes=True,
            force=True,
            out=io.StringIO(),
            err=err,
            credentials=object(),
            backend_maker=make_backend,
            factory_class=RecordingFactory,
        )
        == 0
    )

    assert "12 searches + 7 synthesis/verification calls" in err.getvalue()
    assert RecordingFactory.request.max_searches == 12


def test_kimi_four_round_cap_completes_cli_factory_with_gathered_evidence(
    tmp_path, monkeypatch
):
    user_dir = tmp_path / "personas"
    monkeypatch.setenv("TINYIC_PERSONAS_DIR", str(user_dir))
    seen = {}

    def make_backend(_binding, _credentials, **kwargs):
        backend = BudgetedKimiBackend(kwargs["max_searches"])
        seen["backend"] = backend
        return backend

    out = io.StringIO()
    err = io.StringIO()
    assert (
        research_persona(
            "Ada Value",
            model="kimi/kimi-k2.6",
            slug="ada_kimi_budget",
            max_searches=4,
            yes=True,
            out=out,
            err=err,
            credentials=object(),
            backend_maker=make_backend,
        )
        == 0
    )

    backend = seen["backend"]
    assert backend.rounds_used == 4
    assert backend.rounds_used <= backend.max_rounds
    assert backend.search_requests == 2
    assert backend.closed is True
    agent_path = user_dir / "ada_kimi_budget.agent.json"
    dossier_path = user_dir / "ada_kimi_budget.dossier.md"
    assert agent_path.is_file()
    assert dossier_path.is_file()
    document = json.loads(agent_path.read_text(encoding="utf-8"))
    assert document["tinyic"]["generation"]["search_calls"] == 4
    assert "Search calls: 4." in dossier_path.read_text(encoding="utf-8")
    assert "Created persona 'ada_kimi_budget'" in out.getvalue()
    assert "4 searches" in out.getvalue()
    assert "actual cost unavailable" not in out.getvalue()
    assert "TransientError" not in err.getvalue()


def test_auto_selection_includes_recommended_fallback_for_every_provider():
    preset = Preset(
        "openai_only",
        default=BindingSpec(model="openai/gpt-5.6-terra", thinking="medium"),
    )
    seen = {}

    def select_backend(bindings, credentials, **kwargs):
        seen["bindings"] = bindings
        seen["kwargs"] = kwargs
        backend = FakeBackend("kimi/kimi-k2.6")
        backend.provider = "kimi"
        return backend

    assert (
        research_persona(
            "Ada Value",
            slug="ada_sim",
            yes=True,
            credentials=object(),
            preset_loader=lambda: preset,
            backend_selector=select_backend,
            factory_class=RecordingFactory,
            out=io.StringIO(),
            err=io.StringIO(),
        )
        == 0
    )
    by_provider = {binding.provider: binding.model_ref for binding in seen["bindings"]}
    assert by_provider == {
        "openai": "openai/gpt-5.6-terra",
        "grok": "grok/grok-4.5",
        "google": "google/gemini-3.5-flash",
        "kimi": "kimi/kimi-k2.6",
    }
    assert seen["kwargs"] == {}
    # Kimi gets its provider-aware default; other lanes default to twelve.
    assert RecordingFactory.request.max_searches == 16


def test_auto_selection_uses_available_fallback_in_fixed_provider_priority():
    preset = Preset(
        "openai_only",
        default=BindingSpec(model="openai/gpt-5.6-terra", thinking="medium"),
    )
    credentials = StaticCredentialProvider(
        {
            "GEMINI_API_KEY": "offline-test-key",
            "MOONSHOT_API_KEY": "offline-test-key",
        }
    )

    assert (
        research_persona(
            "Ada Value",
            slug="ada_sim",
            yes=True,
            credentials=credentials,
            preset_loader=lambda: preset,
            factory_class=RecordingFactory,
            out=io.StringIO(),
            err=io.StringIO(),
        )
        == 0
    )
    assert RecordingFactory.backend.provider == "google"
    assert RecordingFactory.backend.model_ref == "google/gemini-3.5-flash"
    assert RecordingFactory.request.max_searches == 12


def test_no_search_lane_returns_onboarding_guidance_and_exit_three():
    class NoLane(RuntimeError):
        reason_code = "no_search_capable_lane"

    def unavailable(*_args, **_kwargs):
        raise NoLane("do not expose credential detail")

    err = io.StringIO()
    assert (
        research_persona(
            "Ada Value",
            slug="ada_sim",
            yes=True,
            credentials=object(),
            backend_selector=unavailable,
            err=err,
        )
        == 3
    )
    diagnostic = err.getvalue()
    assert "no_search_capable_lane" in diagnostic
    assert "tinyic onboard" in diagnostic
    assert "openai, grok, google, or kimi" in diagnostic
    assert "Traceback" not in diagnostic


def test_noninteractive_research_never_prompts_and_requires_yes():
    backend = FakeBackend()
    err = io.StringIO()
    assert (
        research_persona(
            "Ada Value",
            slug="ada_sim",
            stdin=io.StringIO("yes\n"),
            err=err,
            credentials=object(),
            backend_maker=lambda *_args, **_kwargs: backend,
            model="openai/gpt-5.6-sol",
            factory_class=RecordingFactory,
        )
        == 3
    )
    assert "confirmation_required" in err.getvalue()
    assert "Continue with persona research?" not in err.getvalue()
    assert RecordingFactory.request is None
    assert backend.closed is True


def test_interactive_confirmation_can_continue_or_cancel():
    accepted = FakeBackend()
    assert (
        research_persona(
            "Ada Value",
            slug="ada_yes",
            stdin=TTYInput("yes\n"),
            out=io.StringIO(),
            err=io.StringIO(),
            credentials=object(),
            backend_maker=lambda *_args, **_kwargs: accepted,
            model="openai/gpt-5.6-sol",
            factory_class=RecordingFactory,
        )
        == 0
    )
    assert accepted.closed is True

    RecordingFactory.request = None
    declined = FakeBackend()
    err = io.StringIO()
    assert (
        research_persona(
            "Ada Value",
            slug="ada_no",
            stdin=TTYInput("no\n"),
            out=io.StringIO(),
            err=err,
            credentials=object(),
            backend_maker=lambda *_args, **_kwargs: declined,
            model="openai/gpt-5.6-sol",
            factory_class=RecordingFactory,
        )
        == 3
    )
    assert "research_cancelled" in err.getvalue()
    assert RecordingFactory.request is None
    assert declined.closed is True
