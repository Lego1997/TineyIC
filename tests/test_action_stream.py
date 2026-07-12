"""Hard unit tests for the streaming TALK/THINK action-content extractor.

Finding 3: ``talk_delta`` must carry action *prose*, never the raw JSON action
envelope.  The scanner extracts only ``content`` string increments of TALK/THINK
actions, decodes escapes incrementally, and — crucially — emits **nothing** when
it cannot confidently lock onto such a field (non-JSON, unexpected shape,
malformed JSON), because the ``*_completed`` events already carry the full text.
"""

from __future__ import annotations

import json

import pytest

from tinyic.models.action_stream import StreamingActionScanner


def _run(document: str, *, chunk_size: int | None = None) -> tuple[str, str, list, list]:
    """Feed ``document`` (optionally in fixed-size chunks) and collect output.

    Returns ``(talk_joined, think_joined, talk_deltas, think_deltas)``.
    """
    talk: list[str] = []
    think: list[str] = []
    scanner = StreamingActionScanner(on_talk=talk.append, on_think=think.append)
    if chunk_size is None:
        scanner.feed(document)
    else:
        for i in range(0, len(document), chunk_size):
            scanner.feed(document[i : i + chunk_size])
    scanner.close()
    return "".join(talk), "".join(think), talk, think


def _envelope(action_type: str, content: str) -> str:
    """A realistic single-action act-loop envelope (type before content)."""
    return json.dumps(
        {
            "action": {"type": action_type, "content": content, "target": ""},
            "cognitive_state": {
                "goals": "evaluate",
                "attention": "risk",
                "emotions": "skeptical",
                "context": ["debate"],
            },
        }
    )


# --------------------------------------------------------------------------
# Happy path: single action, every chunking
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [None, 1, 2, 3, 5, 7, 13, 1000])
def test_single_talk_action_extracts_only_content(chunk_size):
    doc = _envelope("TALK", "A measured judgment on AAPL.")
    talk, think, _t, _k = _run(doc, chunk_size=chunk_size)
    assert talk == "A measured judgment on AAPL."
    assert think == ""


@pytest.mark.parametrize("chunk_size", [None, 1, 2, 4, 8, 1000])
def test_single_think_action_routes_to_think(chunk_size):
    doc = _envelope("THINK", "Weighing the numbers.")
    talk, think, _t, _k = _run(doc, chunk_size=chunk_size)
    assert talk == ""
    assert think == "Weighing the numbers."


def test_cognitive_state_and_target_are_never_surfaced():
    # cognitive_state has no "content" key; target is not "content". Only the
    # action's TALK content is emitted.
    doc = _envelope("TALK", "hello")
    talk, think, _t, _k = _run(doc)
    assert talk == "hello"
    assert think == ""


def test_top_level_reasoning_field_is_not_surfaced():
    # CognitiveActionModelWithReasoning has a top-level "reasoning" string; it is
    # NOT an action content field (native reasoning arrives via ReasoningDelta).
    doc = json.dumps(
        {
            "reasoning": "internal chain of thought",
            "action": {"type": "TALK", "content": "spoken", "target": ""},
            "cognitive_state": {"goals": "g"},
        }
    )
    talk, think, _t, _k = _run(doc)
    assert talk == "spoken"
    assert think == ""


# --------------------------------------------------------------------------
# Multiple actions / THINK then TALK in one document
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [None, 1, 2, 3, 6, 11, 1000])
def test_multi_action_think_then_talk_in_one_document(chunk_size):
    doc = json.dumps(
        {
            "actions": [
                {"type": "THINK", "content": "first I ponder", "target": ""},
                {"type": "TALK", "content": "then I speak", "target": ""},
                {"type": "DONE", "content": "", "target": ""},
            ],
            "cognitive_state": {"goals": "g"},
        }
    )
    talk, think, talk_deltas, think_deltas = _run(doc, chunk_size=chunk_size)
    assert think == "first I ponder"
    assert talk == "then I speak"
    # No stray empty deltas.
    assert all(d for d in talk_deltas + think_deltas)


def test_multiple_talk_actions_accumulate_in_order():
    doc = json.dumps(
        {
            "actions": [
                {"type": "TALK", "content": "part one. ", "target": ""},
                {"type": "TALK", "content": "part two.", "target": ""},
            ]
        }
    )
    talk, _think, _t, _k = _run(doc)
    assert talk == "part one. part two."


