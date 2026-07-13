"""Stage-2 TUI polish: the terminal-grade data surfaces.

Covers the three deliverables of the v2.1 Stage-2 pass, purely offline:

* the **header ticker strip** — the phase stepper (done ``✓`` / current glyph /
  future ``·``) derived only from folded phase events, the meters block
  (elapsed from event ``ts`` deltas — never the wall clock — plus cumulative
  tokens/cost from ``usage`` events and the ``usage_window`` snapshot), and the
  under-80-col degradation that drops the meters first;
* the **scorecard DataTable** — one row per vote with persona monogram +
  color, a directional vote pill, confidence, the changed-mind flag, and the
  muted vote source, plus the collapse-metric / disagreement cards;
* the **usage meter** — per-model accumulation in the fold and the
  end-of-debate rollup table, appended only when a terminal event lands so a
  truncated log (mid-replay of a crashed debate) never shows it.

Everything is driven by the recorded synthetic fixture or hand-fed events —
the renderer stays a pure event consumer, and replay/live share one fold.
"""

from __future__ import annotations

from rich.text import Text

from tinyic.persona_style import persona_monogram
from tinyic.tui import theme
from tinyic.tui.app import TownHallApp
from tinyic.tui.events import parse_event, read_events
from tinyic.tui.state import ArtifactState, TownHallState
from tinyic.tui.widgets import (
    HEADER_METERS_MIN_WIDTH,
    ArtifactCard,
    ScorecardTable,
    StatusHeader,
    UsageTable,
    build_header_meters,
    build_header_text,
    build_phase_stepper,
)

from tests.support import synthetic_events as G
from tests.tui.test_pilot_townhall import _truncated_events


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fold(events) -> TownHallState:
    state = TownHallState()
    for event in events:
        state.dispatch(event)
    return state


def _fold_until(events, predicate) -> TownHallState:
    """Fold events until just after ``predicate(event)`` first holds."""
    state = TownHallState()
    for event in events:
        state.dispatch(event)
        if predicate(event):
            return state
    raise AssertionError("predicate never matched")


def _fixture_events():
    return read_events(G.FIXTURE_PATH)


def _ev(type_: str, **payload):
    return parse_event({"type": type_, "payload": payload})


def _artifacts(state: TownHallState, kind: str) -> list[ArtifactState]:
    return [
        a for a in state.transcript
        if isinstance(a, ArtifactState) and a.kind == kind
    ]


def _cell_text(cell) -> str:
    return cell.plain if isinstance(cell, Text) else str(cell)


# --------------------------------------------------------------------------- #
# Header: the phase stepper (done ✓ / current glyph / future ·)
# --------------------------------------------------------------------------- #

def test_stepper_is_all_future_before_any_phase():
    stepper = build_phase_stepper(TownHallState())
    assert stepper.plain == "· opening   · cross-exam   · rebuttal   · verdict"


def test_stepper_marks_done_current_and_future_mid_debate():
    state = _fold_until(
        _fixture_events(),
        lambda e: e.type == "phase_started" and e.payload.get("phase") == "cross_exam",
    )
    stepper = build_phase_stepper(state)
    assert stepper.plain == "✓ opening   ◉ cross-exam   · rebuttal   · verdict"

    # Styles: done = accent, current = bold, future = muted (dark variant).
    spans = {stepper.plain[s.start:s.end]: str(s.style) for s in stepper.spans}
    assert spans["✓ opening"] == theme.accent(dark=True)
    assert spans["◉ cross-exam"] == "bold"
    assert spans["· rebuttal"] == theme.muted(dark=True)
    assert spans["· verdict"] == theme.muted(dark=True)


def test_stepper_transitions_one_phase_at_a_time():
    events = _fixture_events()
    state = TownHallState()
    seen: list[str] = []
    for event in events:
        state.dispatch(event)
        if event.type in {"phase_started", "phase_completed"}:
            seen.append(build_phase_stepper(state).plain)
    # First transition: opening becomes current; last: everything done.
    assert seen[0].startswith("◉ opening")
    assert seen[-1] == "✓ opening   ✓ cross-exam   ✓ rebuttal   ✓ verdict"
    # Monotonic: the number of done checks never decreases.
    checks = [s.count("✓") for s in seen]
    assert checks == sorted(checks)


def test_stepper_uses_the_supplied_pulse_glyph():
    state = _fold_until(_fixture_events(), lambda e: e.type == "phase_started")
    assert "● opening" in build_phase_stepper(state, glyph="●").plain
    # Default (resting frame) stays the historical static ◉ — replay-stable.
    assert "◉ opening" in build_phase_stepper(state).plain


