"""Regression coverage for total result assembly over malformed JSONL."""

from __future__ import annotations

import json

import pytest

from tinyic.cli import main
from tinyic.result import assemble_result, list_runs
from tinyic.tui.events import Event


def _event(seq: int, event_type: str, payload: object) -> Event:
    return Event(
        v=1,
        seq=seq,
        ts="2026-07-15T00:00:00.000Z",
        debate_id="malformed-20260715-test",
        type=event_type,
        payload=payload,  # type: ignore[arg-type]
    )


def _started_payload() -> dict:
    return {
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "preset": "default",
        "personas": [],
        "moderator": "rules",
        "aggregator": "openai/gpt-5.2",
        "caps": {},
        "config_hash": "sha256:test",
        "tinyic_version": "0.1.0",
    }


def _completed_payload() -> dict:
    return {
        "phases_completed": ["opening", "cross_exam", "rebuttal", "verdict"],
        "duration_s": 1.0,
        "result_ref": "result.json",
    }


def test_assemble_result_rejects_malformed_known_shapes_without_coercion():
    events = [
        _event(1, "debate_started", {**_started_payload(), "personas": 7}),
        _event(
            2,
            "usage",
            {
                "purpose": "turn",
                "model_ref": "openai/gpt-5.2",
                "input_tokens": "100",
                "output_tokens": "50",
                "cached_tokens": "0",
                "cost_usd": "0.01",
            },
        ),
        _event(
            3,
            "scorecard",
            {
                "votes": 9,
                "bull_count": 1,
                "bear_count": 0,
                "hold_count": 0,
            },
        ),
        _event(
            4,
            "debate_completed",
            {**_completed_payload(), "phases_completed": 4},
        ),
    ]

    document = assemble_result(events)

    assert document["debate_id"] == "malformed-20260715-test"
    assert document["status"] == "error"
    assert document["phases_completed"] == []
    assert document["scorecard"] is None
    assert document["usage"]["total"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": None,
    }
    assert document["error"] == {
        "stage": "result",
        "message": "event log contains malformed schema-v1 events",
        "recoverable": False,
    }


def test_result_cli_emits_one_incomplete_document_and_exit_3_for_malformed_jsonl(
    tmp_path, capsys
):
    events = [
        _event(1, "debate_started", _started_payload()),
        _event(
            2,
            "memo_section",
            {
                "section": "executive_summary",
                "content": "summary",
                "contributing_personas": 42,
                "supporting_data": [],
            },
        ),
        _event(
            3,
            "debate_completed",
            {**_completed_payload(), "phases_completed": False},
        ),
    ]
    path = tmp_path / "malformed.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "v": event.v,
                    "seq": event.seq,
                    "ts": event.ts,
                    "debate_id": event.debate_id,
                    "type": event.type,
                    "payload": event.payload,
                }
            )
            + "\n"
            for event in events
        ),
        encoding="utf-8",
    )

    assert main(["result", str(path), "--json"]) == 3
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1
    document = json.loads(lines[0])
    assert document["status"] == "incomplete"
    assert document["ticker"] == "AAPL"
    assert document["memo"] == {}
    assert document["phases_completed"] == []
    assert document["error"]["stage"] == "result"
    assert captured.err == ""


def test_result_rejects_valid_envelopes_with_broken_stream_invariants(
    tmp_path, capsys
):
    events = [
        _event(1, "debate_started", _started_payload()),
        _event(3, "debate_completed", _completed_payload()),
    ]
    path = tmp_path / "sequence-gap.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "v": event.v,
                    "seq": event.seq,
                    "ts": event.ts,
                    "debate_id": event.debate_id,
                    "type": event.type,
                    "payload": event.payload,
                }
            )
            + "\n"
            for event in events
        ),
        encoding="utf-8",
    )

    assert main(["result", str(path), "--json"]) == 3
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "incomplete"
    assert document["error"]["stage"] == "result"
    assert list_runs(tmp_path)[0]["status"] == "incomplete"


@pytest.mark.parametrize("corruption", ["reordered", "junk", "torn_tail"])
def test_result_cli_never_certifies_corrupt_file_order_or_tail(
    tmp_path, capsys, corruption
):
    started = _event(1, "debate_started", _started_payload())
    completed = _event(2, "debate_completed", _completed_payload())

    def line(event):
        return json.dumps(
            {
                "v": event.v,
                "seq": event.seq,
                "ts": event.ts,
                "debate_id": event.debate_id,
                "type": event.type,
                "payload": event.payload,
            }
        ) + "\n"

    if corruption == "reordered":
        content = line(completed) + line(started)
    elif corruption == "junk":
        content = line(started) + line(completed) + "not-json\n"
    else:
        content = line(started) + line(completed) + '{"v":1,"seq":3'
    path = tmp_path / f"{corruption}.jsonl"
    path.write_text(content, encoding="utf-8")

    assert main(["result", str(path), "--json"]) == 3
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "incomplete"
    assert document["error"]["stage"] == "result"
