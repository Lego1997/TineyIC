# AGENTS.md — driving TinyIC headlessly

TinyIC convenes an AI investment committee (the default six are Buffett, Munger,
Graham, Lynch, Marks, and Li Lu) to debate a stock across four phases — opening, cross-examination,
rebuttal, verdict — and emits a scorecard, an investment memo, and a disagreement
analysis. **Every debate is streamed as a machine-readable JSONL event log**, so an
agent can drive a full debate, steer it mid-flight, and consume the result without a
human or a UI. This file is that contract.

The golden rule: **in `--json` mode STDOUT carries nothing but event JSONL** (one JSON
object per line). Human-readable progress and all diagnostics go to STDERR. Parse
STDOUT line-by-line; never scrape STDERR.

---

## Install & run

v1 is distributed **from git source only** (no PyPI). Both work without cloning:

```bash
# One-shot, from a clone on disk:
uvx --from ./src/tinyic tinyic --help
# One-shot, from the repo (once public):
uvx --from 'git+https://github.com/Lego1997/TineyIC.git#subdirectory=src/tinyic' tinyic --help

# Or from a clone, for repeated use:
uv sync            # installs both workspace packages (tinyic + the vendored tinytroupe)
uv run tinyic --help
```

You need one LLM credential (an API key, or a supported subscription lane). Check and
set up non-interactively / interactively:

```bash
tinyic doctor --json      # machine-readable auth/provider probe; exit 0 = ready, 3 = not
tinyic onboard            # interactive TUI wizard to add & verify a credential
tinyic models --json      # one JSON document: every provider's model catalog
```

Providers: `openai`, `anthropic`, `grok`, `google`, `kimi`, `ollama` (local).
`tinyic models [provider] [--refresh] [--json]` lists what each can run —
static catalogs by default; `--refresh` merges each provider's live model
listing (a provider whose refresh fails degrades to its static catalog with a
warning; the command still exits 0).

Missing auth never blocks on a prompt in headless mode — it exits `3` with a
`doctor`-style reason on STDERR.

---

## The debate commands that matter

For a human-operated run, `tinyic debate AAPL` starts the secured localhost web
Town Hall and opens it in the browser. Use `--no-open` to print the capability URL
without launching a browser, `--port N` to select the loopback port, and `--no-wait`
to exit after the final SSE client disconnects. The viewer binds only to
`127.0.0.1`, requires its token/cookie plus literal local Host/Origin values, and
accepts only authenticated JSON for steering/control. It supports next-turn or
next-phase steering, interrupt, pause/resume, next phase, stop, reasoning toggle,
and export; replay disables all writes.

### 1 · Run a debate — `tinyic debate <ticker|company> --headless --json`

```bash
tinyic debate AAPL --headless --json > aapl.events.jsonl
```

`--headless --json` streams the event log to STDOUT **as it happens**; progress lands on
STDERR. Useful flags:

| flag | effect |
|---|---|
| `--personas a,b,c` | committee by built-in/user registry slug (default: overlay committee, then the six built-ins; min 2, max 6) |
| `--preset NAME` | a named committee preset from `tinyic.toml` |
| `--model provider/model` | per-debate override for every role (e.g. `openai/gpt-5.6-sol`, `anthropic/claude-opus-4-8`, `grok/grok-4.5`, `kimi/kimi-k2.6`, `ollama/qwen3:32b`) |
| `--thinking L` | reasoning level: `off·minimal·low·medium·high·xhigh·max` |
| `--da persona` | pin the cross-exam devil's advocate |
| `--no-research` | skip the web-research data source (faster, cheaper) |
| `--steer-stdin` | read JSON steering lines from STDIN (see below) |
| `--yes` | assume yes / never prompt (non-interactive) |

The run is also written to `~/.tinyic/runs/<debate_id>.jsonl` (override with
`TINYIC_RUNS_DIR`), where `<debate_id>` = `<ticker>-<yyyymmdd>-<short-random>` and is the
first line's `debate_id`. Grab it to consume the result later:

```bash
debate_id=$(head -1 aapl.events.jsonl | jq -r .debate_id)
```

### 2 · Read the result — `tinyic result <id|path> --json`

