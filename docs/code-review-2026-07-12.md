# TineyIC Code Review — 2026-07-12

Full-repo review performed ahead of the notebook/town-hall revamp. Findings marked **CONFIRMED** were traced end-to-end or reproduced by executing code; **RISK** items are plausible but untraced. File references are clickable paths.

---

## A. Showstoppers (block the revamp architecture directly)

### A1. Any second debate in one process crashes — global name registries — CONFIRMED (reproduced)
`TinyPerson.all_agents` (src/tinytroupe/agent/tiny_person.py:64) and `TinyWorld.all_environments` (src/tinytroupe/environment/tiny_world.py:26) are class-level dicts; duplicate names raise `ValueError` (tiny_person.py:2024-2032, tiny_world.py:976-981). The app never clears them; the test suite does (tests/test_debate.py:49-53, tests/test_personas.py:19) — proof the authors hit this. Reproduced: second `load_persona("warren_buffett")` → `Agent name Warren Buffett is already in use`; second `DebateOrchestrator(name="IC-AAPL")` → `Environment names must be unique`. Consequences: "New Debate" and "Re-run with same ticker" both error; a second browser session collides with the first; **notebook cell re-execution hits the identical crash** — this is the #1 must-fix for the revamp. Worse, registration happens inside `super().__init__` *before* the philosophy config loads (src/tinyic/personas/base.py:23-27), so a config error leaves a half-built agent permanently registered.

### A2. Fresh-clone onboarding is broken — CONFIRMED (reproduced)
README says `uv sync`, but the root package has `dependencies = []`, so neither workspace member is installed; `uv run pytest` fails with `ModuleNotFoundError: No module named 'tinytroupe'`. `uv sync --all-packages` works (verified: 262 passed, 6 skipped in 37s). `uv.lock` is gitignored, so builds aren't reproducible either.

### A3. The repo is welded to a third-party API proxy — CONFIRMED
config.ini ships `BASE_URL=https://api-vip.codex-for.me/v1` while the README claims a plain OpenAI key. The fork carries `PATCH(tinyIC)` comments forcing `stream=True` because "required by proxy gateway" (src/tinytroupe/clients/openai_client.py:364-365). Two code paths bypass the proxy and hit api.openai.com directly with whatever key is set: deep research (`OpenAI(api_key=...)` with default base URL, src/tinyic/data/research.py:57-59) and llama-index embeddings for semantic memory (src/tinytroupe/__init__.py:422-425). Whichever key type you use, one side fails — and both failures are swallowed silently (research returns `""`; semantic memory stays empty forever, memory.py:599-605). A proxy key sent to api.openai.com is also a credential-hygiene problem.

### A4. `control.py` simulation caching is incompatible with the orchestrator — CONFIRMED
`control.begin(id=...)` with any non-default id raises `KeyError` (src/tinytroupe/control.py:775-841). If caching were enabled, `Simulation._encode_simulation_state` deep-copies agent/world `__dict__`s — the orchestrator holds a `queue.Queue` and `threading.Event` (src/tinyic/debate/orchestrator.py:67-69), which are not deep-copyable → crash at first transaction. Relevant if the notebook revamp wants checkpoint/replay via control.py.

## B. Silent wrong-output bugs (the scariest class — no crash, wrong results)

### B1. SELL votes containing the word "buy" are recorded as BUY — CONFIRMED (executed)
`fuzzy_match_vote` checks `"BUY" in upper` before `"SELL"` by substring (src/tinyic/debate/models.py:47-58). Executed: `"SELL (I would not buy here)"` → BUY; `"Do not buy"` → BUY. One flipped vote can flip the committee consensus (src/tinyic/debate/extraction.py:133-146).

### B2. Mixed-case confidence discards the entire vote — CONFIRMED (executed)
`confidence` has no fuzzy validator; `Vote(vote="BUY", confidence="High")` raises, and the per-agent except replaces the whole (correct) vote with HOLD/LOW/"Extraction failed" (src/tinyic/debate/extraction.py:98-117). Also `"vote": null` bypasses the `.get` default and fails the same way.

### B3. One extraction failure downgrades the whole committee to HOLD — CONFIRMED
`extract_results_from_agents` has no per-agent error handling (src/tinytroupe/extraction/results_extractor.py:76-83); one transient API error on agent #4 → the catch at extraction.py:79 discards all previously-extracted votes → unanimous "HOLD (Extraction failed)".

### B4. Vote extraction and transcript see only the first 1024 chars of each speech — CONFIRMED
`pretty_current_interactions(max_content_length=None)` is intended as "unlimited", but the `@config_defaults` decorator replaces every explicit `None` with `MAX_CONTENT_DISPLAY_LENGTH=1024` (src/tinytroupe/extraction/results_extractor.py:137-139, src/tinytroupe/agent/tiny_person.py:1644, config.ini). Debate speeches are longer than 1024 chars — so final votes are extracted from truncated speeches, and the exported transcript is truncated too. Memo generation then truncates *again* to the last 8000 chars (src/tinyic/debate/memo.py:22,80-85), usually dropping opening statements entirely.

