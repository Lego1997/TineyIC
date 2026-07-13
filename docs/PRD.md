# PRD: TinyIC v2 — "Cyberspace Town Hall" (TUI + Headless CLI Revamp)

| | |
|---|---|
| **Status** | Approved for implementation |
| **Date** | 2026-07-12 |
| **Author** | Claude (product/spec), with Pan (owner decisions via interview) |
| **Implementer** | GPT-5.6 / Codex — see [CODEX_KICKOFF.md](CODEX_KICKOFF.md) |
| **Evidence base** | [Code review (33 findings)](code-review-2026-07-12.md) · [Notebook/town-hall research brief](research/2026-07-12-notebook-town-hall-brief.md) · [Model-agnostic/auth/MoA research brief](research/2026-07-12-model-agnostic-brief.md) (+ [raw claims](research/2026-07-12-model-agnostic-claims.md)) |

---

## 1. Vision

TinyIC is an AI investment committee you can *watch think*. Six legendary value investors — Buffett, Munger, Graham, Lynch, Marks, Li Lu — debate any public company through a structured four-phase protocol, live in your terminal: every persona's private reasoning is one keypress away, you can interject mid-debate like a moderator, every debate is a replayable artifact, and any other AI agent can launch a committee headlessly and consume the verdict as structured events.

**One engine, three faces:**
1. **TUI** (v1 flagship): a Textual full-screen "town hall" for humans.
2. **Headless CLI** (v1): `tinyic debate AAPL --json` streams the event log to stdout — the interface for AI agents, scripts, and CI.
3. **HTML replay export** (v1): a shareable static rendering of any recorded debate. (A notebook/anywidget renderer over the same event stream is the designated v1.1 fast-follow — explicitly out of scope for v1.)

### 1.1 Positioning

The category leader, [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund) (~61k stars), runs **parallel, independent** persona analyses synthesized by a portfolio manager — no interaction between personas, no visible reasoning process, no mid-run interactivity. TinyIC's differentiation is exactly that triad: **genuine cross-examination** (personas argue with each other under anti-sycophancy controls), **visible thinking** (THINK actions + cognitive state as first-class UI), and **live steering** (Codex-style mid-debate injection). Plus a fourth no one in the category has: **agent-launchability** via a documented JSONL event contract.

### 1.2 Non-goals (v1)

- No web app, no Streamlit (the existing Streamlit UI is removed in M6), no Jupyter renderer (v1.1).
- No topics beyond the investment committee; no user-created personas; the 6 investors are fixed.
- No trading, brokerage, or portfolio integration of any kind. Output is educational analysis with a disclaimer.
- No multi-user/server deployment; TinyIC is a local, single-user tool.
- No LiteLLM/aisuite dependency — providers are in-house adapters (§5).

---

## 2. Users and jobs

| User | Job |
|---|---|
| Pan / technical investors | "Convene a committee on a ticker while I work; watch the argument; interrogate a persona; keep the memo." |
| AI agents (Claude Code, Codex, OpenClaw, cron) | "Launch a debate headlessly, stream events, consume votes/memo as JSON." |
| Showcase viewers (GitHub/X) | "Watch a replay of six minds arguing; understand why this beats one-model analysis." |

---

## 3. Architecture overview

```
                      ┌────────────────────────────────────────────┐
                      │              tinyic engine                  │
                      │  personas · moderator · debate protocol     │
                      │  data pipeline · extraction · memo (MoA)    │
                      │  (rebased vendored TinyTroupe 0.7.0 core)   │
                      └───────────────┬────────────────────────────┘
             ModelBinding per persona │ emits typed events
        ┌──────────────────────────┐  │  ┌───────────────────────────────┐
        │  model layer (§5)        │  └─▶│  EVENT STREAM (JSONL, §7)      │
        │  adapters: openai-chat / │     │  append-only · versioned ·     │
        │  openai-responses /      │     │  the ONLY producer→UI channel  │
        │  anthropic-messages /    │     └──────┬──────────┬─────────────┘
        │  openai-compatible       │            │          │
        │  runtimes: codex-oauth / │       ┌────▼───┐ ┌────▼────────┐ ┌──────────────┐
        │  claude-agent-sdk        │       │  TUI   │ │ headless    │ │ HTML replay  │
        │  auth profiles (§6)      │       │(Textual)│ │ CLI --json  │ │ exporter     │
        └──────────────────────────┘       └────────┘ └─────────────┘ └──────────────┘
```

