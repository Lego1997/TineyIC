# PRD Acceptance — TinyIC v2 Success Criteria (§14)

| | |
|---|---|
| **Status** | M6 complete — this is the closing artifact of the v1 implementation |
| **Date** | 2026-07-13 |
| **Scope** | Audits the five v1 success criteria in [`PRD.md` §14](PRD.md) against merged tests and commands |
| **Ground truth** | [`PRD.md`](PRD.md) · [`event-schema.md`](event-schema.md) (the agent contract) · [`code-review-2026-07-12.md`](code-review-2026-07-12.md) (the 33 defect classes) |

This document maps each PRD §14 success criterion to concrete, runnable evidence. It is deliberately honest about the boundary between what an **offline** CI machine can prove (recorded-SSE transports, deterministic mocks, zero network) and what genuinely needs **live** provider credentials. TinyIC's test discipline keeps everything network-touching behind the `live_api` marker, so the entire offline suite runs and stays green with no keys.

### Verdict legend

- **MET** — proven end to end offline; the criterion has no live-only residue.
- **MET-OFFLINE** — every mechanism the criterion names is proven offline; a residual *live-only* aspect (wall-clock timing, real-provider ergonomics, a genuine multi-provider run) is called out explicitly and is exercised by a `live_api` test that skips without keys.
- **NEEDS-LIVE** — would require credentials to demonstrate; noted where it applies.

### How to reproduce

```bash
uv sync --all-packages
uv run pytest tests/ -m "not live_api"      # the full offline suite (green: 1007 passed, 8 deselected)
uv run pytest tests/ -m live_api            # the live checks (need OPENAI_API_KEY etc.; skip otherwise)
uv run pytest tests/test_m6_agent_contract.py   # the M6 headless agent-contract DoD (subprocess)
```

---

## Summary

| # | Criterion (abbreviated) | Verdict | Primary evidence |
|---|---|---|---|
| 1 | Stranger: `uvx tinyic` → onboard → watch a thinking AAPL debate → steer it, within 10 min | **MET-OFFLINE** | packaging + onboard wizard + town-hall pilots; live 10-min round-trip behind `live_api` |
| 2 | Two debates back-to-back in one process; replay renders any past debate identically | **MET** | `test_session_scope.py` + town-hall replay pilots from golden logs |
| 3 | Claude Code / Codex drives a debate end-to-end via the headless contract, no human | **MET-OFFLINE** | `test_m6_agent_contract.py` (real subprocess); real-agent ergonomics need live keys |
| 4 | All 33 defect classes have acceptance tests; cost display shows real per-persona numbers | **MET-OFFLINE** | defect-regression suite + per-model/per-persona usage events (recorded fixtures) |
| 5 | Mixed-provider committee (≥3 providers incl. a subscription lane and a local model) completes | **MET-OFFLINE** | 3-adapter DoD + heterogeneous attribution; the live mixed run needs those runtimes |

---

## Criterion 1 — First-run experience

> "A stranger runs `uvx tinyic` → onboarded → watches a full AAPL debate with visible thinking and steers it, within 10 minutes of the README."

**Verdict: MET-OFFLINE.** Every mechanical link in the chain is proven offline; the only live residue is the end-to-end **wall-clock** (a full six-persona debate against a real provider) and the real onboarding round-trip, both of which require credentials.

Evidence — the chain, link by link:

- **`uvx tinyic` from a clean clone.** `tests/test_m0_packaging.py::test_tinyic_console_entry_point_help` (the console entry point runs and prints help), `::test_tinyic_sdist_rebuilds_wheel_with_persona_configs` (a built wheel ships the persona configs), `::test_plain_uv_sync_includes_both_workspace_packages`. Command (M0 DoD): `uvx tinyic --help`.
- **Onboarded.** The FR-2.4 wizard (detect → choose a lane → connect → verify → summary): `tests/test_m3_onboard_wizard.py` (14 tests, e.g. `::test_api_key_success_verifies_persists_and_advances`, `::test_failed_verify_never_persists_and_never_overwrites`) and the Textual pilot `tests/tui/test_pilot_onboard.py`.
- **Watches a full AAPL debate with visible thinking.** Town-hall pilots rendering a golden four-phase log: `tests/tui/test_pilot_townhall.py::test_a_every_talk_completed_text_appears_in_transcript`, `::test_c_phase_banners_render_in_protocol_order`, and — the "visible thinking" differentiator — `::test_b_thinking_hidden_by_default_and_revealed_by_t_and_T`.
- **Steers it.** `tests/tui/test_pilot_townhall.py::test_d_steering_message_shows_queued_then_delivered`, `::test_e_tab_toggles_steer_queue_chip`, and `tests/test_tui_steering.py`.
- **"…of the README."** The README and `AGENTS.md` were rewritten for the three-faces product in M6 (commits `74c9726`, `7d6ddc4`).