### B5. Repetition guard silently blanks long, distinct speeches — CONFIRMED (measured)
The "action similarity" guard uses `textdistance.jaccard` on raw strings = character multisets (src/tinytroupe/utils/behavior.py:29). Measured in the project venv: two *completely different* ~1000-char investment speeches score 0.870 > the 0.85 threshold (src/tinytroupe/agent/tiny_person.py:37) → the new speech is replaced by DONE, the agent gets a scolding "EXCESSIVE ACTION SIMILARITY" memory, and the UI shows an empty message (tiny_person.py:657-689). Fires precisely when the debate is working well (long, on-topic, shared-vocabulary speeches).

### B6. 10-K/10-Q "summaries" are actually the table of contents — CONFIRMED (executed)
Section extraction takes the *first* regex match of "Item 1"/"Item 1A"/"Item 7" (src/tinyic/data/filings.py:49-63); nearly every filing's markdown opens with a TOC containing those strings, so personas receive page-number junk as the filing summary, and the non-empty result suppresses the raw-text fallback (filings.py:129-130). Also: 10-Q keys overlap (duplicate content) and the join-then-slice at line 148 chops the third section.

### B7. Debt-to-equity has a 100× unit error — CONFIRMED
yfinance `debtToEquity` is a percent (the repo's own fixture: 178.7 for AAPL ≈ 1.79 real D/E, tests/test_data_pipeline.py:458) but the UI renders "D/E Ratio 178.7x" (src/tinyic/ui/app.py:441-459) and the raw unlabeled number goes into the LLM context, priming personas to see catastrophic leverage. `dividend_yield` units are similarly ambiguous vs. the fixture convention.

### B8. The "rotating" devil's advocate never rotates — CONFIRMED
`_da_index` starts at 0 in each fresh orchestrator and CROSS_EXAM occurs once per debate, so the DA is always `agents[0]` — Warren Buffett whenever he's selected (src/tinyic/debate/orchestrator.py:72,99-104,133). README's "rotating devil's advocate" is false in practice.

### B9. Agents' `attention` state is overwritten with `emotions` — CONFIRMED
`attention=cognitive_state.get("emotions", None)` (src/tinytroupe/agent/tiny_person.py:713-718). The model's attention field is discarded every turn; attention mirrors emotions in every subsequent prompt and in memory retrieval. Matters double for a revamp that wants to *display* cognitive_state.

### B10. Ticker resolver can silently swap exchanges — CONFIRMED
When `.info` fails but `fast_info` succeeds, the resolver adopts the *search result's* symbol instead of the validated one (src/tinyic/data/ticker_resolver.py:162-168); Yahoo search ranks US listings first, so `SAP.DE` can become NYSE `SAP`, changing currency/exchange for all downstream data.

### B11. Cost tracking reports $0 — CONFIRMED
Forced streaming never sends `stream_options={"include_usage": true}`, so streamed responses carry no usage; `ChatCompletion.usage` is None and token accounting is skipped (src/tinytroupe/clients/openai_client.py:365,432-459,678-685). Additionally, `TinyWorld.get_cost_stats` divides *process-global* client counters (never reset between debates, and shared with research/memo calls) (src/tinytroupe/environment/tiny_world.py:1021-1081), and the UI hardcodes GPT-5.2 pricing regardless of model (src/tinyic/debate/__init__.py:89-91).

### B12. `response_format` is silently dropped on every call — CONFIRMED
The proxy patch pops `response_format` before sending (src/tinytroupe/clients/openai_client.py:390-405), so all structured-output requests (action generation, extraction, LLMChat) rely on prompt discipline plus a 5-retry JSON repair loop (tiny_person.py:750) — slower, costlier, and less reliable. Also makes the API cache key inconsistent between attempts (openai_client.py:263).

## C. Reliability & concurrency

- **C1. "Stop" doesn't stop — CONFIRMED.** `stop_event` is only checked *after* `run_debate()` completes (src/tinyic/ui/app.py:768); all 4 phases of LLM calls run to completion after cancel. `_reset_state` (app.py:1037-1046) orphans the worker; a worker parked on `phase_gate.wait()` (no timeout, src/tinyic/debate/orchestrator.py:117-119) blocks forever, its `finally` (global model restore) never runs, and every reset leaks another daemon thread holding agents.
- **C2. Global model switch races — CONFIRMED (multi-session).** `config_manager.update("model", ...)` from worker threads mutates a process-global read at call time by every LLM call (src/tinyic/ui/app.py:687-689,804-805); two sessions with different models clobber each other mid-debate.
- **C3. Research JSON crash violates graceful degradation — CONFIRMED.** If the model returns a JSON array, `extract_json` returns a list; `data.get(...)` raises `AttributeError` *outside* the try (src/tinyic/data/research.py:145-162), and `pipeline.py:86` has no guard → the whole `build_data_package` (and debate) dies from one odd LLM response, despite the module contract (src/tinyic/data/pipeline.py:33).
- **C4. memo.py breaks its "never fails" contract on non-dict shapes — RISK** (list JSON, string sides → ValidationError outside try; src/tinyic/debate/memo.py:158-182,220-244). The UI happens to wrap it; other callers crash.
- **C5. `LLMChat.call` TypeError when client returns None — CONFIRMED** (src/tinytroupe/utils/llm.py:553-555; `send_message` returns None on BadRequest/exhausted retries).
- **C6. Stale sidebar — CONFIRMED.** `_start_debate` doesn't reset `sidebar_*` keys, so the previous company's financials/chart/description display under the new debate for the whole fetch window (src/tinyic/ui/app.py:642-658,410-412).
- **C7. Mutable defaults in `TinyWorld.__init__` — CONFIRMED.** Shared `interventions=[]`; `initial_datetime=datetime.now()` evaluated at import → every memory's `simulation_timestamp` is kernel-start time (src/tinytroupe/environment/tiny_world.py:33-38).
- **C8. A fresh `httpx.Client` + `OpenAI` client is constructed per LLM call and never closed — RISK** (src/tinytroupe/clients/openai_client.py:214,117-132); FD growth in long-lived processes.
- **C9. Steering messages can be silently dropped — CONFIRMED.** The queue is drained only before each agent's turn (src/tinyic/debate/orchestrator.py:144); a message sent while the final VERDICT agent is acting is never delivered.

## D. Security / privacy

- **D1. Prompt injection surface — RISK (paths traced).** News titles, Grok's X/Twitter summaries, and web-search output are concatenated raw into every persona's context and into the memo synthesizer prompt, with no delimiting or "treat as data" framing (src/tinyic/data/news.py, social.py, research.py; src/tinyic/debate/prompts.py:36-40; memo.py:138-147). A crafted headline can address instructions to all six agents.
- **D2. Full prompts written to disk — CONFIRMED.** Fork default `LOGLEVEL_FILE=DEBUG` + a `start_logger` bug (`_root_level` missing from the `global` list, src/tinytroupe/utils/config.py:212-218) means every import creates `tinytroupe.<timestamp>.log` in the CWD containing complete LLM prompts (openai_client.py:398-400), including the financial data package. Also attaches a stdout handler to the *root* logger — third-party log bleed.
- **D3. Proxy endpoint committed to the repo** — see A3.

## E. Smaller correctness/polish items

- `to_context_string` budget is best-effort: news titles never truncated, no final hard cap, and `[:1500] + "..."` can *grow* short text (src/tinyic/data/models.py:85-100).
- Markdown export filename sanitizer misses `\n`, `\t`, `\r` (src/tinyic/export.py:11) — DOCX path handles them, Markdown path doesn't.
- Idle screen still says **"openIC"** (src/tinyic/ui/app.py:1058) — branding leftover; repo name "TineyIC" itself appears to be a typo of "TinyIC".
- `XAI_API_KEY` (X/Twitter sentiment) is required for the social source but undocumented in the README; deep research also silently dies if the selected chat model can't use `web_search_preview` (src/tinyic/data/research.py:60-66).
- Hardcoded "earnings 2026" in the research search query will rot (src/tinyic/data/research.py:117).
- DOCX is re-exported via pandoc subprocess on every Streamlit rerun of the results view (src/tinyic/ui/app.py:614-628).
- Import-time side effects: disclaimer print, full config dump, log file creation, `rich.jupyter` monkeypatch, cwd-dependent config discovery (src/tinytroupe/utils/config.py:118-128) — a notebook launched outside the repo root silently loses BASE_URL/MODEL settings and falls back to defaults.

## F. What's genuinely good (assets for the revamp)

1. **THINK-before-TALK is contractual.** The active system prompt mandates explicit reasoning via THINK actions plus a per-turn `cognitive_state` (goals/context/attention/emotions) (src/tinytroupe/agent/prompts/tiny_person.v2.mustache:98,162,179). The "watch each persona think" feature has guaranteed raw material — today it's simply discarded (`extract_talk_content` keeps TALK only, src/tinyic/ui/app.py:201-218).
2. **Streaming is half-plumbed.** The client already streams on the wire and collects chunks (`_collect_stream`); exposing incremental tokens to a UI is a contained change (src/tinytroupe/clients/openai_client.py:364-459).
3. **An event protocol already exists.** `on_phase_start`/`on_agent_start`/`on_agent_done` callbacks + steering queue + phase gate (src/tinyic/debate/orchestrator.py:62-69) map cleanly onto notebook widgets; there's even a seed ipywidgets chat widget in the fork (src/tinytroupe/ui/jupyter_widgets.py).
4. **Persona scholarship is real.** 9–13KB structured configs per investor plus a serious cross-investor differentiation document (src/tinyic/data/cross_investor_differentiation.md) and Buffett bibliography.
5. **Data pipeline degradation discipline** (per-source try/except with warnings) is mostly right — the exceptions are C3/C4.
6. **262 passing offline tests** with live tests cleanly separated behind `-m live_api`; extraction results are order-aligned with agents (zip is safe).

## Summary counts

- CONFIRMED: 26 (4 reproduced by execution in this review)
- RISK: 7
- The dominant defect class is **silent wrong output** (B1–B12): the app rarely crashes; it confidently shows wrong votes, wrong filings, wrong ratios, $0 costs, and truncated transcripts.
