---
phase: 01-foundation-and-tinytroupe-integration
plan: 01
subsystem: infra
tags: [uv, tinytroupe, openai, gpt-5.2, python, monorepo]

# Dependency graph
requires:
  - phase: none
    provides: "Greenfield project"
provides:
  - "uv workspace with tinytroupe and tinyic sibling packages"
  - "TinyTroupe v0.6.0 fork with GPT-5.2 reasoning model patches"
  - "InvestorPersona(TinyPerson) base class with stub methods"
  - "Test infrastructure (pytest, conftest, unit tests)"
  - "config.ini targeting GPT-5.2 with reasoning_effort=xhigh"
  - "Smoke test script for live API validation"
affects: [01-02, phase-2-personas, phase-3-data, phase-4-debate, phase-5-ui]

# Tech tracking
tech-stack:
  added: [uv, tinytroupe-0.6.0, openai-sdk-2.x, pytest, python-dotenv, ipython]
  patterns: [uv-workspace-monorepo, tinyperson-subclassing, agent-json-config, config-ini-model-selection]

key-files:
  created:
    - pyproject.toml
    - config.ini
    - src/tinyic/personas/base.py
    - src/tinyic/personas/configs/test_investor.agent.json
    - src/tinyic/constants.py
    - tests/test_investor_persona.py
    - tests/conftest.py
    - scripts/smoke_test.py
  modified:
    - src/tinytroupe/__init__.py
    - src/tinytroupe/agent/memory.py
    - src/tinytroupe/agent/grounding.py
    - src/tinytroupe/clients/openai_client.py
    - src/tinytroupe/utils/llm.py

key-decisions:
  - "TinyPerson.load_specification() is a factory method returning new instances, so InvestorPersona uses _load_philosophy() with include_persona_definitions() to merge JSON config into existing instance"
  - "Patched _is_reasoning_model() to include gpt-5 models so reasoning_effort and temperature removal apply to GPT-5.2"
  - "Wrapped llama-index imports in try/except since llama-index has version incompatibilities; semantic memory features deferred"
  - "Added ipython to tinytroupe dependencies (required by experimentation module at import time)"
  - "Fixed max_completion_tokens deletion bug in _raw_model_call() for reasoning models"

patterns-established:
  - "Pattern: TinyPerson agent registry cleanup via all_agents.clear() in test fixtures"
  - "Pattern: Philosophy config loaded from .agent.json with name field stripped to avoid merge conflicts"
  - "Pattern: PATCH(tinyIC) comment convention for TinyTroupe fork modifications"

requirements-completed: [FOUND-01, FOUND-02]

# Metrics
duration: 66min
completed: 2026-03-20
---

# Phase 01 Plan 01: Project Scaffold and InvestorPersona Summary

**uv workspace monorepo with TinyTroupe v0.6.0 fork, InvestorPersona(TinyPerson) base class, GPT-5.2 reasoning model patches, and 8 passing unit tests**

## Performance

- **Duration:** 66 min
- **Started:** 2026-03-20T12:03:43Z
- **Completed:** 2026-03-20T13:10:15Z
- **Tasks:** 2
- **Files modified:** 112

## Accomplishments
- Established uv workspace monorepo with tinytroupe and tinyic as sibling packages, all dependencies resolving
- Cloned TinyTroupe v0.6.0 into src/tinytroupe/ and patched it for GPT-5.2 reasoning model support
- Created InvestorPersona base class that properly subclasses TinyPerson with philosophy config loading and stub methods
- Set up pytest infrastructure with 8 passing unit tests and 2 live API tests (skipped without key)
- Configured config.ini for GPT-5.2 with reasoning_effort=xhigh and max_completion_tokens=128000

## Task Commits

Each task was committed atomically:

1. **Task 1: Create uv workspace skeleton and clone TinyTroupe v0.6.0 fork** - `37b7ece` (feat)
2. **Task 2: Create InvestorPersona base class and test infrastructure** - `58e50f7` (feat)

