# TinyIC

**An AI investment committee you can *watch think*.** Six legendary value
investors — Buffett, Munger, Graham, Lynch, Marks, Li Lu — debate any public
company through a structured four-phase protocol, live in your terminal.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Built with TinyTroupe](https://img.shields.io/badge/built%20with-TinyTroupe-orange.svg)](https://github.com/microsoft/TinyTroupe)
[![Interface: TUI · headless · replay](https://img.shields.io/badge/interface-TUI%20·%20headless%20·%20replay-8a63d2.svg)](AGENTS.md)

Type a ticker. The committee convenes, each investor's *private reasoning* is one
keypress away, you interject mid-debate like a moderator, and every debate is a
replayable artifact — a scorecard, an investment memo, and a disagreement
analysis you can re-render or hand to another AI agent forever after.

> **Educational tool, not investment advice.** TinyIC produces AI-generated
> analysis for research and entertainment. It is not financial, investment, or
> trading advice. Do your own diligence; consult a licensed professional before
> making any investment decision.

---

## Why this is different

The category leader,
[virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), runs
**parallel, independent** persona analyses that a portfolio manager stitches
together — the personas never interact, you never see them reason, and you can't
touch a run in flight. TinyIC is built around the opposite bet:

- **Genuine cross-examination** — personas argue *with each other* under
  anti-sycophancy controls and a rotating devil's advocate, not in isolation.
- **Visible thinking** — every persona's THINK stream and cognitive state
  (goals, attention, emotions) is first-class UI, not discarded.
- **Live steering** — interject mid-debate: press a persona on a risk, queue a
  question for the next phase, or interrupt a turn — Codex-style.
- **Agent-launchable** — a documented JSONL event contract lets any other AI
  agent, script, or CI job drive a full debate and consume the verdict as
  structured events. No one else in the category ships this.

## The committee

| Persona | Registry name | Philosophy |
|---|---|---|
| **Warren Buffett** | `warren_buffett` | Wonderful companies at fair prices; durable moats; long-term compounding |
| **Charlie Munger** | `charlie_munger` | Mental models, inversion, quality over cheapness |
| **Benjamin Graham** | `benjamin_graham` | Margin of safety, quantitative screens, Mr. Market |
| **Peter Lynch** | `peter_lynch` | Invest in what you know; growth at a reasonable price; tenbaggers |
| **Howard Marks** | `howard_marks` | Second-level thinking, market cycles, risk as permanent loss |
| **Li Lu** | `li_lu` | Emerging-market value, long-duration compounding, circle of competence |

Each persona is grounded in decades of the investor's real writings (letters,
books, speeches), not a shallow role-play.

---

## Quickstart

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/), and one LLM
credential (an API key *or* a supported subscription lane — see
[Auth & lanes](#auth--lanes)).

```bash
git clone https://github.com/Lego1997/TineyIC.git
cd TineyIC

uv sync            # installs both workspace packages (tinyic + the vendored tinytroupe)
uv sync --extra cn # optional: China A-share / HK market data via akshare (heavy, opt-in)

tinyic onboard     # interactive wizard: detect access, pick a lane, verify, persist
tinyic debate AAPL # convene the committee — opens the live Town Hall TUI
```

`tinyic onboard` walks auth setup in a full-screen wizard and persists only
verified routes to your OS keyring — then, once a provider verifies, lets you
pick that provider's model as the committee default (saved to
`~/.tinyic/tinyic.toml`). Prefer a `.env`? `OPENAI_API_KEY=sk-…` in a `.env`
file (copy `.env.example`) is picked up automatically. Other keys:
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` (Grok — also enables the
X/Twitter sentiment source), and `MOONSHOT_API_KEY` (or `KIMI_API_KEY`) for
Kimi.

Not sure you're ready? `tinyic doctor` prints a machine- or human-readable
provider/auth probe (exit `0` = ready, `3` = setup needed); add `--live` for an
explicit one-token check against the resolved model binding. `tinyic models`
lists what every provider can run (`--refresh` merges each provider's live
model listing over the shipped catalog).

---

## Three faces, one engine

Every debate is streamed as an append-only **JSONL event log**. The engine only
emits events; the renderers only consume them. That single contract is why the
same debate can be watched, scripted, or shared — and replayed offline forever.

### 1. TUI — the Town Hall (for humans)

```bash
tinyic debate AAPL                     # the six-member committee, default preset
tinyic debate NVDA --personas warren_buffett,howard_marks,li_lu
tinyic debate "Costco" --preset heterogeneous --thinking high
```

A Textual full-screen town hall: watch each persona stream its speech and
reasoning in real time, expand any THINK block, and steer from the composer.

**The Town Hall.** Terminal-grade data where the numbers live, theater where
the personas live. The header is a ticker strip — identity, live elapsed /
token / cost meters, a four-phase stepper — and phases open as centered act
headers (`ACT II · CROSS-EXAMINATION`, devil's advocate credited). The
committee bench renders each investor as a medallion card (a two-letter
monogram on the persona's own color) with their model binding and live
attention; the current speaker is spotlit in their color while the idle bench
mutes, and a streaming THINK breathes a soft glyph pulse. Turn cards wear
their speaker's color and render finished speech as markdown; the verdict
lands as a real scorecard table inside a double-rule frame, followed by a
per-model usage rollup. Two registered themes (`tinyic-dark` / `tinyic-light`)
cover dark and light terminals — `d` cycles them in the town hall and the
onboarding wizard alike — and a replayed log renders identically to the live
run that produced it.

<!-- Screenshots coming soon. -->
> _Screenshots and a recorded demo are coming soon._

### 2. Headless CLI — the agent interface

```bash
tinyic debate AAPL --headless --json > aapl.events.jsonl
```

`--headless --json` streams the event log to **STDOUT as it happens** (one JSON
object per line); human-readable progress goes to STDERR. `--steer-stdin` reads
JSON steering commands from STDIN. This is the interface for AI agents, scripts,
and CI — fully specified in **[AGENTS.md](AGENTS.md)** and
[`docs/event-schema.md`](docs/event-schema.md).

### 3. Replay & export — shareable artifacts

Every run is recorded to `~/.tinyic/runs/<debate_id>.jsonl` (override with
`TINYIC_RUNS_DIR`). Re-render or export any recorded debate with **zero LLM
calls**:

```bash
tinyic runs list                                # what's on disk
tinyic replay <debate_id>                        # re-render in the TUI
tinyic result <debate_id> --json                 # the assembled scorecard/memo/usage document
tinyic export <debate_id> --html -o aapl.html    # one self-contained static HTML page
tinyic export <debate_id> --md  > aapl.md        # scorecard/memo/transcript/disagreements
```

The exported HTML is a single file with no external assets — safe to share as-is.

---

## Driving TinyIC from another agent

TinyIC is designed to be launched headlessly by Claude Code, Codex, cron, or any
script. The golden rule: **in `--json` mode STDOUT carries nothing but event
JSONL** — parse it line-by-line, never scrape STDERR. Steer a live debate over
STDIN:

```bash
printf '%s\n' \
  '{"type":"steer","target":"Warren Buffett","text":"Press the China supply-chain risk."}' \
  '{"type":"queue","text":"Everyone, tie your verdict to a valuation multiple."}' \
  | tinyic debate AAPL --headless --json --steer-stdin > aapl.events.jsonl
```

The full contract — commands, event schema, steering semantics, exit codes, and
expected costs — lives in **[AGENTS.md](AGENTS.md)**.

---

## How a debate works

Four phases, run as a structured protocol under a rules-based moderator:

1. **Opening** — each investor presents an initial thesis (bullish/bearish/neutral, with claims).
2. **Cross-examination** — investors challenge each other; a rotating devil's advocate is pinned to press the consensus.
3. **Rebuttal** — each investor responds to criticism and refines (or defends) its position; "caved under pressure" moments are flagged.
4. **Verdict** — a Buy / Hold / Sell vote with confidence and reasoning.

Anti-convergence controls (persona-specific reinforcement injected every turn +
the rotating devil's advocate) keep the personas from collapsing into
groupthink. Afterward, an aggregator synthesizes a five-section investment memo
and a disagreement analysis grounded in the full transcript, and a scorecard
rolls up the votes.

## Auth & lanes

TinyIC needs exactly one working LLM credential. Providers are **in-house
adapters** (no LiteLLM/aisuite) across four wire formats, with two kinds of
credential:

- **API keys** — OpenAI, Anthropic, Google, Grok (`XAI_API_KEY`), Kimi /
  Moonshot AI (`MOONSHOT_API_KEY` or `KIMI_API_KEY`; `MOONSHOT_BASE_URL`
  selects the China endpoint), and any OpenAI-compatible endpoint, plus
  **Ollama** for fully local models.
- **Subscription lanes** — an existing **OpenAI Codex / ChatGPT** subscription
  (via the Codex runtime), an **Anthropic Claude Code** subscription (via the
  Claude Agent SDK), or a **Grok SuperGrok / X Premium+** subscription (a
  read-only reuse of your `grok` CLI sign-in, or a device-code sign-in of
  TinyIC's own), so you can run a committee without paying per token.

> **Policy note.** The Anthropic and Grok subscription lanes are each gated by
> a `policy_guard` kill switch in `tinyic.toml` (`[auth.anthropic]` /
> `[auth.grok]`). Google is **API-key-only by policy**: Google's terms prohibit
> third-party reuse of consumer-subscription OAuth (the June 2026 Antigravity
> transition), so TinyIC ships no Google subscription lane. Subscription lanes
> are **additive, never load-bearing**: if a lane is disabled or a rate window
> is exhausted, an API-key profile takes over as overflow. Third-party
> subscription policies change — use the lanes at your own discretion.

Committees are described by **presets** in `tinyic.toml` (a `default`
single-model preset and a mixed-provider `heterogeneous` example). Select one
with `--preset`, or force every role onto one model with
`--model provider/model` (e.g. `openai/gpt-5.6-sol`, `grok/grok-4.5`,
`kimi/kimi-k2.6`, `ollama/qwen3:32b`) and
`--thinking off|minimal|low|medium|high|xhigh|max`. Discover what each
provider offers with `tinyic models [provider] [--refresh] [--json]`, and set
a personal default from the onboarding wizard's model step (persisted to the
`~/.tinyic/tinyic.toml` overlay, which wins over the repo config key-by-key).

## Architecture

```
                 ┌──────────────────────────────────────┐
                 │              tinyic engine             │
                 │  personas · moderator · debate loop    │
                 │  data pipeline · extraction · MoA memo  │
                 │      (vendored, forked TinyTroupe)      │
                 └───────────────┬────────────────────────┘
   ModelBinding per persona      │  emits typed events
                                 ▼
                 ┌──────────────────────────────────────┐
                 │   EVENT STREAM (append-only JSONL)     │
                 │   the ONLY engine → renderer channel    │
                 └──┬───────────┬───────────┬────────────┘
                    ▼           ▼           ▼
                ┌───────┐  ┌─────────┐  ┌──────────────┐
                │  TUI  │  │ headless│  │ HTML / MD     │
                │Textual│  │ CLI json│  │ replay export │
                └───────┘  └─────────┘  └──────────────┘
```

**The load-bearing rule:** renderers never read engine internals; they consume
events. Replay is just re-feeding a recorded log to any renderer. This is what
makes the TUI, headless mode, and export cheap variants of one product — and it
is enforced by tests.

```
src/
├── tinyic/                     # the application
│   ├── cli.py                  # argparse entry point (tinyic debate|runs|result|export|replay|models|doctor|onboard)
│   ├── headless.py             # the debate worker + log-follower bridge (interactive vs --json)
│   ├── events.py               # EventLog + schema-v1 envelope (the engine↔renderer contract)
│   ├── result.py · report.py   # result document assembly · HTML/MD renderers
│   ├── personas/               # 6 investor configs + registry
│   ├── data/                   # graceful-degradation data pipeline (yfinance, EDGAR, news, sentiment, research)
│   ├── debate/                 # orchestrator, moderator, steering, extraction, MoA memo, analytics
│   ├── models/                 # model-agnostic layer: bindings, adapters (×wire-formats), thinking ladder, presets, catalog
│   ├── auth/                   # profiles, keyring, OAuth/subscription lanes, doctor, live probe
│   └── tui/                    # Textual Town Hall + onboarding wizard + live follower
└── tinytroupe/                 # forked Microsoft TinyTroupe (vendored; treat deliberately)
```

## Development

```bash
uv sync                                    # install both workspace packages
uv run pytest tests/                        # offline suite (no API key needed; network/LLM mocked)
uv run pytest tests/ -m live_api            # live-API tests (need real credentials)
tinyic doctor --live                        # binding-routed one-token live auth check
```

Offline tests mock every LLM/network call — keep it that way; anything hitting
the network belongs under the `live_api` marker. See
[`CLAUDE.md`](CLAUDE.md) for the working-on-the-code guide.

## Status & caveats

- **Distribution:** v1 ships **from git source only** (no PyPI yet). `uv sync`
  from a clone, or `uvx --from ./src/tinyic tinyic --help` for a one-shot.
- **Cost & time:** a full six-member, four-phase debate is ~30–45 model calls —
  budget **minutes, not seconds**, and **cents to low single-dollars** on a
  mid-tier reasoning model. Every run reports real per-call, per-persona token
  usage and cost (`cost_usd` is `null` on subscription lanes, which emit a
  usage-window meter instead). Fewer personas, `--no-research`, a cheaper
  `--model`, or a lower `--thinking` level all cut both.
- **Subscription rate windows** can be small relative to a 6×4 debate; the
  usage meter and automatic API-key overflow are there to help.
- **Scope:** local, single-user tool. No web app, no trading/brokerage, no
  user-created personas — the six investors are fixed (see
  [`docs/PRD.md`](docs/PRD.md)).

## License & acknowledgements

[MIT](LICENSE). Built on [Microsoft TinyTroupe](https://github.com/microsoft/TinyTroupe)
(forked, vendored) for multi-agent persona simulation, and informed by the
public writings of Buffett, Munger, Graham, Lynch, Marks, and Li Lu.
