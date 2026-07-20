# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TinyIC — an AI investment committee: the six built-in investor personas (Buffett, Munger, Graham, Lynch, Marks, Li Lu), or a layered custom committee, debate a stock over 4 phases (opening → cross-exam → rebuttal → verdict) and produce a scorecard, investment memo, and disagreement analysis. Built on a **forked Microsoft TinyTroupe 0.7.0** vendored at `src/tinytroupe/`.

**The v2 revamp, v2.1 provider matrix, v2.2 amendment (PRD §16), and v2.3 Persona Studio (PRD §17) have all landed**: the Textual debate face is replaced by the secured local Web Town Hall, replay uses that same read-only page, the cited Persona Factory adds layered user personas, and `tinyic studio` serves a persistent secured persona hub (library · research wizard · editor · default-committee picker). Providers remain `openai · anthropic · grok · google · kimi · ollama`; the Textual onboarding wizard remains. The source of truth for behavior is `docs/PRD.md` (requirements/amendments), `docs/event-schema.md` (the engine↔renderer contract), and `AGENTS.md` (the headless agent contract). The historical code review and research briefs stay verbatim — they are the PRD's cited evidence base.

## Commands

```bash
uv sync                                        # install both workspace packages (uv.lock is committed)
uv sync --extra cn                             # optional: China A-share/HK data source (akshare; heavy, opt-in)
tinyic debate AAPL                             # convene the committee in the browser Town Hall
tinyic debate AAPL --port 8765 --no-open       # explicit loopback port; print URL without launching
tinyic debate AAPL --no-wait                   # exit after run + viewer SSE drain
tinyic debate AAPL --headless --json           # headless: stream the JSONL event log to STDOUT (agent mode)
tinyic onboard                                 # interactive auth wizard (detect → pick lane → verify → persist)
tinyic doctor [--json] [--live]                # auth/provider probe; exit 0 = ready, 3 = setup needed
tinyic models [provider] [--refresh] [--json]  # per-provider model catalog (static; --refresh = live listings)
tinyic replay <id|path> [--no-open] [--no-wait] # read-only browser replay, zero LLM calls
tinyic export|result|runs ...                  # consume a recorded debate, zero LLM calls
tinyic persona research NAME [--slug S] [--yes] # cited dual-artifact persona factory
tinyic persona list|show ... [--json]          # inspect layered built-in/user registry
uv run pytest tests/                           # offline test suite (no API key needed; network/LLM mocked)
uv run pytest tests/test_debate.py -k name     # single test
uv run pytest tests/ -m live_api               # live-API tests (need real credentials/runtimes)
tinyic doctor --live                           # binding-routed one-token live auth check (replaces the old smoke script)
```

