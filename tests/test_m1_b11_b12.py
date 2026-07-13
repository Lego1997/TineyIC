"""M1 acceptance tests for the B11/B12 client defect classes.

The two ``rebase_proof`` tests document behavior that upstream TinyTroupe
0.7.0 already fixed.  The remaining tests intentionally describe the M1
contract ahead of its implementation and are expected to stay red until the
corresponding product changes land.

Every test is offline: OpenAI transports, Ollama HTTP, model responses, and
usage counters are replaced with deterministic in-process fakes.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from pydantic import BaseModel
import pytest

import tinyic.debate as debate_module
from tinyic.data.models import DataPackage
from tinyic.debate.models import Confidence, DebateResult, Scorecard, Vote, VoteChoice
from tinyic.personas.base import InvestorPersona
from tinyic.usage import snapshot_cost_counters
import tinytroupe.clients as clients_module
from tinytroupe.agent import TinyPerson
from tinytroupe.clients.ollama_client import OllamaClient
from tinytroupe.clients.openai_client import OpenAIClient


class StructuredAnswer(BaseModel):
    """Small schema used to exercise native and fallback structured output."""

    answer: str


def _openai_client() -> OpenAIClient:
    """Return an isolated client with no cache file or concurrency semaphore."""
    return OpenAIClient(cache_api_calls=False, max_concurrent_model_calls=0)


def _completion(
    *, content: str = "ok", input_tokens: int = 7, output_tokens: int = 3
):
    """Build the minimum non-streaming completion shape used by the client."""
    message = MagicMock()
    message.to_dict.return_value = {"role": "assistant", "content": content}
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def _data_package() -> DataPackage:
    return DataPackage(
        ticker="AAPL",
        company_name="Apple Inc.",
        fetched_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )


def _result_with_cost_stats(cost_stats: dict) -> DebateResult:
    return DebateResult(
        ticker="TEST",
        company_name="Test Company",
        scorecard=Scorecard(
            ticker="TEST", company_name="Test Company", votes=[]
        ),
        phases_completed=[],
        cost_stats=cost_stats,
    )


# ---------------------------------------------------------------------------
# Green proofs: the exact pre-rebase B11/B12 proxy-patch defects disappeared.
# ---------------------------------------------------------------------------


def test_rebase_proof_b11_forced_streaming_is_gone_and_usage_is_counted(
    monkeypatch,
):
    """Upstream 0.7.0 uses a normal completion carrying authoritative usage."""
    client = _openai_client()
    captured_params: list[dict] = []

    monkeypatch.setattr(client, "_setup_from_config", lambda **_kwargs: None)

    def fake_raw_model_call(_model, params):
        captured_params.append(deepcopy(params))
        return _completion()

    monkeypatch.setattr(client, "_raw_model_call", fake_raw_model_call)

    response = client.send_message(
        [{"role": "user", "content": "hello"}],
        model="gpt-test",
        max_completion_tokens=8,
        max_attempts=1,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    assert response == {"role": "assistant", "content": "ok"}
    assert captured_params[0]["stream"] is False
    assert "stream_options" not in captured_params[0]
    assert client.get_cost_stats() == {
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "model_calls": 1,
        "cached_calls": 0,
    }


def test_b11_local_cache_hits_do_not_count_tokens_as_billable_usage():
    """Replaying a local response increments hits without rebilling tokens."""
    client = _openai_client()
    response = _completion(
        input_tokens=1_000_000,
        output_tokens=500_000,
    )

    client._update_cost_stats(response, was_cached=False)
    client._update_cost_stats(response, was_cached=True)

    assert client.get_cost_stats() == {
        "input_tokens": 1_000_000,
        "output_tokens": 500_000,
        "total_tokens": 1_500_000,
        "model_calls": 1,
        "cached_calls": 1,
    }


def test_b11_usage_snapshot_is_optional_for_clients_without_counters():
    """Usage instrumentation cannot make an Ollama-compatible lane unusable."""
    assert snapshot_cost_counters(SimpleNamespace()) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "cached_calls": 0,
    }


def test_rebase_proof_b12_response_format_reaches_native_openai_parse():
    """The 0.7.0 client preserves a Pydantic schema instead of dropping it."""
    client = _openai_client()
    client.client = MagicMock()
    expected_response = object()
    client.client.beta.chat.completions.parse.return_value = expected_response

    actual_response = client._raw_model_call(
        "gpt-test",
        {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "response_format": StructuredAnswer,
        },
    )

    assert actual_response is expected_response
    sent = client.client.beta.chat.completions.parse.call_args.kwargs
    assert sent["response_format"] is StructuredAnswer
    client.client.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# Red acceptance tests: remaining B11/B12 behavior to implement for M1.
# ---------------------------------------------------------------------------


def test_b12_openai_raw_call_does_not_mutate_request_params():
    """Model-specific request adaptation must not corrupt retry/cache inputs."""
    client = _openai_client()
    client.client = MagicMock()
    client.client.beta.chat.completions.parse.return_value = object()
    params = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "response_format": StructuredAnswer,
    }
    original = deepcopy(params)

    client._raw_model_call("gpt-test", params)

    assert params == original


def test_b12_reasoning_model_keeps_limit_and_optional_fields_are_optional():
    """Structured o1/o3 calls reach native parsing with their token limit."""
    client = _openai_client()
    client.client = MagicMock()
    expected_response = object()
    client.client.beta.chat.completions.parse.return_value = expected_response

    actual_response = client._raw_model_call(
        "o3-mini",
        {
            "model": "o3-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
            "max_completion_tokens": 321,
            "response_format": StructuredAnswer,
        },
    )

    assert actual_response is expected_response
    sent = client.client.beta.chat.completions.parse.call_args.kwargs
    assert sent["max_completion_tokens"] == 321
    assert "reasoning_effort" in sent


def test_b12_retry_uses_one_stable_cache_key(monkeypatch):
    """A retry of the same logical request must use the identical cache key."""
    client = _openai_client()
    client.client = MagicMock()
    client.client.beta.chat.completions.parse.side_effect = [
        RuntimeError("transient failure"),
        _completion(content='{"answer": "ok"}'),
    ]
    observed_cache_keys: list[str] = []

    monkeypatch.setattr(client, "_setup_from_config", lambda **_kwargs: None)
    monkeypatch.setattr(
        client,
        "_get_cached_response",
        lambda cache_key: observed_cache_keys.append(cache_key) or None,
    )
    monkeypatch.setattr(
        "tinytroupe.clients.openai_client.time.sleep", lambda _seconds: None
    )

    response = client.send_message(
        [{"role": "user", "content": "hello"}],
        model="gpt-test",
        response_format=StructuredAnswer,
        max_completion_tokens=8,
        max_attempts=2,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    assert response["content"] == '{"answer": "ok"}'
    assert len(observed_cache_keys) >= 2
    assert len(set(observed_cache_keys)) == 1


def test_b11_request_preparation_adds_usage_options_only_when_streaming():
    """The preparation seam must make any future streamed request billable.

    Calling the seam with ``stream=True`` does not enable streaming in the
    legacy ``send_message`` path; the green rebase proof above continues to
    require that public path to prepare ``stream=False``.
    """
    client = _openai_client()
    request = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }
    original = deepcopy(request)

    prepared = client._prepare_chat_api_params(request)

    assert request == original
    assert prepared is not request
    assert prepared["stream"] is True
    assert prepared["stream_options"] == {"include_usage": True}


class _CumulativeCounter:
    """Process-wide counter fake with usage predating the current debate."""

    def __init__(self) -> None:
        self._stats = {
            "input_tokens": 1_000,
            "output_tokens": 500,
            "total_tokens": 1_500,
            "model_calls": 9,
            "cached_calls": 0,
        }

    def record_call(self, *, input_tokens: int = 10, output_tokens: int = 2) -> None:
        self._stats["input_tokens"] += input_tokens
        self._stats["output_tokens"] += output_tokens
        self._stats["total_tokens"] += input_tokens + output_tokens
        self._stats["model_calls"] += 1

    def get_cost_stats(self) -> dict:
        return dict(self._stats)


def test_b11_debate_cost_stats_are_snapshot_delta_not_process_totals(monkeypatch):
    """A debate reports its eight mocked turns, excluding earlier client use."""
    counter = _CumulativeCounter()

    def mock_persona_act(self, *, return_actions=False, **_kwargs):
        counter.record_call()
        action = {"type": "TALK", "content": "mocked view", "target": ""}
        self._actions_buffer.append(action)
        return [action] if return_actions else self

    def mock_votes(_orchestrator):
        return [
            Vote(
                investor="Warren Buffett",
                vote=VoteChoice.BUY,
                confidence=Confidence.HIGH,
            ),
            Vote(
                investor="Benjamin Graham",
                vote=VoteChoice.HOLD,
                confidence=Confidence.MEDIUM,
            ),
        ]

    monkeypatch.setattr(clients_module, "client", lambda: counter)
    monkeypatch.setattr(InvestorPersona, "act", mock_persona_act)
    monkeypatch.setattr(
        InvestorPersona, "consolidate_episode_memories", lambda _self: False
    )
    monkeypatch.setattr(debate_module, "extract_votes", mock_votes)
    monkeypatch.setattr(TinyPerson, "communication_display", False)

    result = debate_module.run_debate(
        "AAPL",
        ["warren_buffett", "benjamin_graham"],
        data_package=_data_package(),
    )

    assert result.cost_stats["base_stats"] == {
        "input_tokens": 80,
        "output_tokens": 16,
        "total_tokens": 96,
        "model_calls": 8,
        "cached_calls": 0,
    }
    assert result.cost_stats["model_ref"] == "openai/gpt-5.6-sol"


def test_b11_pricing_sums_model_keyed_rates(monkeypatch):
    """Each model's tokens must use that model's own input/output rates."""
    monkeypatch.setattr(
        debate_module,
        "MODEL_PRICES_USD_PER_MILLION",
        {
            "openai/test-cheap": {"input": 1.0, "output": 2.0},
            "openai/test-expensive": {"input": 10.0, "output": 20.0},
        },
        raising=False,
    )
    result = _result_with_cost_stats(
        {
            "base_stats": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "model_calls": 2,
                "cached_calls": 0,
                "by_model": {
                    "openai/test-cheap": {
                        "input_tokens": 1_000_000,
                        "output_tokens": 0,
                    },
                    "openai/test-expensive": {
                        "input_tokens": 0,
                        "output_tokens": 1_000_000,
                    },
                },
            }
        }
    )

    formatted = debate_module.get_debate_cost_stats(result)

    assert formatted["estimated_cost_usd"] == pytest.approx(21.0)


def test_b11_normal_legacy_counter_shape_uses_recorded_model_ref(monkeypatch):
    """Production-shaped counters never fall through to default-model pricing."""
    monkeypatch.setattr(
        debate_module,
        "MODEL_PRICES_USD_PER_MILLION",
        {"openai/alternate": {"input": 3.0, "output": 7.0}},
        raising=False,
    )
    result = _result_with_cost_stats(
        {
            "base_stats": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "total_tokens": 2_000_000,
                "model_calls": 1,
                "cached_calls": 0,
            },
            "model_ref": "openai/alternate",
        }
    )

    assert debate_module.get_debate_cost_stats(result)[
        "estimated_cost_usd"
    ] == pytest.approx(10.0)


def test_b11_unknown_model_cost_is_none(monkeypatch):
    """An unknown model must not silently inherit some other model's price."""
    monkeypatch.setattr(
        debate_module, "MODEL_PRICES_USD_PER_MILLION", {}, raising=False
    )
    result = _result_with_cost_stats(
        {
            "base_stats": {
                "input_tokens": 1_000,
                "output_tokens": 1_000,
                "total_tokens": 2_000,
                "model_calls": 1,
                "cached_calls": 0,
                "by_model": {
                    "unknown/provider-model": {
                        "input_tokens": 1_000,
                        "output_tokens": 1_000,
                    }
                },
            }
        }
    )

    formatted = debate_module.get_debate_cost_stats(result)

    assert formatted["estimated_cost_usd"] is None


