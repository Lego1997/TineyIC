# Phase 2 Context: Persona Engineering

## Phase Goal
Six investor personas that produce demonstrably different analyses of the same company -- each grounded in their real-world investment philosophy.

## Requirements
- PERS-01: Warren Buffett persona (shareholder letters, interviews)
- PERS-02: Charlie Munger persona (speeches, Poor Charlie's Almanack)
- PERS-03: Benjamin Graham persona (Intelligent Investor, Security Analysis)
- PERS-04: Peter Lynch persona (One Up on Wall Street, Beating the Street)
- PERS-05: Howard Marks persona (investor memos, The Most Important Thing)
- PERS-06: Li Lu persona (Columbia lectures, shareholder letters)

## Success Criteria
1. Given the same company data, each persona produces analysis referencing their specific investment framework
2. A human reader can identify which persona wrote a given analysis without seeing the name
3. Each persona's philosophy stored as .agent.json following TinyTroupe's format
4. Each persona config includes attributable source references

## Key Constraints
- Use TinyTroupe's .agent.json format (proven in Phase 1)
- Use `InvestorPersona._load_philosophy()` + `include_persona_definitions()` (existing)
- `style` field is the #1 lever for differentiation (TinyTroupe template OVER-EMPHASIZES it)
- Anti-convergence: contrastive beliefs, negative constraints, signature vocabulary
- Highest overlap risk: Buffett-Munger and Graham-Buffett pairs
- 90% philosophical distillation, 10% code (registry + tests)
- No new libraries needed

## Technical Foundation (from Phase 1)
- InvestorPersona base class at `src/tinyic/personas/base.py`
- Test config at `src/tinyic/personas/configs/test_investor.agent.json`
- Test infrastructure: `tests/test_investor_persona.py` (8 unit + 2 live tests)
- Config: GPT-5.2, reasoning_effort=xhigh, proxy gateway with stream=True