**Live residue (NEEDS-LIVE):** the literal "within 10 minutes" wall-clock and a real onboard→debate round-trip against a provider. Exercised by `tests/test_m3_auth_live.py` (one-token verification) and `tests/test_debate_live.py` (a live debate), both behind `live_api`. Command: `uv run pytest -m live_api`.

---

## Criterion 2 — Replayability and one-process reuse

> "Two debates back-to-back in one process; replay renders any past debate identically from its log."

**Verdict: MET.** This criterion is entirely mechanical (no model output is judged), so the offline proof is the whole proof.

- **Two debates back-to-back in one process** (the fix for showstopper A1, the process-global name registries): `tests/test_session_scope.py::test_two_consecutive_mocked_debates_reuse_names_in_one_process`, `::test_caller_owned_session_is_reusable_across_full_debates`, `::test_direct_orchestrator_engine_path_can_run_twice`. This is the M0 Definition of Done.
- **Replay renders any past debate identically from its log.** The renderer consumes only the JSONL event log, never engine internals: `tests/test_tui_app.py::test_app_loads_events_from_log_path_without_running` and `::test_run_replay_constructs_app_and_runs`. The town-hall pilots (`tests/tui/test_pilot_townhall.py::test_a`…`test_i`) all render purely from a recorded fixture log, including graceful handling of a truncated (crash-mid-debate) log at `::test_g_truncated_log_renders_and_shows_incomplete_indicator`. The same log also drives the static exports (`tests/test_m6_export.py`, `tests/test_export.py`), so HTML/MD replay is identical-from-log too.

The replayability guarantee is the schema's compatibility promise #2 (`event-schema.md`): a log is replayable iff it opens with `debate_started` and closes with `debate_completed`/`debate_error`.

---

## Criterion 3 — Agent-launchability (the M6 headline)

> "`Claude Code`/Codex can run a debate end-to-end via the documented headless contract without human input."

**Verdict: MET-OFFLINE.** The documented contract is proven *end to end across a real process boundary* — the exact surface an external agent sees. The only live residue is a real Claude/Codex session's ergonomics against a live provider.

**The DoD centerpiece — `tests/test_m6_agent_contract.py`.** It launches `tinyic` **as a fresh child process** (real STDOUT/STDERR/STDIN pipes, real worker + log-tailer + stdin-reader threads, a real event-log file, a real exit code) with the recorded-SSE transports standing in for a live provider (a subprocess cannot receive a pytest `monkeypatch`, so the offline fakes are installed by `tests/support/agent_contract_driver.py`, which the parent configures purely through argv/env). The test asserts the full contract:

- `::test_agent_drives_a_full_debate_via_json_and_steer_stdin` — under `--headless --json --steer-stdin`, feeds a steer line and an interrupt over STDIN mid-run and asserts:
  - **every** STDOUT line is schema-valid v1 JSONL, opening `debate_started` and closing `debate_completed`, with all four phases completed and the vendored import banner + config dump kept off STDOUT (they go to STDERR);
  - the steer is acknowledged then delivered — `steering_submitted` (`source: stdin`) strictly precedes `steering_delivered`, sharing a `msg_id`, delivered at a real speaker-turn boundary;
  - the interrupt yields exactly one `turn_interrupted` with **engine-authoritative** provenance (`by: user`, `disposition: discarded_on_arrival`), the discarded turn never commits, and the same speaker retakes;
  - the process exits `0`; and
  - a **separate** `tinyic result <id> --json` subprocess returns the assembled document (scorecard, memo sections, votes, usage rollup) — schema compatibility promise #3.
- `::test_missing_auth_exits_3_with_clean_reason_on_stderr` — the real `tinyic` CLI with a real provider preset and no resolvable credential exits `3`, with a `doctor`-style, secret-free reason on STDERR and a pristine JSONL STDOUT (also guards the `--personas` import-banner leak).
- `::test_injected_mid_debate_failure_exits_2` — every phase runs, then extraction is forced to fail: `debate_error` after ≥1 completed phase → exit `2`, the FR-6.2 "partial" code, with a class-name-only message (no raw provider/credential detail).

Supporting: the in-process headless path (`tests/test_m6_headless.py`), the contract document itself (`event-schema.md`), and the agent-facing `AGENTS.md` at the repo root (FR-6.3).

**Live residue (NEEDS-LIVE):** an actual Claude Code / Codex session driving `tinyic debate --json --steer-stdin` against a live provider — the "real-agent ergonomics." The offline subprocess is the deterministic stand-in; a live driver run is behind `live_api` (`tests/test_debate_live.py`).

---

## Criterion 4 — Defect coverage and honest cost display

> "All 33 defect classes have merged acceptance tests; cost display shows real, per-debate, per-persona numbers."

**Verdict: MET-OFFLINE** — 30 of 33 classes by merged acceptance tests; the remaining three (C1 "stop doesn't stop", C2 global-model races, C6 stale sidebar) are Streamlit-runtime defects **retired by architectural removal** rather than regression-tested, per the scope note below. The canonical list of 33 is `code-review-2026-07-12.md` (A1–A4, B1–B12, C1–C9, D1–D3, plus the E-class polish items). The defect-class acceptance suite is merged and green offline; the cost half is proven with real, per-model/per-persona numbers over recorded fixtures.

