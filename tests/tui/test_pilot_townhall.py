"""Textual ``App.run_test`` pilots for the Town Hall TUI (M5 stage 4).

These drive the *real* app over the committed synthetic debate log
(``tests/fixtures/synthetic_debate.jsonl``) — the exact bytes a recorded replay
would consume — and assert what a human actually sees on screen: speech in the
transcript, hidden-then-revealed thinking, phase banners in protocol order,
steering that flips queued → delivered, the composer mode chip, the six-member
committee, and graceful rendering of a truncated log with an incomplete
indicator. The renderer is exercised purely through parsed events, never engine
internals (PRD §3 / FR-5.1), and no test touches the network or an LLM.

The final test re-affirms the stage-1 schema-conformance guarantee over the same
fixture, so the pilots and the frozen contract cannot drift apart.
"""

from __future__ import annotations

from textual.widgets import Collapsible

from tinyic.tui.app import TownHallApp
from tinyic.tui.events import (
    Event,
    invalid_enum_fields,
    is_replayable,
    missing_required_fields,
    read_events,
)
from tinyic.tui.state import SteeringState
from tinyic.tui.widgets import (
    PersonaCard,
    PhaseBanner,
    StatusHeader,
    SteeringNote,
    TurnCard,
)

from tests.support import m4_debate as M4
from tests.support import synthetic_events as G

PHASES_IN_ORDER = ["opening", "cross_exam", "rebuttal", "verdict"]
TOTAL_TURNS = 22  # opening 6 + cross_exam 4 + rebuttal 6 + verdict 6
M4_COMMITTEE = set(M4.PERSONA_DISPLAY)


# --------------------------------------------------------------------------- #
# Helpers — all derived from the same recorded log the app replays.
# --------------------------------------------------------------------------- #

def _completed_texts(event_type: str, field: str) -> dict[str, str]:
    """Map ``turn_id -> payload[field]`` for every event of ``event_type``."""
    return {
        e.payload["turn_id"]: e.payload[field]
        for e in read_events(G.FIXTURE_PATH)
        if e.type == event_type
    }


def _speech_text(card: TurnCard) -> str:
    """The plain speech text a turn card is displaying.

    Completed turns now hold a rendered *markdown* form (Stage-1 polish), so the
    card's content is flattened through a wide Rich console — asserting what a
    human actually reads, whether the renderable is plain Text or Markdown.
    """
    from rich.console import Console

    console = Console(width=4000, legacy_windows=False)
    with console.capture() as capture:
        console.print(card._speech.content, end="")
    return capture.get()


def _think_text(card: TurnCard) -> str:
    """The plain thinking text held in a turn card's (collapsible) row."""
    return str(card._think_body.render())


def _steering_item(app: TownHallApp, msg_id: str) -> SteeringState | None:
    """The folded steering view-model for ``msg_id`` (or ``None`` if not yet seen)."""
    for item in app.state.transcript:
        if isinstance(item, SteeringState) and item.msg_id == msg_id:
            return item
    return None


async def _drain_until(app: TownHallApp, pilot, predicate, *, limit: int = 2000) -> None:
    """Fold recorded events one at a time (syncing the panes) until ``predicate``.

    This is the timer-less version of the replay pump: it exercises the exact
    ``dispatch`` → ``_sync`` path a live feed would, one event per step, so tests
    can observe intermediate render states (e.g. a steer that is queued before it
    is delivered) deterministically.
    """
    for _ in range(limit):
        if predicate():
            return
        if not app._pending:
            break
        app._apply_batch(1)
        app._sync()
        await pilot.pause()
    assert predicate(), "predicate was never satisfied before events ran out"


# --------------------------------------------------------------------------- #
# (a) Every talk_completed's text appears in the transcript.
# --------------------------------------------------------------------------- #

async def test_a_every_talk_completed_text_appears_in_transcript():
    talk = _completed_texts("talk_completed", "full_text")
    assert len(talk) == TOTAL_TURNS  # sanity: fixture shape

    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        cards = list(app.query(TurnCard))
        assert len(cards) == TOTAL_TURNS
        transcript = "\n".join(_speech_text(c) for c in cards)
        for turn_id, text in talk.items():
            assert text in transcript, f"talk for {turn_id} missing from transcript"


# --------------------------------------------------------------------------- #
# (b) Thinking is hidden by default; `t` / `T` reveal the think text.
# --------------------------------------------------------------------------- #