def test_stepper_flags_an_errored_current_phase():
    state = _fold([
        _ev("debate_started", ticker="X", company_name="X Corp", personas=[]),
        _ev("phase_started", phase="cross_exam", index=1),
        _ev("debate_error", stage="debate", message="boom"),
    ])
    stepper = build_phase_stepper(state)
    assert "✗ cross-exam" in stepper.plain
    assert "◉" not in stepper.plain


def test_stepper_tolerates_a_forward_compatible_phase():
    state = _fold([
        _ev("debate_started", ticker="X", company_name="X", personas=[]),
        _ev("phase_started", phase="overtime", index=9),
    ])
    plain = build_phase_stepper(state).plain
    assert plain.endswith("◉ overtime")
    assert "· opening" in plain  # canonical four still present


# --------------------------------------------------------------------------- #
# Header: meters (event-derived, never the wall clock)
# --------------------------------------------------------------------------- #

def test_meters_accumulate_tokens_cost_and_window_from_events():
    meters = build_header_meters(_fold(_fixture_events())).plain
    assert "tok 45k/10.1k" in meters
    assert "$0.2115" in meters
    assert "+10 sub" in meters
    assert "win 18/110" in meters
    assert "elapsed 6:12" in meters  # recorded duration_s == 372.5


def test_meters_elapsed_derives_from_event_ts_deltas():
    # First 11 events span exactly 10 * 500ms of recorded ts — 0:05 — no matter
    # when or how slowly this test runs (replay must render like live).
    state = _fold(_fixture_events()[:11])
    assert state.duration_s is None  # no debate_completed folded
    assert "elapsed 0:05" in build_header_meters(state).plain


def test_meters_mid_replay_show_partial_accumulation():
    events = _fixture_events()
    state = _fold_until(
        events,
        lambda e: e.type == "phase_started" and e.payload.get("phase") == "rebuttal",
    )
    meters = build_header_meters(state).plain
    # Some usage already folded, but strictly less than the full-debate rollup.
    assert state.usage_calls > 0
    assert 0 < state.input_tokens < 45000
    assert "$0." in meters


# --------------------------------------------------------------------------- #
# Header: the two-line strip and its under-80-col degradation
# --------------------------------------------------------------------------- #

def test_header_is_two_lines_with_identity_stepper_and_meters():
    text = build_header_text(_fold(_fixture_events()), width=140)
    lines = text.plain.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("AAPL · Apple Inc.")
    assert "✓ complete" in lines[0]
    assert "$0.2115" in lines[0]  # meters on line 1 at a wide width
    assert lines[1] == "✓ opening   ✓ cross-exam   ✓ rebuttal   ✓ verdict"


def test_header_drops_meters_first_under_80_cols():
    state = _fold(_fixture_events())
    narrow = build_header_text(state, width=HEADER_METERS_MIN_WIDTH - 1)
    lines = narrow.plain.split("\n")
    assert len(lines) == 2
    assert "tok" not in lines[0] and "$" not in lines[0]
    # Identity and the stepper survive intact.
    assert lines[0].startswith("AAPL · Apple Inc.")
    assert "✓ verdict" in lines[1]


def test_header_never_overflows_its_width_with_meters():
    state = _fold(_fixture_events())
    for width in (60, 79, 80, 100, 120, 200):
        text = build_header_text(state, width=width)
        lines = text.plain.split("\n")
        assert len(lines) == 2
        # The meters are dropped rather than ever wrapping line 1.
        if "$" in lines[0]:
            assert len(lines[0]) <= width


def test_header_mid_replay_of_a_truncated_log():
    # A crashed debate's log (no terminal event): every header element still
    # renders — done/current stepper, partial meters, the incomplete flag.
    events = _truncated_events()
    state = _fold(events)
    state.truncated = True  # the app sets this once from the parsed log
    text = build_header_text(state, width=140)
    lines = text.plain.split("\n")
    assert "⚠ incomplete (truncated log)" in lines[0]
    assert "✓ opening" in lines[1]
    assert "◉ cross-exam" in lines[1]
    assert "· rebuttal" in lines[1] and "· verdict" in lines[1]
    assert state.input_tokens > 0  # partial meters accumulated


# --------------------------------------------------------------------------- #
# State: per-model usage accumulation + the end-of-debate rollup artifact
# --------------------------------------------------------------------------- #

def test_usage_by_model_accumulates_per_model():
    state = _fold(_fixture_events())
    anthropic = state.usage_by_model["anthropic/claude-opus-4-8"]
    assert anthropic["calls"] == 10
    assert anthropic["input_tokens"] == 19600
    assert anthropic["output_tokens"] == 4380
    assert anthropic["subscription_calls"] == 10  # null-cost lane
    assert anthropic["cost_usd"] == 0.0
    sol = state.usage_by_model["openai/gpt-5.6-sol"]
    assert sol["calls"] == 4 and round(sol["cost_usd"], 4) == 0.05
    assert len(state.usage_by_model) == 7