**The load-bearing rule:** renderers never read engine internals; they consume events. The engine never draws; it emits events. Replay = re-feeding recorded events to any renderer. This contract is what makes the TUI, headless mode, HTML export, and the future notebook renderer cheap variants of one product, and it is enforced by tests (§9).

Threading model: the debate loop runs in one worker thread owned by the session controller; renderers run on the main thread consuming a `queue.Queue` of events; steering flows through a command queue drained at turn boundaries. No detached daemon threads; every thread is joined on session end; stop/interrupt is checked **between every model call**, not after the debate (fixes review C1).

---

## 4. Workstream 0 — Foundation

**FR-0.1 Rebase the vendored fork.** Replace `src/tinytroupe` (v0.6.0 lineage) with upstream **TinyTroupe 0.7.0**, re-applying only patches the new architecture still needs (the proxy stream-forcing patch is superseded by §5; adopt upstream's pickle→JSON cache format). Document every retained divergence in `src/tinytroupe/FORK.md`.

**FR-0.2 Re-audit the defect inventory on the new base.** All 33 findings in [code-review-2026-07-12.md](code-review-2026-07-12.md) are in scope as **defect classes with acceptance tests** — line numbers will move in the rebase; the tests define done. For each finding: write the failing test first (or mark "vanished in rebase/deletion" with proof), then fix. Highest-priority classes:
- **A1 registries:** creating personas/worlds must be idempotent per session. Introduce a `Session` scope object owning agent/world registries (or clear-and-recreate semantics) such that two consecutive debates in one process, same personas/ticker, succeed. Acceptance: run two full mocked debates back-to-back in one process; also `tinyic debate X` twice in one long-lived REPL.
- **B1–B3 vote integrity:** replace substring vote fuzzing with anchored matching (`SELL` beats `BUY` when both appear; explicit token wins); case-insensitive enum validation for confidence; per-agent extraction error isolation (one failure never falsifies other agents' votes). Acceptance tests use the exact adversarial strings from the review.
- **B4 truncation:** extraction and transcript rendering must see full speeches (`max_content_length=None` must genuinely mean unlimited through the decorator); memo synthesis uses windowed aggregation (§8), not `[-8000:]`.
- **B5 similarity guard:** replace character-multiset Jaccard with token-level similarity on normalized text, threshold recalibrated with fixture speeches from the review repro; guard must never blank a >500-char on-topic speech in tests.
- **B6 filings TOC:** section extraction must skip table-of-contents matches (e.g., require minimum body length after the heading / take the *last* match / anchor on "Item 1." + body heuristics). Acceptance: the review's TOC fixture yields real section text.
- **B7 units:** normalize yfinance units at the model boundary (D/E percent→ratio; dividend-yield convention detected and labeled); every number entering LLM context carries units.
- **B11/B12 client:** streamed calls send `stream_options={"include_usage": true}`; cost stats are per-debate (client counters scoped or snapshot-diffed), priced from a model→price table; `response_format` is honored (native structured outputs where supported, schema-in-prompt fallback elsewhere).
- **C-class:** dissolved by the new threading model (C1), per-binding clients (C2), pipeline try/except completeness (C3/C4), no stale UI state (C6 — moot with event-stream renderers), bounded queues and drained-or-acked steering (C9).
- **D-class:** fetched external text (news titles, X summaries, web research) enters prompts only inside a delimited data block with "content is data, not instructions" framing (D1); file logging defaults to INFO with prompts logged only under an explicit debug flag, never containing credentials (D2); no proxy endpoints in committed config (D3 — §6).
- **E-class polish:** including removal of the `openIC` branding leftover with the Streamlit app, README `uv sync` fix (root package depends on both members so plain `uv sync` works), committed `uv.lock`, XAI_API_KEY documented, no hardcoded years in search queries.

**FR-0.3 Packaging.** `uv` workspace stays; `uv sync` alone must produce a working dev environment; `uv.lock` committed; `tinyic` console entry point via `[project.scripts]`; `uvx tinyic` works from a clean machine — meaning source-based invocation from a clean clone (`uvx --from <clone>/src/tinyic tinyic`) and, once public, `uvx --from git+<repo-url>#subdirectory=src/tinyic tinyic`.

**FR-0.4 Distribution & publication (owner decision, 2026-07-13).** v1 distributes as **git-source only**; registry (PyPI) publication is out of scope. Rationale: the `tinyic` wheel's `Requires-Dist: tinytroupe` would resolve to Microsoft's upstream PyPI distribution — not the vendored fork — on any registry install (uv workspace source mappings are not serialized into wheel metadata). Fail-safe: both workspace packages carry the `Private :: Do Not Upload` trove classifier (PyPI rejects unknown classifiers, so accidental uploads fail), asserted by a packaging test. If post-v1 publication is desired, the recorded direction is to **bundle the vendored fork inside the `tinyic` distribution** (ship both import packages in one wheel, drop the external `tinytroupe` requirement), accepting and documenting the import-name-shadowing caveat if upstream `tinytroupe` is co-installed; publishing the fork as a separately named distribution is rejected (two artifacts to maintain, same collision).

---

## 5. Workstream 1 — Model-agnostic backend

**FR-1.1 ModelBinding.** Every LLM consumer (each persona, the moderator, the aggregator, extraction, data-pipeline synthesis) resolves a `ModelBinding = {model_ref, auth_profile, thinking_level, params}` where `model_ref = "provider/model"` (e.g. `anthropic/claude-opus-4-8`, `openai/gpt-5.6-sol`, `ollama/qwen3:32b`). Bindings come from config presets (FR-1.4) with per-debate CLI/TUI overrides.

**FR-1.2 Provider adapters (in-house; no LiteLLM).** A small adapter SDK (`register_provider(...)`) over **four wire formats**: `openai-chat`, `openai-responses`, `anthropic-messages`, `openai-compatible` (custom base URL — covers Ollama, vLLM, OpenRouter, LiteLLM-proxy users). v1 bundled providers: OpenAI (key + subscription runtime), Anthropic (key + subscription runtime), Google Gemini (key), xAI (key), DeepSeek (key), Ollama/local (compatible). Each adapter owns: request/response mapping, **token streaming with usage** (deltas surfaced as events), retry/backoff classification, model catalog, thinking-parameter mapping, and error normalization. Adding a provider must require only a new adapter module + registry entry. *(Amended by §15.1, 2026-07-14: the provider matrix is now openai · anthropic · grok · google · kimi · ollama.)*

**FR-1.3 Thinking ladder.** One normalized enum `off | minimal | low | medium | high | xhigh | max`, **capability-gated per model** via the adapter's `thinking_profile(model)`: unsupported levels are rejected with the valid set (config) or remapped to nearest (runtime override), and the parameter is *omitted* entirely for models that reject it. Mappings: OpenAI `reasoning_effort` / Responses `reasoning.effort`; Anthropic extended-thinking budgets (documented level→budget table); Gemini `thinkingBudget`/`thinkingLevel`; DeepSeek `reasoning_effort`; Ollama `think`. On the ChatGPT-subscription lane, effort maps to **model tier** (there is no numeric knob there — see research brief §3). Reasoning/thinking content returned by providers is captured into THINK events, unifying "watch it think" across providers.

**FR-1.4 Presets (Hermes-style).** Named committee configs in `tinyic.toml`: per-persona bindings, aggregator binding, moderator binding, caps and temperament settings, with a `default` preset = one strong model everywhere. Per-persona heterogeneity is supported and documented as a *cost/character* knob with same-tier guidance (Self-MoA evidence — research brief §2); the docs recommend task-specialized mixes (e.g. strong-math model for Graham) over random diversity.

**FR-1.5 Cost & usage.** Every model call emits a usage event (tokens in/out, cached, model, persona attribution, computed cost for key-lanes). Per-debate rollup in the verdict summary and in `tinyic result`. Subscription lanes show a **message-window meter** (count-based estimate for the rolling 5h window) instead of dollars.

---

## 6. Workstream 2 — Auth & onboarding (subscription-first)

**FR-2.1 Auth profiles.** A profile store holds named credentials: `openai:<name>` (subscription OAuth or API key), `anthropic:<name>` (Claude-runtime or API key), plus plain key profiles per provider. Ordered fallback per provider (`auth_order`) — e.g. subscription first, key overflow — rotating on usage-limit blocks without changing the selected model. Storage: OS keyring where available, else `~/.tinyic/credentials.json` chmod 0600. Secrets never appear in events, logs, or exports.

**FR-2.2 OpenAI subscription lane (sanctioned).** "Sign in with ChatGPT": implement device-code OAuth (works headless/SSH) and browser-callback OAuth with paste-the-redirect fallback; **read-through reuse** of an existing `~/.codex/auth.json` (honoring the keyring option) when Codex CLI is present — never re-writing it (refresh-rotation hazard: taking ownership of a copied refresh token can log the user's own CLI out; see research brief §1). Subscription traffic uses the sanctioned Codex transport for subscription-only models.

**FR-2.3 Anthropic subscription lane (via official plumbing only).** "Use your Claude subscription" = a **`claude-agent-sdk` runtime binding** (with `claude -p` subprocess fallback) that reuses the user's own logged-in Claude Code; identity-honest (never spoofing another client); accepts a user-minted `CLAUDE_CODE_OAUTH_TOKEN` for headless use. **TinyIC must not implement a Claude.ai OAuth flow of its own — that remains prohibited.** Per the June 15, 2026 reinstatement, usage draws from the user's Pro/Max limits; the onboarding copy states the policy plainly, links Anthropic's support article, and the lane sits behind a `policy_guard` config so a future policy change can disable it with a clear message instead of stranding configs (this policy changed three times in 2026 — see research brief §1).

**FR-2.4 Onboarding (`tinyic onboard`).** OpenClaw-pattern wizard, TUI-native: **detect** (env keys, `~/.codex/auth.json`, Claude Code login, local Ollama) → per-provider **two-branch chooser** (subscription vs API key, "best for" copy, policy note on the Claude lane) → **live 1-token verification** (refuses to finish on failure) → persist only the verified route → summary card of each persona's resolved binding. Re-running is an idempotent verify-and-repair pass; it never silently replaces a working config. `tinyic doctor` is the headless equivalent with reason-coded probes (`missing_credential`, `expired`, `unsupported_thinking_level`, `policy_disabled`, …). *(Amended by §15.2–15.3, 2026-07-14: a Grok subscription branch and a post-verification MODEL step.)*

---

## 7. Workstream 3 — Event stream & persistence

**FR-3.1 Event log.** Every debate appends to `~/.tinyic/runs/<debate_id>.jsonl`, schema in [event-schema.md](event-schema.md): versioned envelope (`v`, `seq`, `ts`, `debate_id`, `type`, `payload`), event types covering lifecycle, streaming deltas (THINK/TALK), cognitive state, steering, votes, memo sections, usage, and errors. The schema is the **public agent contract** — changes require a version bump and are additive within v1.

**FR-3.2 Replay.** `tinyic replay <debate_id|path>` re-renders any recorded debate in the TUI (with timing compression) with zero LLM calls; `tinyic export <debate_id> --html` produces a self-contained static HTML page (speeches, expandable thinking, scorecard, memo); `--md` exports the markdown bundle (scorecard/memo/transcript — replacing today's Streamlit downloads).

**FR-3.3 Determinism for tests.** Golden event-log fixtures drive renderer tests: TUI snapshot tests (Textual pilot) and HTML export tests run entirely from recorded logs.

---

## 8. Workstream 4 — Debate engine hardening (evidence-based)

**FR-4.1 Moderator.** A restraint-first, non-voting orchestrator component (optional lightweight ModelBinding) that: gates phase transitions, delivers steering (§9.3) at turn boundaries, selects and announces the devil's advocate, records structured verdicts, and requests the memo from the aggregator. It speaks in the transcript only for procedure (phase banners, steering acknowledgments).

**FR-4.2 Protocol caps.** Opening: one statement each. Cross-exam: at most **2 exchanges** per challenged persona (challenge → response). Rebuttal: one per persona. Verdict: one per persona. Caps configurable within bounds (research: sycophancy grows with rounds).

**FR-4.3 Anti-sycophancy composition.** Persona configs gain a `temperament` field (conciliatory ↔ contrarian); the default committee always includes at least one hard dissenter whose prompt prioritizes accuracy over agreement. The devil's advocate **actually rotates** (deterministic rotation keyed on a persisted per-install counter + `--da <persona>` override; fixes review B8). Per-turn persona reinforcement stays but is tightened to one line.

**FR-4.4 Structured artifacts.** Opening theses and final verdicts are emitted as structured records (claims + stance + confidence) alongside prose; free-form NL is reserved for cross-exam/rebuttal (anti-"telephone effect", per TradingAgents). Vote extraction consumes the structured verdict record first, LLM extraction as fallback.

**FR-4.5 Memo via MoA aggregation.** The memo/disagreement writer is the **aggregator binding**: it consumes per-phase structured records plus windowed speech context (Self-MoA-Seq-style sliding window with slots reserved for the running synthesis — bounds context, kills the 8K truncation), using Together's skeptical aggregator framing ("critically evaluate… may be biased or incorrect"). Disagreement analysis computes collapse metrics: per-persona stance trajectory, a disagreement-collapse rate, and "caved under pressure" flags emitted as events and shown in the thinking lane.

---

## 9. Workstream 5 — TUI (Textual)

**FR-5.1 Layout.** Header: company/ticker, phase indicator, elapsed, cost/usage meter. Main: **transcript pane** (chronological turn cards: persona, stance badge, speech streaming token-by-token; a collapsed `▸ thinking` row per turn, expandable). Right sidebar: **committee panel** — six persona cards with live cognitive-state badges (mood/attention/goal, from `cognitive_state`) and per-persona model/auth chips. Bottom: **composer** (§9.3) + status line. The *current* speaker's THINK streams live in an inline highlight block, auto-collapsing when TALK begins (locked interview decision).

**FR-5.2 Keys.** `enter` send · `tab` steer/queue toggle · `esc` hard interrupt · `t` toggle thinking on selected turn · `T` toggle all thinking · `space` pause/resume auto-advance · `n` next phase (when paused) · `m` cycle persona mind view · `q` quit (with confirm while a debate runs).

**FR-5.3 Steering (Codex semantics).** Composer always active. Two explicit modes shown as a chip: **Steer** (delivered at the next speaker-turn boundary within the current phase) and **Queue** (delivered at the next phase boundary). `@name` targets a persona (moderator relays to others as observation, as today). Messages appear in the transcript immediately with `queued` state, flip to `delivered` at injection, are delivered **one at a time** (never flushed simultaneously — a documented Codex bug class to avoid), and `esc` interrupts the current speaker: their in-flight call is cancelled, the steering message lands, and they retake the turn with it in context.

**FR-5.4 Phase flow.** Default auto-advance with a pause toggle; paused mode stops at each phase boundary with a banner (`n` to continue) — the between-phases reflection moment from the interview, preserved.

**FR-5.5 Performance.** Streaming updates batched (~30–60ms) so six concurrent-looking streams stay smooth; transcript virtualized beyond ~200 turns.

---

## 10. Workstream 6 — Headless CLI & agent contract

**FR-6.1 Commands.**
```
tinyic onboard | doctor
tinyic debate <ticker|company> [--preset X] [--personas a,b,c] [--model provider/model]
              [--thinking L] [--da persona] [--no-research] [--headless] [--json]
              [--phase-step] [--steer-stdin] [--yes]
tinyic replay <id|path> · tinyic export <id> --html|--md · tinyic result <id> --json
tinyic runs list · tinyic models list [--provider P] · tinyic auth login|status|logout
```

**FR-6.2 Agent mode.** `--headless --json`: no TUI; the event stream (schema §7) is written line-by-line to stdout as it happens; stderr carries human-readable progress; `--steer-stdin` accepts JSON lines (`{"type":"steer"|"queue", "target":..., "text":...}`) for programmatic steering; `tinyic result <id> --json` returns the final scorecard/memo document. Exit codes: `0` complete, `2` partial (some phases failed), `3` setup/auth error. Non-interactive runs never prompt (`--yes` semantics; missing auth → exit 3 with a `doctor`-style reason).

**FR-6.3 AGENTS.md.** Repo root gains an `AGENTS.md` teaching AI agents to drive TinyIC: install line, the three commands that matter, the event schema pointer, steering examples, and expected costs/durations. This file is part of the product surface.

---

## 11. Workstream 7 — Data pipeline

Unchanged sources (yfinance, EDGAR, yfinance news, xAI sentiment, OpenAI web research) with the review's defect fixes (FR-0.2) plus: per-source **availability badges** driven by resolved credentials (provider-tied sources light up only when their credential exists — locked decision), all fetched text delimited as data-not-instructions (D1), units normalized at the boundary (B7), and pipeline failures always degrade per-source (C3) with warnings surfaced as events.

---

## 12. Milestones (PR-per-milestone; each has a Definition of Done)

| M | Scope | DoD (beyond tests passing) |
|---|---|---|
| **M0** | Rebase to 0.7.0; Session scope (registry fix); packaging (`uv sync`, lockfile, entry point) | Two consecutive mocked debates in one process; `uvx tinyic --help` from clean clone |
| **M1** | Event stream + persistence + defect-class test suite (failing tests written; wave-1 fixes: B1–B7, B11, B12) | Mocked debate produces a valid v1 event log; adversarial vote fixtures pass; golden log recorded |
| **M2** | Model layer: adapters ×4 wire formats, bindings, thinking ladder, presets, usage events | Same mocked debate runs via 3 different adapters; thinking-gate matrix test green |
| **M3** | Auth: profiles, OpenAI OAuth lanes, Claude runtime lane, keyring; `onboard` + `doctor` | Fresh-machine walkthrough: onboard → verified binding → 1-token live test (behind live marker) |
| **M4** | Debate hardening: moderator, caps, temperaments, DA rotation, structured artifacts, MoA memo, collapse metrics | Recorded full live debate meets protocol acceptance checks; memo grounded on full transcript |
| **M5** | TUI: layout, streaming, thinking UX, composer with steer/queue/interrupt, replay | Textual pilot snapshot tests from golden logs; manual script: steer mid-phase, interrupt, expand thinking |
| **M6** | Headless mode + AGENTS.md + HTML/MD export; **remove Streamlit app**; README rewrite | An external agent script drives a full debate via `--json`/`--steer-stdin`; Streamlit gone; docs current |

Sequencing rule: product code never merges ahead of its defect-class tests. Fork-internal findings not on the product path (review A4/E-class leftovers) are batched into a final cleanup PR and must not block M1–M6.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Anthropic third-party subscription policy shifts again (3 changes in 2026) | `policy_guard` disable switch + API-key overflow + honest onboarding copy; lane is additive, never load-bearing |
| Subscription rate windows too small for 6×4 debates (Plus ≈ 20–110 msgs/5h) | Usage meter, cheap-tier persona guidance, automatic key-overflow, caps from FR-4.2 reduce calls |
| Rebase to 0.7.0 surfaces behavior drift | M0 is isolated; defect tests re-run on the new base define regressions objectively |
| Textual streaming perf with 6 personas | Batched updates, virtualized transcript, perf test in M5 |
| Token-cancel semantics vary per provider | Interrupt = cancel-if-supported else discard-on-arrival; event log records which |

## 14. Success criteria (v1)

1. A stranger runs `uvx tinyic` → onboarded → watches a full AAPL debate with visible thinking and steers it, within 10 minutes of the README.
2. Two debates back-to-back in one process; replay renders any past debate identically from its log.
3. `Claude Code`/Codex can run a debate end-to-end via the documented headless contract without human input.
4. All 33 defect classes have merged acceptance tests; cost display shows real, per-debate, per-persona numbers.
5. A mixed-provider committee (≥3 providers incl. one subscription lane and one local model) completes a debate.

---

## 15. v2.1 amendment (2026-07-14) — provider matrix, Grok lane, Kimi, model selector

> **Status: implemented.** This section amends the v1 spec; where they disagree, this section wins. §§1–14 are kept as written for the historical record. Provider facts below were verified against official provider documentation on 2026-07-14.

### 15.1 Provider matrix refresh (amends FR-1.2)

The bundled provider set is now **openai · anthropic · grok · google · kimi · ollama**.

- **`xai` → `grok` is a hard rename.** No alias — `xai/...` model refs no longer resolve. The env var stays `XAI_API_KEY` and the base URL `api.x.ai/v1` (xAI's own conventions survive the rename).
- **DeepSeek is removed from the product entirely** (owner decision).
- **Kimi (Moonshot AI) is added**: `openai-chat` wire against `https://api.moonshot.ai/v1`, with `MOONSHOT_BASE_URL` selecting the China endpoint (`https://api.moonshot.cn/v1`); credentials resolve `MOONSHOT_API_KEY` then `KIMI_API_KEY`. Catalog: `kimi-k2.6`, `kimi-k2.5` (the `kimi-k2-*-preview` generation was discontinued 2026-05-25 and is not shipped). Kimi thinking is ON by default — the ladder's `off` renders `{"thinking":{"type":"disabled"}}`, and reasoning streams as `reasoning_content` (already handled by the chat adapter). The `KimiChatAdapter` adds the **opt-in server-side `$web_search` builtin** (`params.web_search = true` on the binding): declared as a `builtin_function`, driven by the echo protocol (on `finish_reason="tool_calls"` the client returns `function.arguments` verbatim as the tool message and re-sends; the server injects results), tool frames never leak into the visible stream, usage summed across rounds. Moonshot requires thinking **disabled** for `$web_search`; a violating binding is rejected at transport construction. *Recorded finding:* Kimi Work's China financial datasets are product-only (no API) — a pipeline-level China-market data source is tracked as a follow-up, out of scope for v2.1.
- **Google is API-key-only by policy, not by omission.** Google's terms explicitly prohibit third-party reuse of consumer-subscription OAuth following the June 2026 Antigravity transition, so TinyIC deliberately ships no Google subscription lane; the onboarding copy states this as the design reason.

### 15.2 Grok subscription lane (extends FR-2.2/FR-2.3's pattern)

A third subscription lane joins OpenAI and Anthropic, modeled on both:

- **Read-through** of the official grok CLI's sign-in (`~/.grok/auth.json`, current and legacy document shapes) — strictly read-only: TinyIC **never rewrites the CLI's file**; a stale token refreshes in memory for the session only (the FR-2.2 refresh-rotation hazard, applied).
- **Device-code OAuth** (RFC 8628 with PKCE S256) against `auth.x.ai` using the public desktop client id, producing a TinyIC-owned token profile (keyring-first). Both profile kinds are provider-scoped to `grok` and subscription-lane-only; all read-through aliases share one rotation owner (one CLI login is not two quota fallbacks).
- Subscription traffic is the same OpenAI-chat wire with a Bearer token; the base URL is overridable via `GROK_SUBSCRIPTION_BASE_URL`. xAI enforces entitlement **server-side**: the 403 maps to the stable, secret-free reason code `subscription_inactive` and advances `auth_order` rotation (e.g. to an `XAI_API_KEY` overflow) with zero replay.
- **Policy posture: unverified but tolerated.** xAI has published no explicit third-party policy for this lane; the onboarding copy says so plainly, and the lane sits behind `[auth.grok] policy_guard` in `tinyic.toml` (default **on**), exactly parallel to the Anthropic guard. Like every subscription lane it is additive, never load-bearing.
- `tinyic doctor` gains the (`grok`, `subscription`) lane under the existing reason-code contract; `onboard` gains the corresponding subscription branch (SuperGrok / X Premium+ copy).

### 15.3 Model selector (amends FR-2.4 and FR-6.1)

- **Catalog service** (`models/catalog.py`): one normalized view of "which models can this install run" — the static registry catalogs enriched with pinned context/output metadata, merged with **explicit-only** live refresh per provider (anthropic `/v1/models`, google `/v1beta/models`, grok `/v1/language-models`, kimi `{base}/models` — all metadata-rich — plus ollama's local `/api/tags`; OpenAI stays static-only because its listing returns bare ids). Each provider degrades independently to its static catalog with a stable warning code; static entries are never dropped by a lagging listing; credentials ride the adapters' own seams and go into request headers, never URLs.
- **`tinyic models [provider] [--refresh] [--json]`** — FR-6.1's `tinyic models list [--provider P]` shipped with this syntax: an aligned human table with `[WARN]` degrade lines, or one schema-versioned, secret-free JSON document. Degraded refreshes still exit 0 (the command succeeded); an unknown provider exits 2.
- **Wizard MODEL step:** in `tinyic onboard`, a verified lane earns a model-picker step — that provider's catalog (context window and thinking capability shown when known), an optional off-thread live refresh gated by `CatalogService.can_refresh`, and the pick persisted as the committee default; `esc` keeps the current default.
- **User overlay:** the pick is written to `~/.tinyic/tinyic.toml` (`$TINYIC_USER_CONFIG` override) via `set_user_default_binding`, which writes **only** the overlay. The overlay deep-merges *over* the base config (explicit path / `$TINYIC_CONFIG` / `./tinyic.toml` / built-in); the repo's `tinyic.toml` is never written by TinyIC.

### 15.4 Catalog refresh (current as of 2026-07-14)

| Provider | Shipped catalog | Thinking mapping |
|---|---|---|
| openai | `gpt-5.6-sol` (Responses-routed; no longer subscription-only), `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.2` (legacy, still servable) | `reasoning_effort` / nested `reasoning.effort`; `none`…`xhigh` (ladder `off` → `none`; `max` remaps to `xhigh` at runtime) |
| anthropic | `claude-fable-5`, `claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5` | **adaptive scheme** on fable-5 / opus-4-8 / sonnet-5: `thinking={"type":"adaptive"}` + `output_config.effort` `low`…`max` (`budget_tokens` is a 400 there; these models cannot disable thinking — `off`/`minimal` follow FR-1.3 reject/remap); haiku-4-5 keeps legacy `budget_tokens` |
| grok | `grok-4.5`, `grok-4.3`, `grok-4.20` | `reasoning_effort` `low\|medium\|high`; grok-4.5 cannot disable reasoning (`off` = config-time reject, runtime remap → `low`) |
| google | `gemini-3.5-flash`, `gemini-3.1-pro-preview`, `gemini-3.1-flash-lite` (`gemini-2.5-pro` dropped ahead of its 2026-10-16 shutdown) | effort knob on the OpenAI-compat endpoint, stripped gracefully if the server rejects it |
| kimi | `kimi-k2.6`, `kimi-k2.5` | default-on toggle: `off` → `{"thinking":{"type":"disabled"}}`, else enabled |
| ollama | `qwen3:32b`, `qwen3.5`, `deepseek-r1:14b`, `llama3.1:8b`, `gemma4` | `think` flag where the model supports it |

The `default` preset (FR-1.4) is now `openai/gpt-5.6-sol` @ `high`; the bundled `heterogeneous` preset demonstrates a Kimi Graham, an Anthropic aggregator, and a `gpt-5.6-luna` minimal-thinking moderator. The usage price table covers every catalog model as of the same date. The event schema (§7) is unchanged — v2.1 added no event types and no payload fields.