async def test_b_thinking_hidden_by_default_and_revealed_by_t_and_T():
    think = _completed_texts("think_completed", "full_text")

    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        collapsibles = list(app.query(Collapsible))
        assert len(collapsibles) == TOTAL_TURNS
        # Hidden by default: every thinking row starts collapsed.
        assert all(c.collapsed for c in collapsibles)

        # `T` reveals all thinking rows...
        await pilot.press("T")
        await pilot.pause()
        assert all(not c.collapsed for c in collapsibles)
        # ...and the revealed bodies carry the persona's actual think text.
        for card in app.query(TurnCard):
            expected = think.get(card.turn.turn_id, "")
            assert expected and expected in _think_text(card)

        # `T` again collapses everything back to hidden.
        await pilot.press("T")
        await pilot.pause()
        assert all(c.collapsed for c in collapsibles)

        # `t` reveals only the selected (latest) turn's thinking row.
        await pilot.press("t")
        await pilot.pause()
        revealed = [c for c in collapsibles if not c.collapsed]
        assert len(revealed) == 1


# --------------------------------------------------------------------------- #
# (c) Phase banners appear in protocol order.
# --------------------------------------------------------------------------- #

async def test_c_phase_banners_render_in_protocol_order():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        banners = list(app.query(PhaseBanner))
        assert [b.phase_state.phase for b in banners] == PHASES_IN_ORDER

        # The visible labels are in the same order.
        labels = [str(b.render()) for b in banners]
        assert "OPENING" in labels[0]
        assert "CROSS-EXAMINATION" in labels[1]
        assert "REBUTTAL" in labels[2]
        assert "VERDICT" in labels[3]
        # cross_exam surfaces its devil's advocate.
        assert "Howard Marks" in labels[1]


# --------------------------------------------------------------------------- #
# (d) A steering message shows queued, then delivered.
# --------------------------------------------------------------------------- #

