# Phase 8: Debate Quality Controls - Context

**Phase:** 8
**Name:** Debate Quality Controls
**Goal:** Personas maintain distinct positions throughout multi-round debates, with at least one structurally dissenting voice during cross-examination

## Requirements

| ID | Description | Priority |
|----|-------------|----------|
| DEBT-04 | Anti-convergence controls preventing opinion collapse during multi-round debates (system prompt re-injection already exists via reset_prompt() -- this needs additional structural controls) | MUST |
| DEBT-05 | Rotating devil's advocate role during cross-examination phase with unconstrained final votes | MUST |
| PERS-08 | Lightweight automated differentiation regression tests detecting convergence on fixed company fixtures (PERS-08-lite scope) | MUST |

## Success Criteria

1. Anti-convergence controls are active during debate: the orchestrator applies a mechanism (e.g., persona-specific reinforcement prompts, contrastive framing) that prevents opinions from collapsing to unanimous agreement
2. During the cross-examination phase, one persona is assigned a rotating devil's advocate role that argues the strongest counter-position to the emerging consensus
3. The devil's advocate assignment rotates and does not constrain final votes -- personas vote independently in the verdict phase
4. An automated differentiation regression test exists that runs a debate on a fixed company fixture and asserts that persona analyses remain distinct (e.g., no two personas share >70% vocabulary overlap in key reasoning)
5. The convergence detection test can be run as part of the standard pytest suite without API keys (using mocked responses or recorded fixtures)

## Dependencies

- Phase 7 (Release Hardening) -- COMPLETE: clean test baseline with 160 tests passing

## Key Decisions

- Persona re-injection already exists via TinyPerson.reset_prompt() -- DEBT-04 needs structural anti-convergence, not re-injection
- Rotating devil's advocate over forced bears -- confine dissent to cross-exam mechanics; keep final votes unconstrained for authenticity
- Hard-code philosophy hooks for 6 known personas (simpler, precise phrasing)
- Round-robin DA rotation (deterministic, testable, satisfies "rotating" requirement)
- TF-IDF cosine similarity for differentiation testing (scikit-learn already installed)
- DA role release message at REBUTTAL start to prevent memory contamination

## Key Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Structural dissent damages persona authenticity | MODERATE | Confine DA to cross-exam role; keep final vote unconstrained; clear role release message |
| Reinforcement prompt overwhelms persona identity | LOW | Keep reinforcement short (2-3 sentences); focus on reminding, not restating |
| DA prompt persists in episodic memory past cross-exam | HIGH | Explicit role release counter-stimulus via listen() at REBUTTAL start |
| Differentiation threshold too strict/loose | MODERATE | Start at 0.70, calibrate against fixture; add complementary unanimous-vote check |

## Architectural Context

### Files to Modify
- `src/tinyic/debate/orchestrator.py` -- add anti-convergence + DA logic to _step()
- `src/tinyic/debate/prompts.py` -- add reinforcement template, DA prompt, role release prompt, philosophy hooks

### Files to Create
- `tests/test_differentiation.py` -- PERS-08-lite convergence detection tests
- `tests/fixtures/recorded_debate_apple.json` -- recorded debate fixture for offline testing

### Files to Extend
- `tests/test_debate.py` -- new TestAntiConvergence and TestDevilsAdvocate test classes

### Key APIs (TinyTroupe)
- `agent.listen(speech)` -- inject stimulus into episodic memory
- `agent.internalize_goal(goal)` -- inject internal goal stimulus
- `agent.act(return_actions=True)` -- generate response with actions
- `reset_prompt()` -- regenerate system message from template + memory (runs every act() call)
- `broadcast_internal_goal(prompt)` -- broadcast goal to all agents

## Source Material
- Research: `.planning/phases/08-debate-quality-controls/08-RESEARCH.md`
- Roadmap: `.planning/ROADMAP.md` (Phase 8 section)
- Requirements: `.planning/REQUIREMENTS.md` (DEBT-04, DEBT-05, PERS-08)
