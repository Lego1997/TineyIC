---
phase: 1
slug: foundation-and-tinytroupe-integration
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-20
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (latest, TinyTroupe's test dependency) |
| **Config file** | None yet — Wave 0 creates pyproject.toml [tool.pytest] |
| **Quick run command** | `uv run pytest tests/test_investor_persona.py -x -v` |
| **Full suite command** | `uv run pytest tests/ -v --timeout=120` |
| **Estimated runtime** | ~30 seconds (smoke) / ~120 seconds (with live API) |

---

## Sampling Rate

- **After every task commit:** Run `uv run python -c "from openic.personas.base import InvestorPersona"` (fast, no API call)
- **After every plan wave:** Run `uv run pytest tests/ -v --timeout=120`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 01-01-01 | 01 | 1 | FOUND-01 | smoke | `uv run python -c "from openic.personas.base import InvestorPersona"` | ❌ W0 | ⬜ pending |
| 01-01-02 | 01 | 1 | FOUND-02 | smoke | `uv run python -c "from tinytroupe.agent import TinyPerson"` | ❌ W0 | ⬜ pending |
| 01-02-01 | 02 | 2 | FOUND-03 | integration (live API) | `uv run pytest tests/test_investor_persona.py::test_listen_act_gpt52 -x` | ❌ W0 | ⬜ pending |
| 01-02-02 | 02 | 2 | FOUND-04 | integration (live API) | `uv run pytest tests/test_investor_persona.py::test_sdk_v2_compatibility -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — shared fixtures, API key validation, skip-if-no-key marker
- [ ] `tests/test_investor_persona.py` — covers FOUND-01 through FOUND-04
- [ ] `pyproject.toml [tool.pytest.ini_options]` — pytest configuration with timeout, markers
- [ ] `.env.example` — template for required environment variables

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| GPT-5.2 response quality | FOUND-03 | Subjective output quality | Run `listen()`/`act()` cycle, inspect response for coherence |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