# --------------------------------------------------------------------------
# Escapes, unicode, whitespace variance — incremental decoding
# --------------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [None, 1, 2, 3, 4, 5])
def test_escapes_decoded_incrementally_across_split_points(chunk_size):
    # Content with escaped quote, backslash, newline, tab, and a unicode escape.
    content = 'she said "hi",\n\tpath C:\\x and é accent'
    doc = _envelope("TALK", content)
    talk, _think, _t, _k = _run(doc, chunk_size=chunk_size)
    assert talk == content


@pytest.mark.parametrize("chunk_size", [1, 2, 3])
def test_split_mid_unicode_escape(chunk_size):
    doc = _envelope("TALK", "café")  # e-acute via é
    talk, _think, _t, _k = _run(doc, chunk_size=chunk_size)
    assert talk == "café"


@pytest.mark.parametrize("chunk_size", [1, 2, 3])
def test_split_mid_key_still_locks_onto_content(chunk_size):
    # "content" and "type" keys are split across chunks (chunk_size=1..3).
    doc = _envelope("TALK", "kept")
    talk, _think, _t, _k = _run(doc, chunk_size=chunk_size)
    assert talk == "kept"


def test_whitespace_and_format_variance_tolerated():
    doc = (
        '  {\n  "action" :   {\n    "type"\t:\t"TALK" ,\n'
        '    "content" :  "spaced out"  ,\n    "target": ""\n  }\n}  '
    )
    talk, _think, _t, _k = _run(doc)
    assert talk == "spaced out"


# --------------------------------------------------------------------------
# Degrade paths: emit NOTHING, never crash
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prose",
    [
        "A measured judgment on AAPL.",
        "Answer: 42",
        "",
        "   ",
        "Here is my analysis: buy the stock.",
    ],
)
def test_non_json_prose_yields_no_deltas(prose):
    talk, think, talk_deltas, think_deltas = _run(prose)
    assert talk == "" and think == ""
    assert talk_deltas == [] and think_deltas == []


@pytest.mark.parametrize(
    "malformed",
    [
        '{"action": {"type": "TALK", "content":',   # truncated before value
        '{"action": {"type": "TALK", "content"',    # truncated mid-structure
        "{bad json here",                            # garbage after brace
        '{"action": : }',                            # missing key
        '{"action": {{{',                            # nonsense nesting
        "[1, 2, 3]",                                 # top-level array (no actions)
        '{"action": 123}',                           # action not an object
        "not json at all",                           # pure prose
        '{"action": {"content": "x", "type": "TALK"}}',  # content BEFORE type
    ],
)
def test_malformed_or_unexpected_shape_yields_no_deltas_and_no_crash(malformed):
    # No exception, and nothing leaked.
    for chunk_size in (None, 1, 3):
        talk, think, _t, _k = _run(malformed, chunk_size=chunk_size)
        assert talk == "" and think == ""


def test_content_before_type_is_not_streamed_but_type_first_is():
    # Guards the documented ordering assumption both ways.
    type_first = '{"action": {"type": "TALK", "content": "shown"}}'
    content_first = '{"action": {"content": "hidden", "type": "TALK"}}'
    assert _run(type_first)[0] == "shown"
    assert _run(content_first)[0] == ""


def test_non_talk_think_actions_are_ignored():
    doc = _envelope("DONE", "should not appear")
    talk, think, _t, _k = _run(doc)
    assert talk == "" and think == ""


def test_empty_content_emits_no_delta():
    doc = _envelope("TALK", "")
    talk, think, talk_deltas, _k = _run(doc)
    assert talk == ""
    assert talk_deltas == []


# --------------------------------------------------------------------------
# Delta granularity: one delta per fed chunk that carries content
# --------------------------------------------------------------------------


def test_content_split_across_two_feeds_emits_two_deltas():
    # The envelope up to mid-content in feed 1, the rest in feed 2.
    talk: list[str] = []
    scanner = StreamingActionScanner(on_talk=talk.append)
    scanner.feed('{"action": {"type": "TALK", "content": "Answer')
    scanner.feed(': 42", "target": ""}}')
    scanner.close()
    assert talk == ["Answer", ": 42"]
    assert "".join(talk) == "Answer: 42"


def test_whole_envelope_in_one_feed_emits_single_delta():
    talk: list[str] = []
    scanner = StreamingActionScanner(on_talk=talk.append)
    scanner.feed(_envelope("TALK", "one shot"))
    scanner.close()
    assert talk == ["one shot"]
