"""Regenerate the M1 golden event-log fixture from the mocked-debate generator.

``tests/fixtures/m1_mocked_debate.jsonl`` is *generated behaviour*, not a
hand-authored sample: it is the canonical output of a fully mocked two-persona
debate and drives the renderer/replay tests
(``test_golden_mocked_debate_log_is_schema_valid_and_replayable`` and
``test_mocked_debate_generation_matches_normalized_golden``). Regenerate it, and
commit the diff, whenever debate event emission legitimately changes.

    uv run python scripts/regen_golden_log.py

The mocks (persona act, usage counter, votes, data package) are single-sourced
from ``tests/test_event_stream.py`` so the fixture can never drift from the
generator the comparison test uses. A fresh, isolated ``TINYIC_STATE_DIR`` makes
the devil's-advocate rotation counter start at 0 (deterministic DA = the first
persona) and keeps the real ``~/.tinyic`` untouched.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Stable placeholder written into debate_completed.result_ref (the comparison
# test masks this field; a fixed value keeps the committed fixture clean).
_RESULT_REF_PLACEHOLDER = "tests/fixtures/m1_mocked_debate.jsonl"


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tinyic-regen-"))
    os.environ["TINYIC_STATE_DIR"] = str(tmp / "state")
    os.environ["TINYIC_RUNS_DIR"] = str(tmp / "runs")

    from tinyic.events import EventLog
    from tinytroupe.agent import TinyPerson
    from tests import test_event_stream as gen

    usage_counter = gen._UsageCounter()

    def act_with_usage(self, **kwargs):
        usage_counter.record_turn()
        return gen._mock_persona_act(self, **kwargs)

    log_path = tmp / f"{gen.DEBATE_ID}.jsonl"
    with (
        patch.object(gen.clients_module, "client", lambda: usage_counter),
        patch.object(gen.InvestorPersona, "act", act_with_usage),
        patch.object(
            gen.InvestorPersona,
            "consolidate_episode_memories",
            lambda _self: False,
        ),
        patch.object(gen.debate_module, "extract_votes", gen._mock_votes),
        patch.object(gen.DebateOrchestrator, "get_cost_stats", lambda _self: {}),
        patch.object(TinyPerson, "communication_display", False),
    ):
        with EventLog(
            gen.DEBATE_ID, path=log_path, clock=lambda: gen.FIXED_NOW
        ) as event_log:
            gen.debate_module.run_debate(
                "AAPL",
                ["warren_buffett", "benjamin_graham"],
                data_package=gen._mock_data_package(),
                event_log=event_log,
            )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        event = json.loads(line)
        if event.get("type") == "debate_completed":
            event["payload"]["result_ref"] = _RESULT_REF_PLACEHOLDER
            out.append(json.dumps(event, separators=(",", ":")))
        else:
            out.append(line)

    gen.GOLDEN_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {gen.GOLDEN_PATH} ({len(out)} events)")


if __name__ == "__main__":
    main()