def test_usage_rollup_appended_once_at_debate_completed():
    events = _fixture_events()
    state = _fold(events)
    (rollup,) = _artifacts(state, "usage_rollup")
    assert rollup.title == "Usage · 25 calls · $0.2115 · +10 sub"
    assert len(rollup.rows) == 7
    assert rollup.key == "usage-rollup"
    # The rollup is the terminal artifact — after the scorecard region content.
    kinds = [a.kind for a in state.transcript if isinstance(a, ArtifactState)]
    assert kinds.index("usage_rollup") > kinds.index("scorecard")

    # Idempotent: a malformed log with a second terminal event appends nothing.
    state.dispatch(_ev("debate_completed", phases_completed=[], duration_s=1.0))
    assert len(_artifacts(state, "usage_rollup")) == 1


def test_usage_rollup_appended_after_a_debate_error_too():
    state = _fold([
        _ev("debate_started", ticker="X", company_name="X", personas=[]),
        _ev("usage", purpose="research", model_ref="openai/gpt-5.6",
            input_tokens=100, output_tokens=10, cached_tokens=0, cost_usd=0.01),
        _ev("debate_error", stage="data", message="boom"),
    ])
    kinds = [a.kind for a in state.transcript if isinstance(a, ArtifactState)]
    assert kinds == ["debate_error", "usage_rollup"]


def test_usage_rollup_absent_mid_replay_of_a_truncated_log():
    state = _fold(_truncated_events())
    assert _artifacts(state, "usage_rollup") == []
    assert state.usage_by_model  # accumulation still happened
    assert state.finished is False


# --------------------------------------------------------------------------- #
# State: scorecard rows and collapse-metric cards
# --------------------------------------------------------------------------- #

def test_scorecard_rows_merge_vote_recorded_details():
    state = _fold(_fixture_events())
    (scorecard,) = _artifacts(state, "scorecard")
    rows = {r["persona"]: r for r in scorecard.rows}
    assert len(rows) == 6
    # The scorecard's votes list has no changed_mind/source — they merge in
    # from the per-persona vote_recorded fold.
    assert rows["Peter Lynch"]["changed_mind"] is True
    assert rows["Warren Buffett"]["changed_mind"] is False
    assert all(r["source"] == "structured" for r in rows.values())
    assert rows["Benjamin Graham"]["vote"] == "SELL"
    assert rows["Benjamin Graham"]["confidence"] == "HIGH"


def test_scorecard_rows_fall_back_to_persona_votes_without_a_votes_list():
    state = _fold(_fixture_events()[:-1])  # everything but debate_completed
    state.dispatch(_ev("scorecard", votes=[], consensus="BUY",
                       bull_count=3, bear_count=1, hold_count=2))
    scorecard = _artifacts(state, "scorecard")[-1]
    rows = {r["persona"]: r for r in scorecard.rows}
    assert len(rows) == 6  # rebuilt from the folded vote_recorded events
    assert rows["Li Lu"]["vote"] == "BUY"
    assert rows["Peter Lynch"]["changed_mind"] is True


def test_collapse_metric_becomes_a_stance_colored_transcript_card():
    state = _fold(_fixture_events())
    cards = _artifacts(state, "collapse_metric")
    assert len(cards) == 2
    by_title = {c.title: c for c in cards}
    caved = by_title["Collapse check · Peter Lynch · caved"]
    held = by_title["Collapse check · Howard Marks · held"]
    assert "neutral → cautious-hold" in caved.body
    assert bool(caved.payload["caved"]) is True
    assert bool(held.payload["caved"]) is False


# --------------------------------------------------------------------------- #
# Widgets: the scorecard DataTable
# --------------------------------------------------------------------------- #

async def test_pilot_scorecard_renders_as_a_datatable():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        card = app.query_one(ScorecardTable)
        table = card._table
        assert table.row_count == 6

        # Summary line: consensus + directional counts, scannable collapsed.
        summary = str(card._summary.render())
        assert "Scorecard" in summary
        assert "consensus" in summary and "BUY" in summary
        assert "▲ 3" in summary and "● 2" in summary and "▾ 1" in summary

        rows = [
            [_cell_text(c) for c in table.get_row_at(i)]
            for i in range(table.row_count)
        ]
        by_name = {row[0]: row for row in rows}
        buffett = by_name[f"{persona_monogram('Warren Buffett')} Warren Buffett"]
        assert "▲ BUY" in buffett[1]
        assert buffett[2] == "HIGH"
        assert buffett[3] == "—"  # did not change his mind
        assert buffett[4] == "structured"

        graham = by_name[f"{persona_monogram('Benjamin Graham')} Benjamin Graham"]
        assert "▾ SELL" in graham[1]

        lynch = by_name[f"{persona_monogram('Peter Lynch')} Peter Lynch"]
        assert "● HOLD" in lynch[1]
        assert "⚑ changed" in lynch[3]  # the changed-mind flag


