"""Regenerate the M4 golden event-log fixture from the full-debate generator.

``tests/fixtures/m4_full_debate.jsonl`` is *generated behaviour*, not a
hand-authored sample: it is the canonical event log of a fully mocked
six-persona committee debate that exercises the whole M4 emission set
(structured theses + verdicts, the MoA memo / disagreement synthesis, and the
disagreement-collapse metrics). It drives the M4 Definition-of-Done acceptance
suite and the renderer/replay tests. Regenerate it, and commit the diff,
whenever debate event emission legitimately changes::

    uv run python scripts/regen_m4_golden.py

The generator (persona prose, aggregator response, committee wiring) is
single-sourced from ``tests/support/m4_debate.py`` so the fixture can never drift
from the generator the drift-guard test uses. A fresh, isolated
``TINYIC_STATE_DIR`` makes the devil's-advocate rotation counter start at 0
(deterministic DA = the first committee member) and keeps the real ``~/.tinyic``
untouched.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tinyic-m4-regen-"))
    # Isolate per-install state (fresh DA rotation counter) and run logs.
    os.environ["TINYIC_STATE_DIR"] = str(tmp / "state")
    os.environ["TINYIC_RUNS_DIR"] = str(tmp / "runs")

    from tinyic.events import EventLog
    from tests.support import m4_debate as gen

    log_path = tmp / f"{gen.DEBATE_ID}.jsonl"
    with EventLog(
        gen.DEBATE_ID, path=log_path, clock=lambda: gen.FIXED_NOW
    ) as event_log:
        gen.record_debate(event_log)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        event = json.loads(line)
        if event.get("type") == "debate_completed":
            event["payload"]["result_ref"] = gen.RESULT_REF_PLACEHOLDER
            out.append(json.dumps(event, separators=(",", ":")))
        else:
            out.append(line)

    gen.GOLDEN_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {gen.GOLDEN_PATH} ({len(out)} events)")


if __name__ == "__main__":
    main()
