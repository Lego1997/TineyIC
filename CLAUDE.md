# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TinyIC — an AI investment committee: 6 investor personas (Buffett, Munger, Graham, Lynch, Marks, Li Lu) debate a stock over 4 phases (opening → cross-exam → rebuttal → verdict) and produce a scorecard, investment memo, and disagreement analysis. Built on a **forked Microsoft TinyTroupe 0.7.0** vendored at `src/tinytroupe/`.

**The v2 revamp (Streamlit → Textual TUI + headless CLI, model-agnostic backend) has landed** (milestones M0–M6). The source of truth for behavior is `docs/PRD.md` (requirements/milestones), `docs/event-schema.md` (the engine↔renderer contract), and `AGENTS.md` (the headless agent contract). `docs/code-review-2026-07-12.md` (33 defects) and `docs/CODEX_KICKOFF.md` are historical records — keep them verbatim.

## Commands

```bash
uv sync                                        # install both workspace packages (uv.lock is committed)
tinyic debate AAPL                             # convene the committee in the Town Hall TUI
tinyic debate AAPL --headless --json           # headless: stream the JSONL event log to STDOUT (agent mode)
tinyic onboard                                 # interactive auth wizard (detect → pick lane → verify → persist)
tinyic doctor [--json] [--live]                # auth/provider probe; exit 0 = ready, 3 = setup needed
tinyic replay|export|result|runs <id|path>     # consume a recorded debate (zero LLM calls)
uv run pytest tests/                           # offline test suite (no API key needed; network/LLM mocked)
uv run pytest tests/test_debate.py -k name     # single test
uv run pytest tests/ -m live_api               # live-API tests (need real credentials/runtimes)
tinyic doctor --live                           # binding-routed one-token live auth check (replaces the old smoke script)
```

- Credentials: `tinyic onboard` persists verified profiles to the OS keyring; a `.env` (`OPENAI_API_KEY`, optional `XAI_API_KEY` for X sentiment) is also honored via python-dotenv.
- pytest has a global 120s timeout per test (`pyproject.toml`); `live_api`-marked tests are deselected by default.

## Architecture

Two-package uv workspace (root `pyproject.toml` lists members `src/tinyic` and `src/tinytroupe`). **One engine, three faces, joined by one event stream.**

- **The event stream is the only engine→renderer channel.** The debate engine emits an append-only **JSONL event log** (`tinyic/events.py`, schema v1 in `docs/event-schema.md`); the TUI, the headless `--json` streamer, the HTML/MD exporter, and replay all *consume that log*, never engine internals. Replay = re-feeding a recorded log to any renderer. This contract is enforced by tests.
- **`src/tinytroupe/`** — forked framework: `TinyPerson` (LLM-backed agent, act loop emitting typed THINK/TALK/DONE actions + a `cognitive_state`), `TinyWorld` (run loop), `Session` (registry scoping, M0), `clients/`, `extraction/`. Treat as vendored: change it deliberately (see vendored discipline below).
- **`src/tinyic/`** — the app:
  - `cli.py` — argparse entry point; subcommands `debate · runs · result · export · replay · doctor · onboard`. Heavy imports are lazy so `--help`/`doctor`/`replay` stay import-light.
  - `headless.py` — the M6 CLI↔engine seam: runs the debate in a worker thread that appends to the event log, then *watches that file* (via `tui/live.py` follower). Interactive attaches the TUI; `--headless`/`--json` streams the log to STDOUT; `--steer-stdin` feeds the engine steering inbox. The whole engine run executes under `redirect_stdout` so STDOUT stays a clean machine channel.
  - `events.py` / `result.py` / `report.py` — the schema-v1 `EventLog` + envelope; result-document assembly (compat promise #3); HTML/MD renderers.
  - `personas/` — `registry.py` maps snake_case names → `configs/*.agent.json`; `InvestorPersona(TinyPerson)` merges the JSON `persona` block.
  - `data/` — `pipeline.build_data_package(ticker)` fans out to independent sources (yfinance, EDGAR, news, xAI sentiment, optional web research); each fails independently → `warnings` surfaced as `data_ready` source statuses.
  - `debate/` — `run_debate()` drives the protocol; `DebateOrchestrator(TinyWorld)`, `Moderator` (phase gating, caps, DA rotation, steering delivery), `steering.py` (engine-backed steer/queue/interrupt sinks), `extraction.py`/`memo.py` (votes + mixture-of-agents memo over the full transcript), `analytics.py` (collapse metrics).
  - `models/` — the model-agnostic layer: `ModelBinding`, adapters per wire format (`openai_chat`, `openai_responses`, `anthropic_messages`, `openai_compatible`) + runtimes (`codex_runtime`, `claude_runtime`), the `thinking` ladder, `presets.py` (`tinyic.toml`), usage capture.
  - `auth/` — auth profiles, keyring, OpenAI/Anthropic subscription lanes, `doctor.py` (reason-coded probe schema v1) + `live_probe.py`.
  - `tui/` — the Textual Town Hall (`app.py`), the FR-2.4 onboarding wizard (`onboard.py`), and the live log follower (`live.py`).

Threading: the debate loop runs in one worker thread; renderers consume a `queue.Queue`/log follower on the main thread; steering flows through a command queue drained at turn/phase boundaries; stop/interrupt is checked between model calls. No detached daemon threads.

## Critical gotchas

- **STDOUT is a machine channel in `--json` mode.** STDOUT must carry *only* event JSONL; all progress/diagnostics go to STDERR. The vendored TinyTroupe import prints an AI disclaimer + config dump to stdout, and its console log handler was moved to STDERR (`tinytroupe/utils/config.py`, M6); headless additionally runs under `redirect_stdout`. Never `print()` to real stdout on the headless path, and never let secrets reach any log/event/export.
- **Session scoping (M0 fix).** `TinyPerson`/`TinyWorld` registries are now `Session`-scoped, so each debate runs in its own `Session` and two back-to-back debates in one process no longer collide (the old duplicate-name `ValueError` is gone). Don't reintroduce process-global name collisions; construct agents/worlds within a Session.
- **Schema is frozen (additive-only).** Within schema v1, `seq`/`ts`/`type` never change meaning; only add new event types / optional payload fields. Renderers must ignore unknown types and fields. A log is replayable iff it starts with `debate_started` and ends with `debate_completed`/`debate_error`; handle truncated logs.
- **Model/auth resolution.** Bindings resolve per-persona through `models/`; auth profiles live in the keyring; the Anthropic subscription lane is gated by `policy_guard` in `tinyic.toml` and is additive (API-key overflow), never load-bearing. `tinyic doctor` reason codes are a stable, secret-free contract.
- **Config resolution is cwd-dependent.** `tinytroupe/utils/config.py` reads `src/tinytroupe/config.ini` then overlays `Path.cwd()/config.ini`. The committed root `config.ini` no longer sets a proxy `BASE_URL` (D3) and uses a JSON API cache — packaging tests enforce both.
- **Vendored discipline.** `src/tinytroupe/` is a pinned 0.7.0 fork. Any change to a vendored file must be added to `VENDORED_DIVERGENCES` in `tests/test_m0_packaging.py`, noted in `src/tinytroupe/FORK.md`, and the pinned upstream manifest regenerated (procedure in that test). Otherwise the manifest guard fails.
- **Test discipline.** Offline tests mock all LLM/network calls — keep it that way; anything hitting the network belongs under `-m live_api`. The full offline suite must stay green; `uv.lock` is committed.