## Files Created/Modified
- `pyproject.toml` - Root workspace config with uv workspace members and pytest settings
- `.python-version` - Python 3.12
- `.gitignore` - Standard Python ignores plus TinyTroupe log files
- `.env.example` - Template for OPENAI_API_KEY
- `config.ini` - TinyTroupe model config targeting GPT-5.2 with xhigh reasoning
- `src/tinytroupe/pyproject.toml` - TinyTroupe fork package definition with all dependencies
- `src/tinytroupe/__init__.py` - Patched: llama-index imports wrapped in try/except
- `src/tinytroupe/agent/memory.py` - Patched: llama-index import guarded
- `src/tinytroupe/agent/grounding.py` - Patched: llama-index imports guarded
- `src/tinytroupe/clients/openai_client.py` - Patched: gpt-5 added to _is_reasoning_model(), max_completion_tokens bug fixed
- `src/tinytroupe/utils/llm.py` - Fixed: invalid escape sequence SyntaxWarning
- `src/tinyic/__init__.py` - tinyIC package init with version
- `src/tinyic/pyproject.toml` - tinyIC package definition with workspace dependency on tinytroupe
- `src/tinyic/constants.py` - Default model config constants
- `src/tinyic/personas/__init__.py` - Exports InvestorPersona
- `src/tinyic/personas/base.py` - InvestorPersona(TinyPerson) with analyze_company() and format_vote() stubs
- `src/tinyic/personas/configs/test_investor.agent.json` - Test persona config for Phase 1 validation
- `src/tinyic/data/__init__.py` - Stub for Phase 3
- `src/tinyic/debate/__init__.py` - Stub for Phase 4
- `src/tinyic/ui/__init__.py` - Stub for Phase 5
- `tests/__init__.py` - Test package init
- `tests/conftest.py` - Shared fixtures: API key check, dotenv loading
- `tests/test_investor_persona.py` - 8 unit tests + 2 live API tests
- `scripts/smoke_test.py` - Standalone GPT-5.2 smoke test script

## Decisions Made
- **TinyPerson.load_specification() is a factory method:** It returns a new instance instead of modifying the current one. InvestorPersona uses a custom `_load_philosophy()` method that loads JSON and calls `include_persona_definitions()` to merge persona data into the existing instance.
- **GPT-5.2 treated as reasoning model:** Patched `_is_reasoning_model()` to include `"gpt-5"` prefix so that reasoning_effort is sent and incompatible params (temperature, stream, top_p) are removed.
- **llama-index deferred:** Version incompatibility between llama-index packages. Wrapped all imports in try/except. Semantic memory features are non-critical for Phase 1-4.
- **max_completion_tokens preserved for reasoning models:** Original code had a self-assignment followed by deletion (effectively a bug). Removed the deletion to preserve the parameter for GPT-5.2.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] llama-index version incompatibility blocks TinyTroupe import**
- **Found during:** Task 1 (uv sync and import verification)
- **Issue:** `llama_index.core.llms` missing `ChatMessage` export, causing ImportError on `from tinytroupe.agent import TinyPerson`
- **Fix:** Wrapped llama-index imports in try/except in `__init__.py`, `memory.py`, and `grounding.py`
- **Files modified:** src/tinytroupe/__init__.py, src/tinytroupe/agent/memory.py, src/tinytroupe/agent/grounding.py
- **Verification:** TinyPerson import succeeds with graceful warning
- **Committed in:** 37b7ece (Task 1 commit)

**2. [Rule 1 - Bug] SyntaxWarning from invalid escape sequence in utils/llm.py**
- **Found during:** Task 1 (import verification)
- **Issue:** Line 959 has `\{` which is invalid escape in Python 3.12
- **Fix:** Changed `\{` to `\\{` in the string literal
- **Files modified:** src/tinytroupe/utils/llm.py
- **Verification:** No SyntaxWarning on import
- **Committed in:** 37b7ece (Task 1 commit)