async def test_d_steering_message_shows_queued_then_delivered():
    app = TownHallApp(events=read_events(G.FIXTURE_PATH), auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Drain until the mid-cross-exam steer (m01) has been submitted.
        await _drain_until(app, pilot, lambda: _steering_item(app, "m01") is not None)
        submitted = _steering_item(app, "m01")
        assert submitted.mode == "steer"
        assert submitted.status == "queued"  # not yet delivered
        note = app._item_widgets["steer-m01"]
        assert "queued" in str(note.render()).lower()
        assert "delivered" not in str(note.render()).lower()

        # Fold on until the engine delivers it at the next speaker boundary.
        await _drain_until(
            app, pilot, lambda: _steering_item(app, "m01").status == "delivered"
        )
        delivered = _steering_item(app, "m01")
        assert delivered.status == "delivered"
        assert delivered.delivered_before_turn_id  # points at the upcoming turn
        assert "delivered" in str(app._item_widgets["steer-m01"].render()).lower()


# --------------------------------------------------------------------------- #
# (e) The composer Tab toggles the Steer / Queue chip.
# --------------------------------------------------------------------------- #

async def test_e_tab_toggles_steer_queue_chip():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        await pilot.pause()

        def chip() -> str:
            return str(app.query_one("#mode-chip").render())

        assert app.steer_mode == "steer"
        assert "STEER" in chip() and "QUEUE" not in chip()

        await pilot.press("tab")
        await pilot.pause()
        assert app.steer_mode == "queue"
        assert "QUEUE" in chip() and "STEER" not in chip()
        assert app.focused is None  # tab toggled the mode, did not grab the input

        await pilot.press("tab")
        await pilot.pause()
        assert app.steer_mode == "steer"
        assert "STEER" in chip() and "QUEUE" not in chip()


# --------------------------------------------------------------------------- #
# (f) The committee panel shows six personas with model chips.
# --------------------------------------------------------------------------- #

async def test_f_committee_shows_six_personas_with_model_chips():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        cards = list(app.query(PersonaCard))
        assert len(cards) == 6

        expected = {p["name"]: p["model_ref"] for p in G.PERSONAS}
        by_name = {c.persona.name: c for c in cards}
        assert set(by_name) == set(expected)

        for name, model_ref in expected.items():
            rendered = str(by_name[name].render())
            assert name in rendered
            assert model_ref in rendered, f"model chip {model_ref!r} missing for {name}"


# --------------------------------------------------------------------------- #
# (g) A truncated log (cut mid-phase) renders without crashing and flags itself.
# --------------------------------------------------------------------------- #

def _truncated_events() -> list[Event]:
    """Cut the synthetic log mid-``cross_exam`` (and mid-turn), as a crash would.

    Keeps the prefix through the first ``think_completed`` inside cross_exam —
    so there is an opened-but-uncompleted turn and no ``phase_completed`` /
    ``debate_completed`` — then appends a corrupt partial final JSON line to
    mimic a half-written record. The tolerant reader must drop the junk line and
    keep the well-formed prefix.
    """
    dicts = G.build_events()
    cut = None
    in_cross_exam = False
    for i, ev in enumerate(dicts):
        if ev["type"] == "phase_started" and ev["payload"].get("phase") == "cross_exam":
            in_cross_exam = True
        if in_cross_exam and ev["type"] == "think_completed":
            cut = i + 1  # keep through this think; drop the rest of the turn
            break
    assert cut is not None, "could not locate a mid-cross_exam cut point"

    jsonl = G.to_jsonl(dicts[:cut]) + '{"v":1,"seq":9999,"type":"talk_de'
    return read_events(jsonl.splitlines())


async def test_g_truncated_log_renders_and_shows_incomplete_indicator():
    events = _truncated_events()
    assert not is_replayable(events)  # genuinely truncated (no terminal event)

    app = TownHallApp(events=events, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()  # must not raise on a partial log
        await pilot.pause()

        # It rendered what it had: at least the opening phase, its turns, and the
        # partial cross_exam turn — without crashing.
        assert len(list(app.query(TurnCard))) > 0
        assert len(list(app.query(PhaseBanner))) >= 2
        assert app.state.finished is False

        # And the header surfaces an explicit incomplete indicator.
        header_text = str(app.query_one(StatusHeader).render())
        assert "incomplete" in header_text.lower()


async def test_g_complete_log_shows_no_incomplete_indicator():
    # Guard against a false positive: the full, terminal-ended fixture must NOT
    # be flagged incomplete.
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        assert app.state.finished is True
        assert app.state.truncated is False
        header_text = str(app.query_one(StatusHeader).render())
        assert "incomplete" not in header_text.lower()
        assert "complete" in header_text.lower()  # "✓ complete"


# --------------------------------------------------------------------------- #
# (h) The stage-1 schema-conformance guarantee still holds for the fixture.
# --------------------------------------------------------------------------- #

def test_h_fixture_still_conforms_to_frozen_schema():
    events = read_events(G.FIXTURE_PATH)
    assert events, "fixture is empty or missing"

    problems: list[str] = []
    for event in events:
        if not event.known:
            problems.append(f"seq {event.seq}: unknown type {event.type!r}")
            continue
        missing = missing_required_fields(event)
        if missing:
            problems.append(f"seq {event.seq} {event.type}: missing {missing}")
        invalid = invalid_enum_fields(event)
        if invalid:
            problems.append(f"seq {event.seq} {event.type}: bad enum {invalid}")
    assert not problems, "\n".join(problems)

    # A well-formed, bookended log the renderer can replay end to end.
    assert is_replayable(events)
    assert events[0].type == "debate_started"
    assert events[-1].type == "debate_completed"


# --------------------------------------------------------------------------- #
# (i) The M4 emission set (thesis_recorded / collapse_metric) renders.
#
# These drive the *real* recorded M4 debate (a full six-persona committee run,
# ``tests/fixtures/m4_full_debate.jsonl``) — the recording the interim synthetic
# log was a stand-in for — and assert the additive M4 events are not merely
# tolerated but surfaced: the opening theses stamp the stance badges, and the
# disagreement-collapse metric raises the "caved" flag on the committee card.
# --------------------------------------------------------------------------- #

def _m4_events():
    return read_events(M4.GOLDEN_PATH)


async def test_i_m4_opening_theses_render_as_stance_badges():
    events = _m4_events()
    assert is_replayable(events)  # a complete, real recording

    app = TownHallApp(events=events, auto_replay=False)
    async with app.run_test() as pilot:
        # Fold until every committee member carries a stance — i.e. all six
        # opening ``thesis_recorded`` events have been rendered into the badges.
        await _drain_until(
            app,
            pilot,
            lambda: all(
                (m := app.state.personas.get(name)) is not None and bool(m.stance)
                for name in M4_COMMITTEE
            ),
        )
        stances = {name: app.state.personas[name].stance for name in M4_COMMITTEE}
        # The recorded opening theses: four bulls, two bears (Graham + Marks).
        assert stances["Warren Buffett"] == "bullish"
        assert stances["Charlie Munger"] == "bullish"
        assert stances["Benjamin Graham"] == "bearish"
        assert stances["Howard Marks"] == "bearish"


async def test_i_m4_collapse_metric_raises_the_caved_flag():
    app = TownHallApp(events=_m4_events(), auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()  # tolerate the full additive emission set
        await pilot.pause()

        # The constructed cave: the disagreement-collapse metric flags Benjamin
        # Graham and flips his stance to the majority; the standing dissenter
        # (Howard Marks, who held SELL) is not flagged.
        graham = app.state.personas["Benjamin Graham"]
        assert graham.caved is True
        assert graham.stance == "bullish"  # collapse_metric.stance_after
        marks = app.state.personas["Howard Marks"]
        assert marks.caved in (False, None)

        # The flag is visible on the committee card, not just in the view-model.
        graham_card = next(
            c for c in app.query(PersonaCard) if c.persona.name == "Benjamin Graham"
        )
        assert "caved" in str(graham_card.render()).lower()

        # A complete, terminal-ended recording renders without an incomplete flag.
        assert app.state.finished is True
        assert "incomplete" not in str(app.query_one(StatusHeader).render()).lower()