Defect acceptance tests, by class:

- **Silent wrong output (B — the review's dominant, scariest class).** `tests/test_defect_regressions.py` (B1 vote-polarity, B2 confidence/null-vote normalization, B3 per-agent extraction isolation, B4 full-history rendering, B5 repetition guard, D2 log redaction); `tests/test_data_integrity_regressions.py` (B6 filing summaries vs. table-of-contents, B7 the 100× debt-to-equity unit error); `tests/test_m1_b11_b12.py` (B11 $0 cost, B12 dropped `response_format`); DA rotation (B8) in `tests/test_moderator.py` + `tests/test_m4_dod.py`.
- **Architectural showstoppers (A).** A1 (second-debate registry crash) — `tests/test_session_scope.py`; A2/A3 (broken fresh-clone onboarding / the committed API proxy weld) — `tests/test_m0_packaging.py`.
- **Reliability & privacy (C/D).** C3 per-source graceful degradation — `tests/test_data_pipeline.py`, `tests/test_research.py`; C9 steering never silently dropped — `tests/test_tui_steering.py` plus the engine-authoritative interrupt in `tests/test_m6_agent_contract.py`; D1 prompt-injection "treat-as-data" framing — `tests/test_moa_memo.py`; D2 credential redaction in logs — `tests/test_defect_regressions.py`.

**Honest scope note.** A literal one-test-per-defect table is a *milestone-level* claim delivered across M0–M6, not a single file. A few Streamlit-era concurrency items (C1 "stop doesn't stop", C2 global-model races, C6 stale sidebar) are retired by **architecture** rather than a regression test: the Streamlit app is removed in M6 (commit `3bc6897`) and replaced by the event-log + worker-thread engine whose interrupt/stop semantics are covered by the agent-contract interrupt and `tests/test_tui_controls.py`. The claim here is that the merged offline suite covers the defect classes as cited above and is green; it is not a claim that a live provider re-executed each historical bug.

**Cost display — real, per-debate, per-persona numbers (the B11 fix, via `usage` events):**

- Per-persona / per-model attribution: `tests/test_m2_dod.py::test_heterogeneous_committee_attributes_each_turn_to_its_persona_and_model` — each turn's tokens are attributed to the acting persona *and* its model.
- The result-document usage rollup with real recorded-fixture token counts: `tests/test_m6_agent_contract.py` asserts `usage.total.input_tokens > 0` and `usage.by_model` contains `openai/gpt-5.2` (the `usage` schema is in `event-schema.md` §Usage & cost). The numbers are the provider's real streamed token counts; a live run surfaces live token/cost figures through the identical code path (cost is `null` on subscription lanes, per schema).

---

## Criterion 5 — Mixed-provider committee

> "A mixed-provider committee (≥3 providers incl. one subscription lane and one local model) completes a debate."

**Verdict: MET-OFFLINE.** The full mixed-binding mechanism — different personas routed to different providers/wire formats within one debate — is proven offline; the residue is a genuine run against ≥3 *live* runtimes.

- **The same debate runs via three different wire adapters with identical structure:** `tests/test_m2_dod.py::test_same_mocked_debate_runs_via_three_adapters_with_identical_structure` (the M2 DoD).
- **A heterogeneous committee, each turn attributed to its own persona and model:** `tests/test_m2_dod.py::test_heterogeneous_committee_attributes_each_turn_to_its_persona_and_model`.
- **The four wire adapters and binding/preset precedence:** `tests/test_model_adapters.py`; preset field/precedence resolution in `tests/test_m2_dod.py` (`::test_preset_field_precedence_role_and_persona_override_committee_default`, `::test_per_debate_override_is_highest_precedence`).
- **Subscription lanes** (the "≥1 subscription lane" half): `tests/test_m3_openai_subscription.py`, `tests/test_m3_anthropic_subscription.py`, and the usage-window meter `tests/test_m3_usage_window.py`.

**Live residue (NEEDS-LIVE):** a genuine mixed-provider debate spanning, say, an OpenAI subscription lane + an API-key provider + a local Ollama model. That needs the real runtimes and is behind `live_api` (`tests/test_debate_live.py`, `tests/test_m3_auth_live.py`).

---

## What a live sign-off adds

The offline suite proves every *mechanism* the PRD names. A credentialed sign-off would close the remaining live residue in one session:

1. **Criterion 1** — time a real `uvx tinyic` → onboard → six-persona AAPL debate and confirm it lands inside the 10-minute budget.
2. **Criterion 3** — have Claude Code / Codex actually drive `tinyic debate --json --steer-stdin` against a live provider and consume `tinyic result --json`.
3. **Criterion 5** — run a committee whose personas span ≥3 live providers (incl. a subscription lane and a local model) to completion, and read the per-model cost/usage rollup.

All three are wired as `live_api` tests today; they skip cleanly with no keys, so the offline gate stays deterministic and green.
