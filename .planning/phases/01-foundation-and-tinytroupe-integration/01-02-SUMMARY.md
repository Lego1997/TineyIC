---
phase: 01-foundation-and-tinytroupe-integration
plan: 02
subsystem: infra
tags: [openai, gpt-5.2, openai-sdk-v2, reasoning-effort, smoke-test, streaming, proxy]

# Dependency graph
requires:
  - phase: 01-foundation-and-tinytroupe-integration
    plan: 01
    provides: "uv workspace with TinyTroupe v0.6.0 fork, InvestorPersona base class, GPT-5.2 patches"
provides:
  - "Validated GPT-5.2 live API call through TinyTroupe with reasoning_effort=xhigh"
  - "OpenAI SDK v2.29.0 confirmed compatible with TinyTroupe fork"
  - "Streaming support via _collect_stream() for proxy gateway compatibility"
  - "base_url config parameter for OpenAI client (proxy routing)"
  - "User-approved smoke test output demonstrating financial reasoning"
affects: [phase-2-personas, phase-4-debate]

# Tech tracking
tech-stack:
  added: []
  patterns: [streaming-response-collection, proxy-base-url-config, safe-pop-param-cleanup]

key-files:
  created: []
  modified:
    - src/tinytroupe/clients/openai_client.py
    - scripts/smoke_test.py
    - config.ini

key-decisions:
  - "Proxy gateway requires stream=True for all API calls; added _collect_stream() to reassemble chunks into ChatCompletion objects"
  - "base_url passed from config.ini to OpenAI client constructor for proxy routing"
  - "Safe .pop() used for reasoning model param cleanup instead of del (prevents KeyError on missing keys)"
  - "smoke_test.py uses pop_latest_actions() instead of act() return value (TinyTroupe stores actions internally)"

patterns-established:
  - "Pattern: PATCH(tinyIC) stream=True forced in _raw_model_call() for proxy compatibility"
  - "Pattern: _collect_stream() reassembles streaming chunks into a single ChatCompletion for downstream extractors"
  - "Pattern: config.ini BASE_URL field controls OpenAI client endpoint routing"

requirements-completed: [FOUND-03, FOUND-04]

# Metrics
duration: 21min
completed: 2026-03-20
---

# Phase 01 Plan 02: GPT-5.2 Live API Validation Summary

**Live GPT-5.2 call validated end-to-end through TinyTroupe fork with reasoning_effort=xhigh, streaming proxy support, and user-approved financial reasoning output**

## Performance

- **Duration:** 21 min
- **Started:** 2026-03-20T13:20:19Z
- **Completed:** 2026-03-20T13:41:35Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Audited _raw_model_call() and confirmed all GPT-5.2 compatibility patches from Plan 01 are correct (model detection, temperature removal, reasoning_effort passing, max_completion_tokens preservation)
- Verified config.ini loads correctly: MODEL=gpt-5.2, REASONING_EFFORT=xhigh, MAX_COMPLETION_TOKENS=128000
- Confirmed OpenAI SDK v2.29.0 compatibility with TinyTroupe fork (8 unit tests pass, SDK version assertion passes)
- Live API smoke test produced coherent value investing analysis about Apple (AAPL) via GPT-5.2 with reasoning_effort=xhigh -- user approved the output
- Proxy gateway compatibility achieved via stream=True and _collect_stream() (orchestrator patch a3730ef)

## Task Commits

Each task was committed atomically:

1. **Task 1: Verify and patch openai_client.py for GPT-5.2 compatibility** - No new commit (audit confirmed Plan 01 patches are correct; no code changes needed from executor)
2. **Task 1 (orchestrator supplement): Proxy gateway patches** - `a3730ef` (feat) -- stream=True, _collect_stream(), base_url config, safe .pop(), smoke_test.py fix
3. **Task 2: Verify live GPT-5.2 smoke test output** - User approved (checkpoint:human-verify)

## Files Created/Modified
- `src/tinytroupe/clients/openai_client.py` - Added stream=True forcing, _collect_stream() method for proxy, base_url from config, safe .pop() for param cleanup
- `scripts/smoke_test.py` - Fixed to use pop_latest_actions() for TinyTroupe action retrieval pattern
- `config.ini` - Added BASE_URL for proxy gateway routing