**3. [Rule 1 - Bug] GPT-5.2 not detected as reasoning model by _is_reasoning_model()**
- **Found during:** Task 2 (reading openai_client.py)
- **Issue:** `_is_reasoning_model()` only checks for "o1" and "o3", missing "gpt-5" models. GPT-5.2 with reasoning needs temperature removed and reasoning_effort added.
- **Fix:** Added `or "gpt-5" in model` to the detection condition
- **Files modified:** src/tinytroupe/clients/openai_client.py
- **Verification:** Config shows reasoning_effort=xhigh correctly applied
- **Committed in:** 58e50f7 (Task 2 commit)

**4. [Rule 1 - Bug] max_completion_tokens deleted for reasoning models**
- **Found during:** Task 2 (reading openai_client.py)
- **Issue:** Lines 371-374 assigned max_completion_tokens to itself then deleted it, effectively removing a required parameter
- **Fix:** Removed the self-assignment and deletion, keeping max_completion_tokens in params
- **Files modified:** src/tinytroupe/clients/openai_client.py
- **Verification:** Parameter preserved in API call params
- **Committed in:** 58e50f7 (Task 2 commit)

**5. [Rule 3 - Blocking] Missing IPython dependency causes import failure**
- **Found during:** Task 2 (running tests)
- **Issue:** `tinytroupe.experimentation.in_place_experiment_runner` imports IPython at module level, not in dependencies
- **Fix:** Added `ipython` to tinytroupe pyproject.toml dependencies
- **Files modified:** src/tinytroupe/pyproject.toml
- **Verification:** All tests pass after uv sync
- **Committed in:** 58e50f7 (Task 2 commit)

**6. [Rule 1 - Bug] TinyPerson agent name uniqueness causes test failures**
- **Found during:** Task 2 (running tests)
- **Issue:** TinyPerson registers agents globally by name. Multiple tests creating agents with same name causes ValueError.
- **Fix:** Added autouse fixture to clear `TinyPerson.all_agents` between tests, used unique names per test
- **Files modified:** tests/test_investor_persona.py
- **Verification:** All 8 unit tests pass
- **Committed in:** 58e50f7 (Task 2 commit)

**7. [Rule 1 - Bug] Philosophy config merge fails on "name" field conflict**
- **Found during:** Task 2 (running tests)
- **Issue:** `include_persona_definitions()` uses `merge_dicts()` which raises on scalar conflicts. The JSON config's "name" field conflicts with the already-set agent name.
- **Fix:** Strip "name" from persona data before merging in `_load_philosophy()`
- **Files modified:** src/tinyic/personas/base.py
- **Verification:** test_load_philosophy_config passes
- **Committed in:** 58e50f7 (Task 2 commit)

---

**Total deviations:** 7 auto-fixed (3 bugs in TinyTroupe fork, 2 blocking dependency issues, 1 test isolation issue, 1 config merge issue)
**Impact on plan:** All auto-fixes necessary for correctness and ability to complete tasks. No scope creep.

## Issues Encountered
- TinyTroupe v0.6.0 git clone was very slow (~19MB repo with large PDF files). Switched to downloading zip archive, which also truncated. Eventually succeeded with `--filter=blob:limit=1m` clone approach.
- TinyTroupe v0.6.0 has a significantly different module structure than the plan assumed (agent is a subpackage with tiny_person.py, not a single agent.py file). Adapted accordingly.
- setuptools flat-layout detection failed for both packages. Fixed with explicit `[tool.setuptools]` packages and package-dir configuration.

## User Setup Required
None for this plan. The OpenAI API key is needed for Plan 02 (live API smoke test).

## Next Phase Readiness
- Both packages are importable and all dependencies resolve
- InvestorPersona is ready to be extended in Phase 2
- GPT-5.2 patches are in place; live API validation happens in Plan 02
- Smoke test script is ready for Plan 02's human verification checkpoint
- Test infrastructure is established for all future phases

## Self-Check: PASSED

All claimed files exist. All commit hashes verified. 8/8 unit tests pass.

---
*Phase: 01-foundation-and-tinytroupe-integration*
*Completed: 2026-03-20*
