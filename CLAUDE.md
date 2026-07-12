# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TineyIC — an AI investment committee: 6 investor personas (Buffett, Munger, Graham, Lynch, Marks, Li Lu) debate a stock over 4 phases and produce a scorecard, investment memo, and disagreement analysis. Built on a **forked Microsoft TinyTroupe v0.6.0** vendored at `src/tinytroupe/`.

**A v2 revamp (Streamlit → Textual TUI + headless CLI) is specified and approved**: see `docs/PRD.md` (requirements, milestones M0–M6), `docs/event-schema.md` (the engine↔renderer contract), `docs/code-review-2026-07-12.md` (33 defects, all in scope), and `docs/CODEX_KICKOFF.md` (implementer bootstrap). Everything below describes the **pre-revamp** code, which remains accurate until the milestones land.

## Commands

```bash
uv sync                                        # install both workspace packages (uv.lock is committed)
uv run streamlit run src/tinyic/ui/app.py      # run the app
uv run pytest tests/                           # offline test suite (no API key needed)
uv run pytest tests/test_debate.py -k name     # single test
uv run pytest tests/ -m live_api               # live-API tests (needs OPENAI_API_KEY in .env)
uv run python scripts/smoke_test.py            # live smoke test (persona -> LLM round trip)
```

- Secrets go in `.env` (loaded via python-dotenv): `OPENAI_API_KEY` required; `XAI_API_KEY` optional (X/Twitter sentiment via xAI Grok — without it that source is skipped with a warning).
- pytest has a global 120s timeout per test (`pyproject.toml`).

## Architecture

Two-package uv workspace (root `pyproject.toml` lists members `src/tinyic` and `src/tinytroupe`):

- **`src/tinytroupe/`** — forked framework: `TinyPerson` (LLM-backed agent with episodic/semantic memory, act loop emitting typed actions: THINK/TALK/DONE plus a `cognitive_state`), `TinyWorld` (environment run loop), `control.py` (simulation transactions/caching), `clients/` (OpenAI/Azure/Ollama; `client()` singleton), `extraction/` (LLM-based results extraction from agent memory). Treat as vendored: change it deliberately, not casually.
- **`src/tinyic/`** — the app:
  - `personas/`: `registry.py` maps snake_case names → `configs/*.agent.json`; `InvestorPersona(TinyPerson)` merges the JSON's `persona` block into the live agent via `include_persona_definitions`. Display names ("Warren Buffett") are derived from registry keys and must stay in sync with `PHILOSOPHY_HOOKS` keys in `debate/prompts.py` (fallback is a generic hook, no error).
  - `data/`: `pipeline.build_data_package(ticker)` fans out to independent sources — yfinance fundamentals, SEC EDGAR 10-K/10-Q (edgartools), yfinance news, xAI social sentiment, optional "deep research" (OpenAI Responses web search + LLM synthesis). Each source fails independently and appends to `DataPackage.warnings` (graceful degradation). `DataPackage.to_context_string()` caps context at ~20K chars.
  - `debate/`: `DebateOrchestrator(TinyWorld)` **replaces** `_step()` entirely — each step = one phase (OPENING → CROSS_EXAM → REBUTTAL → VERDICT). Per phase: broadcast phase goal, then agents act sequentially; before each agent acts, a persona-specific anti-convergence reinforcement is injected; in CROSS_EXAM a rotating devil's advocate gets an extra prompt and is released at REBUTTAL. Hooks: `on_phase_start` / `on_agent_start` / `on_agent_done` callbacks, `message_queue` (user steering, drained between agent turns), `phase_gate` (threading.Event pausing between phases). `extraction.py` runs an LLM extraction pass over each agent's memory → `Vote` (Pydantic, fuzzy vote-string validator) → `Scorecard`. `memo.py` makes 2 more LLM calls (memo + disagreements) over a transcript truncated to 8K chars.
  - `ui/app.py`: Streamlit. Debate runs in a **background daemon thread** (`_debate_worker`) that must never touch `st.session_state`; it communicates via a `queue.Queue` of typed events (`status`, `phase`, `agent_start`, `message`, `data_ready`, `phase_complete`, `complete`, `partial_complete`, `error`) drained on the main thread by `_drain_queue()` inside an `st.fragment(run_every=2)` poller. Only TALK action content is displayed; THINK actions are dropped.

## Critical gotchas

- **Process-global registries**: `TinyPerson.all_agents` and `TinyWorld.all_environments` are class-level dicts; creating a duplicate name **raises ValueError**. Tests clear them in fixtures (see `tests/test_debate.py`, `tests/test_personas.py`). Any code path that re-creates personas/worlds in one process (Streamlit reruns, notebooks) must clear these or use unique names — the current UI does not, so a second debate in the same process errors.
- **Global config/model state**: `config_manager` and `client()` are process-wide singletons. The UI's per-debate model switch mutates global config (restored in a `finally`), which is unsafe for concurrent debates.
- **Config resolution is cwd-dependent**: `tinytroupe/utils/config.py` reads `src/tinytroupe/config.ini` first, then overlays `Path.cwd()/config.ini`. Running from a different directory silently changes model/endpoint settings. Note the root `config.ini` sets a non-default OpenAI `BASE_URL`; `data/research.py` bypasses it (bare `OpenAI()` client), so the two paths need different key types.
- Offline tests mock LLM/network calls; keep it that way — anything hitting the network belongs under `-m live_api`.