async def test_pilot_scorecard_pills_and_colors_track_the_theme():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()
        card = app.query_one(ScorecardTable)
        assert card._built_for is not None and card._built_for[0] is True

        vote_cell = card._table.get_row_at(0)[1]
        assert str(vote_cell.style) == theme.vote_pill("BUY", dark=True)

        await pilot.press("d")  # -> tinyic-light; _sync rebuilds the table
        await pilot.pause()
        assert card._built_for[0] is False
        vote_cell = card._table.get_row_at(0)[1]
        assert str(vote_cell.style) == theme.vote_pill("BUY", dark=False)


# --------------------------------------------------------------------------- #
# Widgets: the usage rollup table
# --------------------------------------------------------------------------- #

async def test_pilot_usage_rollup_table_renders_at_debate_end():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        card = app.query_one(UsageTable)
        assert "Usage · 25 calls · $0.2115 · +10 sub" in str(card._summary.render())

        table = card._table
        assert table.row_count == 7  # one row per distinct model
        rows = [
            [_cell_text(c) for c in table.get_row_at(i)]
            for i in range(table.row_count)
        ]
        by_model = {row[0]: row for row in rows}
        anthropic = by_model["anthropic/claude-opus-4-8"]
        assert anthropic[1] == "10"
        assert anthropic[2] == "19.6k" and anthropic[3] == "4.4k"
        assert anthropic[5] == "sub"  # subscription lane, no dollar figure
        sol = by_model["openai/gpt-5.6-sol"]
        assert sol[5] == "$0.0500"


# --------------------------------------------------------------------------- #
# Widgets: collapse-metric and disagreement cards
# --------------------------------------------------------------------------- #

async def test_pilot_collapse_cards_render_caved_red_held_green():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        cards = [
            c for c in app.query(ArtifactCard)
            if c.artifact.kind == "collapse_metric"
        ]
        assert len(cards) == 2
        caved = next(c for c in cards if c.has_class("caved"))
        held = next(c for c in cards if c.has_class("held"))
        assert not caved.has_class("held") and not held.has_class("caved")

        caved_text = str(caved.render())
        assert "Peter Lynch" in caved_text
        assert "⚑ caved" in caved_text
        assert "neutral → cautious-hold" in caved_text

        held_text = str(held.render())
        assert "Howard Marks" in held_text
        assert "✓ held" in held_text


async def test_pilot_disagreement_card_names_both_sides():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        card = next(
            c for c in app.query(ArtifactCard)
            if c.artifact.kind == "disagreement"
        )
        text = str(card.render())
        assert "Disagreement · Valuation vs. quality" in text
        assert "Warren Buffett" in text and "Benjamin Graham" in text
        assert "Quality justifies the price" in text
        assert "resolution" in text.lower()


# --------------------------------------------------------------------------- #
# Mid-replay of a truncated log: the tables simply are not there yet
# --------------------------------------------------------------------------- #

async def test_pilot_truncated_log_renders_header_but_no_tables():
    events = _truncated_events()
    app = TownHallApp(events=events, auto_replay=False)
    async with app.run_test() as pilot:
        app.replay_all_now()
        await pilot.pause()

        assert len(app.query(ScorecardTable)) == 0
        assert len(app.query(UsageTable)) == 0

        header_text = str(app.query_one(StatusHeader).render())
        assert "incomplete" in header_text
        lines = header_text.split("\n")
        assert len(lines) == 2
        assert "✓ opening" in lines[1] and "◉ cross-exam" in lines[1]


# --------------------------------------------------------------------------- #
# The mounted header degrades with the real terminal width
# --------------------------------------------------------------------------- #

async def test_pilot_header_drops_meters_at_80_cols():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test(size=(80, 24)) as pilot:
        app.replay_all_now()
        await pilot.pause()
        header_text = str(app.query_one(StatusHeader).render())
        assert "AAPL · Apple Inc." in header_text
        assert "✓ verdict" in header_text
        assert "tok" not in header_text and "$" not in header_text


async def test_pilot_header_shows_meters_on_a_wide_terminal():
    app = TownHallApp(G.FIXTURE_PATH, auto_replay=False)
    async with app.run_test(size=(180, 24)) as pilot:
        app.replay_all_now()
        await pilot.pause()
        header_text = str(app.query_one(StatusHeader).render())
        assert "tok 45k/10.1k" in header_text
        assert "$0.2115" in header_text
        assert "win 18/110" in header_text