- Credentials: `tinyic onboard` persists verified profiles to the OS keyring; a `.env` is also honored via python-dotenv (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY` for Grok + X sentiment, `MOONSHOT_API_KEY`/`KIMI_API_KEY` for Kimi).
- pytest has a global 120s timeout per test (`pyproject.toml`); `live_api`-marked tests are deselected by default.

## Architecture

Two-package uv workspace (root `pyproject.toml` lists members `src/tinyic` and `src/tinytroupe`). **One engine, three faces, joined by one event stream.**

- **The event stream is the only engine→renderer channel.** The debate engine emits an append-only **JSONL event log** (`tinyic/events.py`, schema v1 in `docs/event-schema.md`); the web Town Hall, the headless `--json` streamer, the HTML/MD exporter, and replay all *consume that log*, never engine internals. Replay = re-feeding a recorded log to any renderer. This contract is enforced by tests.
- **`src/tinytroupe/`** — forked framework: `TinyPerson` (LLM-backed agent, act loop emitting typed THINK/TALK/DONE actions + a `cognitive_state`), `TinyWorld` (run loop), `Session` (registry scoping, M0), `clients/`, `extraction/`. Treat as vendored: change it deliberately (see vendored discipline below).
- **`src/tinyic/`** — the app:
  - `cli.py` / `persona_cli.py` — argparse entry point and persona command service; subcommands `debate · runs · result · export · replay · models · persona · doctor · onboard`. Heavy imports are lazy so every help path stays import-light.
  - `headless.py` — the CLI↔engine seam: runs the debate in a worker thread that appends to the event log. The default path attaches `web.WebFace`; `--headless`/`--json` streams the log to STDOUT; `--steer-stdin` feeds the engine steering inbox. The whole engine run executes under `redirect_stdout` so STDOUT stays a clean machine channel.
  - `events.py` / `result.py` / `report.py` — the schema-v1 `EventLog` + envelope; result-document assembly (compat promise #3); HTML/MD renderers.
  - `personas/` — `registry.py` layers protected built-ins over valid `~/.tinyic/personas/*.agent.json` files; `InvestorPersona(TinyPerson)` reads the JSON persona plus `tinyic` metadata. `factory/` plans cited research, builds/verifies the dossier and persona, enforces source/schema gates, and installs a staged pair with guarded per-file replacement.
  - `data/` — `pipeline.build_data_package(ticker)` fans out to independent sources (yfinance, EDGAR, news, xAI sentiment, optional web research); each fails independently → `warnings` surfaced as `data_ready` source statuses.
  - `debate/` — `run_debate()` drives the protocol; `DebateOrchestrator(TinyWorld)`, `Moderator` (phase gating, caps, DA rotation, steering delivery), `steering.py` (thread-safe steer/queue/interrupt inbox), `control.py` (pause/resume/next/stop), `extraction.py`/`memo.py` (votes + mixture-of-agents memo over the full transcript), `analytics.py` (collapse metrics).
  - `models/` — the model-agnostic layer: `ModelBinding`, ordinary adapters/runtimes, the `thinking` ladder, presets/user overlay, catalog, and usage capture. `models/research/` is the sibling citation-preserving search surface for OpenAI, Grok, Gemini, and Kimi; it shares the credential and injectable HTTP seams but not the debate chat stream.
  - `auth/` — auth profiles, keyring, OpenAI/Anthropic/Grok subscription lanes (`grok.py`: grok-CLI read-through + RFC 8628 device code), `doctor.py` (reason-coded probe schema v1) + `live_probe.py`.
  - `web/` — the stdlib `127.0.0.1` server, capability-token/cookie policy, literal Host/Origin validation, JSON-only writes, SSE/JSON endpoints, existing report downloads, and zero-build browser Town Hall assets.
  - `tui/` — the FR-2.4 Textual onboarding wizard plus framework-free event/state helpers retained by reports; it is no longer a debate renderer. The renderer-free live follower is `live.py` at package scope.

Threading: the debate loop runs in one worker thread; the loopback server uses daemon request threads and tails the event log per SSE connection; steering flows through a command queue drained at turn/phase boundaries; stop/interrupt is checked between model calls. Server shutdown drains active connections for one second.

## Critical gotchas

- **STDOUT is a machine channel in `--json` mode.** STDOUT must carry *only* event JSONL; all progress/diagnostics go to STDERR. The vendored TinyTroupe import prints an AI disclaimer + config dump to stdout, and its console log handler was moved to STDERR (`tinytroupe/utils/config.py`, M6); headless additionally runs under `redirect_stdout`. Never `print()` to real stdout on the headless path, and never let secrets reach any log/event/export.
- **Session scoping (M0 fix).** `TinyPerson`/`TinyWorld` registries are now `Session`-scoped, so each debate runs in its own `Session` and two back-to-back debates in one process no longer collide (the old duplicate-name `ValueError` is gone). Don't reintroduce process-global name collisions; construct agents/worlds within a Session.
- **Schema v1 is frozen and v2.2 made no schema change.** `seq`/`ts`/`type` never change meaning; renderers must ignore unknown types and fields. A log is replayable iff it starts with `debate_started` and ends with `debate_completed`/`debate_error`; handle truncated logs. Do not use the Web Town Hall or Persona Factory as a reason to add events.
- **Web security is a four-part boundary.** Keep exact loopback binding, per-process capability token→`HttpOnly; SameSite=Strict` cookie/Bearer auth, literal Host+Origin allowlists, and authenticated JSON-only POSTs. Every response keeps no-store/CSP/nosniff/no-referrer headers; never restore stdlib request logging because the first URL contains the token. Browser rendering must remain escape-first, with no raw model text assigned to `innerHTML`.
- **Model/auth resolution.** Bindings resolve per-persona through `models/`; auth profiles live in the keyring; the Anthropic and Grok subscription lanes are each gated by a `policy_guard` in `tinyic.toml` (`[auth.anthropic]` / `[auth.grok]`) and are additive (API-key overflow), never load-bearing. `tinyic doctor` reason codes are a stable, secret-free contract.
- **The Grok read-through never writes `~/.grok/auth.json` — and never redeems the CLI's refresh token.** Redemption is a server-side rotation event that can invalidate the CLI's own copy and log the user out (the FR-2.2 hazard; same discipline as the Codex lane's `refreshToken: false`). A stale file sign-in is reported as `expired` — the user refreshes it by running the grok CLI, or uses the TinyIC-owned device-code lane (whose rotated tokens ARE persisted back to the keyring profile). xAI enforces entitlement server-side: the 403 is the stable reason code `subscription_inactive`, which advances auth rotation.
- **`xai/` model refs no longer resolve.** The provider was hard-renamed to `grok` (no alias); the env var is still `XAI_API_KEY`. DeepSeek is gone entirely.
- **Kimi `$web_search` needs thinking off.** The ordinary opt-in builtin (`params.web_search = true`) rejects any thinking level other than `off`; the research backend forces `off`. Persona research budgets Kimi by total echo HTTP rounds across the whole run (default 16), not by logical queries, and every sent round consumes that counter even if the call fails.
- **Persona research model eligibility is narrower than the debate catalog.** Budget-capable configured bindings win; ineligible bindings are skipped before credential/provider access. Google persona research uses `google/gemini-2.5-flash`, counting and pricing each grounded prompt as one billable unit regardless of internal queries (worst-case Search fee: $0.035). Gemini 3 remains valid for ordinary debates but is rejected for persona research because it cannot impose a per-prompt query ceiling. Grok pairs `max_turns` with serialized tool calls and accounts from `server_side_tool_usage` when present.
- **Persona writes are collision- and evidence-gated.** Built-in slugs are immutable even under `--force`; existing user artifacts are checked before backend setup. Fewer than three independent domains writes nothing, verified claims/quotes only survive, schema validation precedes two staged per-file atomic replacements with rollback on detected failure, and generated artifacts must never contain credentials or personal-life material.
- **Committee precedence is explicit.** `--personas` wins over top-level user-overlay `committee`, which wins over the built-in six. Every committee is 2–6 unique, resolvable layered-registry slugs. Tests must sandbox both `TINYIC_USER_CONFIG` and `TINYIC_PERSONAS_DIR`.
- **Preset precedence: user overlay wins.** `presets.load_config` reads the base config (explicit path / `$TINYIC_CONFIG` / `./tinyic.toml` / built-in), then deep-merges the user overlay (`$TINYIC_USER_CONFIG` else `~/.tinyic/tinyic.toml`) over it, key-by-key. The wizard's model step writes only the overlay; tests pin `TINYIC_USER_CONFIG` into the sandbox so the suite never touches a real one.
- **Config resolution is cwd-dependent.** `tinytroupe/utils/config.py` reads `src/tinytroupe/config.ini` then overlays `Path.cwd()/config.ini`. The committed root `config.ini` no longer sets a proxy `BASE_URL` (D3) and uses a JSON API cache — packaging tests enforce both.
- **Vendored discipline.** `src/tinytroupe/` is a pinned 0.7.0 fork. Any change to a vendored file must be added to `VENDORED_DIVERGENCES` in `tests/test_m0_packaging.py`, noted in `src/tinytroupe/FORK.md`, and the pinned upstream manifest regenerated (procedure in that test). Otherwise the manifest guard fails.
- **Test discipline.** Offline tests mock all LLM/network calls — keep it that way; anything hitting the network belongs under `-m live_api`. The full offline suite must stay green; `uv.lock` is committed.