If you don't want to consume the stream, read the single assembled document instead
(schema compatibility promise #3):

```bash
tinyic result "$debate_id" --json | jq '{consensus: .scorecard.consensus, cost: .usage.total.cost_usd}'
```

It returns one JSON object: `scorecard`, per-persona `votes`, the `memo` (five sections),
`disagreements`, `collapse_metrics`, and a `usage` rollup (per-model / per-purpose / total
tokens + `cost_usd`). It tolerates a truncated log, reporting `status` = `complete` /
`partial` / `incomplete` / `error` rather than failing.

### 3 · Replay or export a recorded debate — `tinyic replay` / `tinyic export`

```bash
tinyic replay "$debate_id"                     # read-only web replay, zero LLM calls
tinyic export "$debate_id" --html -o aapl.html # one self-contained static HTML page
tinyic export "$debate_id" --md  > aapl.md     # scorecard/memo/transcript/disagreements
```

`export` writes the artifact to STDOUT unless `-o FILE` is given. The HTML is a single
file — no external assets — safe to share as-is. `runs list` enumerates what's on disk:

```bash
tinyic runs list --json | jq '.runs[] | {debate_id, ticker, status, consensus}'
```

---

## Research and inspect custom personas

Persona research is a separate, human-summary command; `list` and `show` have
stable one-document JSON forms for agents:

```bash
# Built-in howard_marks is protected, so choose a distinct variant slug.
tinyic persona research "Howard Marks" \
  --slug howard_marks_researched --max-searches 4 --yes

tinyic persona list --json
tinyic persona show howard_marks_researched --json
```

`research` checks built-in and existing-user collisions before credential or
backend setup. Built-ins can never be overwritten; replacing an existing user
persona requires `--force`. Use `--model provider/model` for an exact binding,
or let TinyIC choose the first usable **API-key** search lane in fixed priority
`openai → grok → google → kimi`. Budget-capable configured bindings win within a
provider, and recommended provider bindings cover credentials not present in an
OpenAI-only committee preset. An ineligible binding is skipped before its
credential or provider is touched. Subscription and Ollama lanes are not
persona-research lanes.
Google persona research is pinned to the budget-safe
`google/gemini-2.5-flash` lane; explicit Gemini 3 bindings are rejected before
provider calls and skipped during automatic selection. This does not change the
ordinary debate model catalog.

Before provider calls, the estimate and progress go to STDERR. `--yes` is
required for non-interactive use; without an eligible lane the command exits
`3` with `no_search_capable_lane` and onboarding guidance. The default cap is
12 billable search units, or 16 total Kimi `$web_search` echo rounds;
`--max-searches N` sets an explicit 1–16 cap. A Google unit is one Gemini 2.5
grounded prompt regardless of its internal queries and is estimated at the
worst-case $0.035 Google Search grounding fee.

Success stages `~/.tinyic/personas/<slug>.agent.json` and
`<slug>.dossier.md`, then installs each with an atomic replace and rollback on
a detected replacement failure. Fewer than three independent source domains cause
`insufficient_sources` and write nothing; three or four domains are marked
thin; normal quality requires at least five domains including a primary source.
Every generated claim is ledger-verified, quotations are checked verbatim, and
the artifacts contain public-record-only educational disclaimers.

Use two to six registry slugs with `--personas`. For a persistent default, put
this at the top level of `~/.tinyic/tinyic.toml`:

```toml
committee = ["warren_buffett", "howard_marks_researched", "li_lu"]
```

Resolution is `--personas` → overlay `committee` → the built-in six. Unknown,
duplicate, or out-of-range committees fail instead of silently substituting.

---

## The event stream (schema v1)

Full contract: [`docs/event-schema.md`](docs/event-schema.md). Every line is one envelope:

```json
{"v":1,"seq":42,"ts":"2026-07-12T20:31:07.114Z","debate_id":"aapl-20260712-a3f2","type":"talk_delta","payload":{"turn_id":"turn-0007","text":"…"}}
```

- `seq` is monotonic per debate (no gaps); resync by `seq`.
- **Ignore unknown event `type`s and unknown payload fields** — within v1, changes are
  additive only.
- A log is replayable iff it starts with `debate_started` and ends with `debate_completed`
  or `debate_error`; handle a truncated log (crash mid-debate) gracefully.
- **Secrets, keys, and OAuth material never appear in any payload, log, or export.**

Event types you'll act on most: `debate_started`, `data_ready`, `phase_started` /
`phase_completed`, `turn_started`, `think_delta` / `talk_delta` (+ their `*_completed`
one-shots), `turn_completed` / `turn_interrupted`, `vote_recorded`, `scorecard`,
`memo_section`, `disagreement`, `collapse_metric`, `usage`, and the `steering_*` acks
below. `debate_completed` / `debate_error` are terminal.

---

## Steering over STDIN (`--steer-stdin`)

With `--steer-stdin`, each STDIN line is one JSON command. `steer` and `queue`
commands emit `steering_submitted`, followed by exactly one `steering_delivered`
or `steering_dropped`. An interrupt emits `turn_interrupted` only when it affects
a turn; a no-op interrupt emits no event.

```json
{"type":"steer",  "target":"Warren Buffett", "text":"Press the China supply-chain risk."}
{"type":"queue",  "text":"Everyone, tie your verdict to a valuation multiple."}
{"type":"interrupt", "text":"Stop — reconsider the downside first."}
```

- `steer` — delivered at the **next speaker-turn** boundary (optionally `target`ed).
- `queue` — delivered at the **next phase** boundary.
- `interrupt` — requests share one latest-wins slot. Untargeted requests retain
  the existing next-in-flight behavior. A targeted request affects only its
  normalized matching persona's in-flight turn; a different in-flight speaker
  or an unknown target makes it expire immediately with one WARNING on STDERR
  and no event. It never waits or falls back to broadcast. A matching interrupt
  cancels the turn (or discards it on arrival) and lets the speaker retake.

End-to-end, feeding steering from a script:

```bash
printf '%s\n' \
  '{"type":"steer","target":"Warren Buffett","text":"Press the China supply-chain risk."}' \
  '{"type":"queue","text":"Everyone, tie your verdict to a valuation multiple."}' \
  | tinyic debate AAPL --headless --json --steer-stdin > aapl.events.jsonl
```

---

## Exit codes

| code | meaning |
|---|---|
| `0` | complete — the debate finished all phases (`debate` / `result`), or the report rendered (`export`) |
| `2` | partial — some phases ran, then a later stage failed (`debate` / `result`) |
| `3` | setup/auth/persona error — nothing ran, a run/credential wasn't found, or a persona gate refused; STDERR carries the reason |

Non-interactive runs never prompt: pass `--yes`; missing auth → `3`.

---

## Expected durations & costs

A full six-member, four-phase debate is roughly **30–45 model calls** (six openers, a
capped cross-examination with a rotating devil's advocate, rebuttals, verdicts, then vote
extraction and a mixture-of-agents memo). Wall-clock and cost scale with the model and
`--thinking` level: budget **minutes, not seconds**, and **cents to low single-dollars**
per debate on a mid-tier reasoning model. Fewer personas, a cheaper `--model`,
`--no-research`, or a lower `--thinking` level all cut both.

Don't guess at the numbers — every run reports the real ones. `usage` events (and the
`result --json` `usage` rollup) carry per-call `input_tokens` / `output_tokens` /
`cached_tokens` and `cost_usd`. On subscription lanes `cost_usd` is `null`; those emit a
`usage_window` message-window meter instead.

Persona research reports its estimate before the confirmation gate and its
captured usage afterward. It performs up to six planned research angles plus
five dossier syntheses, persona synthesis, and verification; Kimi searches may
span several echo rounds within the run-wide cap. Budget minutes and verify the
displayed estimate rather than assuming debate pricing applies.

---

## Guarantees you can rely on

- **STDOUT is clean** in `--json`: only event JSONL. Progress, banners, and errors go to
  STDERR.
- **The schema is stable**: `seq` / `ts` / `type` never change meaning within v1; new
  fields and types are additive. Ignore what you don't recognize.
- **No secrets** ever appear in events, logs, exports, or generated persona artifacts.
- **Replayable & inspectable offline**: recorded logs replay and export with zero LLM
  calls, so a debate can be produced once and consumed, rendered, or diffed forever after.