## Decisions Made
- **Proxy gateway requires streaming:** The API proxy does not support non-streaming requests. Added `stream=True` to all _raw_model_call() invocations and a `_collect_stream()` method that reassembles streaming chunks into a ChatCompletion object so all downstream extractors continue working unchanged.
- **base_url from config.ini:** Rather than hardcoding the proxy URL or using an environment variable, the base_url is read from config.ini's `[OpenAI]` section and passed conditionally to the OpenAI client constructor.
- **Safe .pop() for reasoning model params:** Changed `del chat_api_params["key"]` to `chat_api_params.pop("key", None)` to prevent KeyError when parameters are already absent (e.g., temperature might not be in params if it was None-filtered earlier).
- **pop_latest_actions() over act() return:** TinyTroupe's act() stores actions internally rather than returning them directly. The smoke test was updated to call pop_latest_actions() to retrieve the generated actions.

## Deviations from Plan

### Auto-fixed Issues (by orchestrator during checkpoint)

**1. [Rule 3 - Blocking] Proxy gateway rejects non-streaming requests**
- **Found during:** Task 1 checkpoint (live API smoke test attempt)
- **Issue:** The proxy gateway at api-vip.codex-for.me requires stream=True for all requests; non-streaming calls fail
- **Fix:** Force stream=True in _raw_model_call(), add _collect_stream() to reassemble chunks into ChatCompletion
- **Files modified:** src/tinytroupe/clients/openai_client.py
- **Verification:** Smoke test completes successfully through proxy
- **Committed in:** a3730ef

**2. [Rule 3 - Blocking] OpenAI client needs base_url for proxy routing**
- **Found during:** Task 1 checkpoint (live API smoke test attempt)
- **Issue:** Default OpenAI client points to api.openai.com; proxy gateway at different URL
- **Fix:** Read base_url from config.ini and pass to OpenAI() constructor
- **Files modified:** src/tinytroupe/clients/openai_client.py, config.ini
- **Verification:** Client connects to proxy successfully
- **Committed in:** a3730ef

**3. [Rule 1 - Bug] KeyError on del for missing parameters in reasoning model cleanup**
- **Found during:** Task 1 checkpoint (live API smoke test attempt)
- **Issue:** `del chat_api_params["stream"]` raises KeyError if stream was already removed by None-filtering
- **Fix:** Changed to safe `.pop(key, None)` for all reasoning model parameter removals
- **Files modified:** src/tinytroupe/clients/openai_client.py
- **Verification:** No KeyError during API calls
- **Committed in:** a3730ef

**4. [Rule 1 - Bug] smoke_test.py used act() return value instead of pop_latest_actions()**
- **Found during:** Task 1 checkpoint (live API smoke test attempt)
- **Issue:** TinyTroupe's act() stores actions internally; the return value does not match expected format
- **Fix:** Changed to call act() then pop_latest_actions() to retrieve actions
- **Files modified:** scripts/smoke_test.py
- **Verification:** Smoke test prints persona response correctly
- **Committed in:** a3730ef

---

**Total deviations:** 4 auto-fixed by orchestrator (2 blocking proxy issues, 2 bugs)
**Impact on plan:** All fixes necessary for live API validation through proxy gateway. No scope creep.

## Issues Encountered
- OPENAI_API_KEY was not set in .env during initial executor run; unit tests passed but live API tests were skipped. The orchestrator subsequently set the key and ran the smoke test during the checkpoint phase.
- The proxy gateway requirement (stream=True, base_url) was not anticipated in the plan but was handled cleanly via orchestrator patches.

## User Setup Required
- `.env` file must contain a valid `OPENAI_API_KEY` for live API tests and smoke test to work
- `config.ini` BASE_URL is set to the proxy gateway; change to `https://api.openai.com/v1` for direct OpenAI access

## Next Phase Readiness
- Phase 1 is fully complete: all 4 FOUND requirements validated
- InvestorPersona can call GPT-5.2 through TinyTroupe with reasoning_effort=xhigh and receive coherent responses
- OpenAI SDK v2.29.0 confirmed working with TinyTroupe fork
- Foundation is ready for Phase 2 (persona engineering) and Phase 3 (data pipeline), which can proceed in parallel
- Streaming support is built in, which will benefit Phase 5 (Streamlit real-time display)

## Self-Check: PASSED

All claimed files exist. Commit hash a3730ef verified. 6 PATCH(tinyIC) comments in openai_client.py. Config verified: MODEL=gpt-5.2, REASONING_EFFORT=xhigh, BASE_URL set.

---
*Phase: 01-foundation-and-tinytroupe-integration*
*Completed: 2026-03-20*
