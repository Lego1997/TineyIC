# Codex Kickoff Prompt — TinyIC v2 Implementation

Copy everything below the line into a fresh Codex (GPT-5.6) session started at the repository root.

---

You are implementing **TinyIC v2**, a full revamp of this repository (an AI investment-committee simulator), working from an approved PRD. You are the implementer; the product decisions are already made and are not yours to relitigate. Where the PRD is ambiguous, choose the smallest interpretation consistent with its architecture rules and record the choice in the PR description; where the PRD seems *wrong* (contradicts code reality or another requirement), stop and raise it in the PR/issue rather than silently deviating.

## Read first, in this order

1. `docs/PRD.md` — the complete requirements, workstreams FR-0…FR-6, milestones M0–M6 with Definitions of Done, risks. This is your contract.
2. `docs/event-schema.md` — the JSONL event contract. This is the load-bearing architectural rule: **the engine emits events; renderers consume events; nothing else crosses that boundary.**
3. `docs/code-review-2026-07-12.md` — 33 verified defects in the current code. All are in scope as *defect classes with acceptance tests* (PRD FR-0.2). Line numbers will shift when you rebase the vendored TinyTroupe to upstream 0.7.0; the described failure scenarios, not the line numbers, define each defect.
4. `docs/research/2026-07-12-model-agnostic-brief.md` — evidence behind the model layer, the thinking-effort ladder, and the two subscription-auth lanes, including the exact Anthropic policy constraints. `docs/research/2026-07-12-notebook-town-hall-brief.md` — evidence behind the debate-protocol hardening (sycophancy caps, temperaments) and streaming architecture.
5. `CLAUDE.md` — current-code orientation (architecture, commands, gotchas). Note: it describes the *pre-revamp* code; update it as part of M6.

## Non-negotiable rules

- **Event stream is the only engine→UI channel.** If you find yourself having a renderer import engine internals, stop; emit an event instead.
- **Tests before fixes** for every defect class: write the failing test from the review's failure scenario first (or prove the defect vanished in the rebase), then fix. Product code never merges ahead of its defect-class tests.
- **Auth boundaries are legal boundaries.** OpenAI subscription: native OAuth (device-code + browser) and read-through of `~/.codex/auth.json` — never rewrite another tool's credential file. Anthropic subscription: ONLY via the user's own Claude Code login through the Agent SDK / `claude -p` runtime; you must NOT implement a Claude.ai OAuth flow, and the lane must honor the `policy_guard` disable switch. No credentials in events, logs, exports, or committed files. Remove the committed third-party proxy endpoint from `config.ini` as part of M0.
- **One milestone per PR** (M0→M6 in order), each meeting its Definition of Done from PRD §12, each with a verification section in the PR description showing the DoD commands/output.
- Work on a feature branch off `revamp/prd`; never commit directly to `main`.
- Offline test suite stays offline (no network); live-API tests stay behind the `live_api` marker.
- Python 3.12, `uv` workspace; after M0, plain `uv sync` must work on a clean clone and `uv.lock` is committed.

## Current-state facts you'd otherwise discover slowly

- Install today requires `uv sync --all-packages` (root package has no deps — you fix this in M0). Test suite: `uv run pytest tests/` (262 passing offline currently).
- The vendored `src/tinytroupe` is a patched Microsoft TinyTroupe v0.6.0; upstream 0.7.0 (MIT) adds vision + pickle→JSON cache. The `PATCH(tinyIC)` markers in `clients/openai_client.py` exist for a proxy you are removing; the forced `stream=True` + `_collect_stream` shape is being replaced by real streaming adapters (PRD FR-1.2), so don't preserve those patches — replace them.
- The #1 landmine: `TinyPerson.all_agents` and `TinyWorld.all_environments` are process-global registries that raise on duplicate names — any second debate in one process crashes today. PRD FR-0.2/A1 (Session scoping) fixes this and everything depends on it; do it first in M0.
- Personas' THINK actions and per-turn `cognitive_state` are already produced by the agent loop (`tiny_person.v2.mustache` mandates THINK-before-TALK) — today's UI throws them away. Your event emitters in M1 capture them.
- The existing Streamlit app (`src/tinyic/ui/app.py`) remains untouched until M6, where it is deleted along with its tests.

## Start now

Begin **M0**: rebase `src/tinytroupe` onto upstream TinyTroupe 0.7.0 (document retained divergences in `src/tinytroupe/FORK.md`), implement Session-scoped agent/world registries with the two-consecutive-debates acceptance test, fix packaging (`uv sync`, committed lockfile, `tinyic` console entry point), and remove the proxy endpoint from committed config. Open the PR titled `M0: foundation — 0.7.0 rebase, session scoping, packaging`.