def test_b12_ollama_preserves_schema_via_native_format_or_prompt_fallback(
    monkeypatch,
):
    """Ollama must receive the schema natively or in an explicit instruction."""
    client = OllamaClient(cache_api_calls=False)
    captured: dict = {}

    def fake_request(_endpoint, method="POST", **kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {"message": {"role": "assistant", "content": '{"answer": "ok"}'}}
            ]
        }

    monkeypatch.setattr(client, "_make_request", fake_request)
    monkeypatch.setattr(
        "tinytroupe.clients.ollama_client.time.sleep", lambda _seconds: None
    )

    client.send_message(
        [{"role": "user", "content": "hello"}],
        model="qwen-test",
        response_format=StructuredAnswer,
        max_attempts=1,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    payload = captured["json"]
    native_schema = json.dumps(payload.get("format"), default=str)
    prompt_fallback = json.dumps(payload["messages"], default=str)
    assert "answer" in native_schema or "answer" in prompt_fallback


def test_b12_ollama_uses_local_default_when_shared_base_url_is_unset():
    client = OllamaClient(cache_api_calls=False)

    assert client.base_url == "http://localhost:11434/v1"


def test_b12_ollama_json_object_descriptor_is_not_called_json_schema(
    monkeypatch,
):
    """OpenAI's generic JSON descriptor becomes a truthful JSON instruction."""
    client = OllamaClient(cache_api_calls=False)
    captured: dict = {}

    def fake_request(_endpoint, method="POST", **kwargs):
        captured.update(kwargs)
        return {
            "choices": [
                {"message": {"role": "assistant", "content": '{"ok": true}'}}
            ]
        }

    monkeypatch.setattr(client, "_make_request", fake_request)
    monkeypatch.setattr(
        "tinytroupe.clients.ollama_client.time.sleep", lambda _seconds: None
    )

    client.send_message(
        [{"role": "user", "content": "hello"}],
        model="qwen-test",
        response_format={"type": "json_object"},
        max_attempts=1,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    instruction = captured["json"]["messages"][0]["content"]
    assert "valid JSON object" in instruction
    assert "JSON Schema" not in instruction


def test_b12_ollama_returns_requested_pydantic_model(monkeypatch):
    """The schema fallback retains the shared typed-return client contract."""
    client = OllamaClient(cache_api_calls=False)
    monkeypatch.setattr(
        client,
        "_make_request",
        lambda *_args, **_kwargs: {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"answer": "typed"}',
                    }
                }
            ]
        },
    )
    monkeypatch.setattr(
        "tinytroupe.clients.ollama_client.time.sleep", lambda _seconds: None
    )

    result = client.send_message(
        [{"role": "user", "content": "hello"}],
        model="qwen-test",
        response_format=StructuredAnswer,
        enable_pydantic_model_return=True,
        max_attempts=1,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    assert isinstance(result, StructuredAnswer)
    assert result.answer == "typed"


def test_b12_ollama_structured_retry_evicts_malformed_cache_entry(
    tmp_path, monkeypatch
):
    """One malformed structured response cannot poison every retry."""
    client = OllamaClient(cache_api_calls=False)
    client.cache_api_calls = True
    client.cache_file_name = str(tmp_path / "ollama-cache.json")
    client.api_cache = {}
    responses = iter(["not-json", '{"answer": "recovered"}'])
    provider_calls = 0

    def fake_request(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": next(responses),
                    }
                }
            ]
        }

    monkeypatch.setattr(client, "_make_request", fake_request)
    monkeypatch.setattr(client, "_save_cache", lambda: None)
    monkeypatch.setattr(
        "tinytroupe.clients.ollama_client.time.sleep", lambda _seconds: None
    )

    result = client.send_message(
        [{"role": "user", "content": "hello"}],
        model="qwen-test",
        response_format=StructuredAnswer,
        enable_pydantic_model_return=True,
        max_attempts=2,
        waiting_time=0,
        exponential_backoff_factor=1,
        timeout=1,
    )

    assert isinstance(result, StructuredAnswer)
    assert result.answer == "recovered"
    assert provider_calls == 2
