# Phase 8: Debate Quality Controls - Validation

**Phase:** 8
**Created:** 2026-03-23

## Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_debate.py tests/test_differentiation.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q --timeout=120` |

## Phase Requirements -> Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DEBT-04 | Anti-convergence reinforcement prompts injected per-agent before act() | unit | `uv run pytest tests/test_debate.py::TestAntiConvergence -x` | No -- Wave 0 |
| DEBT-04 | Reinforcement prompt contains persona-specific philosophy hook | unit | `uv run pytest tests/test_debate.py::TestAntiConvergence::test_reinforcement_contains_philosophy_hook -x` | No -- Wave 0 |
| DEBT-05 | Devil's advocate assigned during CROSS_EXAM only | unit | `uv run pytest tests/test_debate.py::TestDevilsAdvocate -x` | No -- Wave 0 |
| DEBT-05 | DA assignment rotates across agents | unit | `uv run pytest tests/test_debate.py::TestDevilsAdvocate::test_da_rotation -x` | No -- Wave 0 |
| DEBT-05 | DA prompt not present in VERDICT phase | unit | `uv run pytest tests/test_debate.py::TestDevilsAdvocate::test_da_not_in_verdict -x` | No -- Wave 0 |
| DEBT-05 | DA role release message sent at REBUTTAL start | unit | `uv run pytest tests/test_debate.py::TestDevilsAdvocate::test_da_role_release -x` | No -- Wave 0 |
| PERS-08 | Pairwise TF-IDF cosine similarity < 0.70 on recorded fixture | unit | `uv run pytest tests/test_differentiation.py::test_persona_differentiation_on_fixture -x` | No -- Wave 0 |
| PERS-08 | Similarity measurement function returns correct structure | unit | `uv run pytest tests/test_differentiation.py::test_measure_differentiation_structure -x` | No -- Wave 0 |

## Sampling Rate

- **Per task commit:** `uv run pytest tests/test_debate.py tests/test_differentiation.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q --timeout=120`
- **Phase gate:** Full suite green before `/gsd:verify-work`

## Wave 0 Gaps

- [ ] `tests/test_differentiation.py` -- covers PERS-08 (new file)
- [ ] `tests/fixtures/recorded_debate_apple.json` -- recorded debate fixture for offline testing
- [ ] New test classes in `tests/test_debate.py` -- TestAntiConvergence, TestDevilsAdvocate (extend existing file)
