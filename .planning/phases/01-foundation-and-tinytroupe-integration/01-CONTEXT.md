# Phase 1: Foundation and TinyTroupe Integration - Context

**Gathered:** 2026-03-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Establish the project skeleton for tinyIC (renamed from openIC) and validate that a TinyPerson subclass (InvestorPersona) can call GPT-5.2 through a forked TinyTroupe framework. This phase delivers: project structure, TinyTroupe fork integration, OpenAI SDK v2.x compatibility, and a working smoke test.

</domain>

<decisions>
## Implementation Decisions

### Project Rename
- Project renamed from "openIC" to **tinyIC** in honor of TinyTroupe
- Python package name: `tinyic` (lowercase, no separator)
- Imports: `from tinyic.personas.base import InvestorPersona`

### Fork Integration Strategy
- Full copy of TinyTroupe v0.6.0 into the repo (not minimal subset, not git subtree)
- Preserve original TinyTroupe module/class names (TinyPerson, TinyWorld, etc.) -- openIC code subclasses and imports from `tinytroupe`
- Keep and adapt TinyTroupe's existing test suite for regression safety during SDK patching
- Permanent diverging fork -- no intent to contribute patches upstream, free to modify aggressively

### SDK Compatibility Approach
- Patch TinyTroupe's `openai_utils.py` directly for OpenAI SDK v2.x (no adapter/shim layer)
- Global default `reasoning_effort=xhigh`, with per-call override capability for future phases
- API key via `OPENAI_API_KEY` environment variable (TinyTroupe's existing convention)
- Validate compatibility with a live API smoke test (actual GPT-5.2 call, not mocks)

### Project Layout & Packaging
- Sibling packages under `src/`: `src/tinytroupe/` (fork) and `src/tinyic/` (application code)
- Domain-based module organization inside tinyic: `personas/`, `data/`, `debate/`, `ui/`
- Minimal config: `.env` for API keys + `constants.py` or `config.py` for defaults (model name, reasoning effort)
- No config framework (pydantic-settings etc.) -- add complexity only when needed in later phases
- uv package manager with Python 3.12, proper lockfile

### InvestorPersona Base Class
- Minimal subclass of TinyPerson with extension points for Phase 2
- Fields: `model` (gpt-5.2), `reasoning_effort` (xhigh), `philosophy` (loaded from config)
- Stub methods: `analyze_company(data_package)` and `format_vote()` -- raise NotImplementedError, Phase 2 implements
- Philosophy stored in JSON config files following TinyTroupe's `.agent.json` pattern
- Phase 1 establishes the loading mechanism; Phase 2 creates the actual persona content

### Smoke Test
- Test persona gives a simple financial opinion on a well-known company (e.g., Apple)
- Validates full chain: TinyPerson subclass -> listen() -> GPT-5.2 with reasoning_effort=xhigh -> act() response

### Claude's Discretion
- Exact patching approach for openai_utils.py (depends on what TinyTroupe v0.6.0 actually does)
- Test file organization and naming conventions
- .gitignore setup and dev tooling (linting, formatting)
- pyproject.toml structure and dependency specification details

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### TinyTroupe Framework
- TinyTroupe v0.6.0 source code (to be cloned during implementation) -- core architecture: agent.py, environment.py, openai_utils.py, extraction.py
- TinyTroupe's existing `.agent.json` config pattern -- persona config file format to follow

### Project Requirements
- `.planning/REQUIREMENTS.md` -- FOUND-01 through FOUND-04 define Phase 1 acceptance criteria
- `.planning/ROADMAP.md` -- Phase 1 success criteria (4 specific validation checks)
- `.planning/PROJECT.md` -- Constraints and key decisions (tech stack, LLM provider, data sources)

### OpenAI SDK
- OpenAI Python SDK v2.x migration guide -- breaking changes from v1.x that affect TinyTroupe's openai_utils.py

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None -- greenfield project, no existing code

### Established Patterns
- TinyTroupe's `.agent.json` config pattern will be adopted for persona philosophy storage
- TinyTroupe's `listen()`/`act()` interaction pattern is the foundation for all persona behavior

### Integration Points
- `src/tinytroupe/` is the fork; `src/tinyic/` imports from it
- `tinyic.personas.base.InvestorPersona` subclasses `tinytroupe.agent.TinyPerson`
- OpenAI API calls flow through patched `tinytroupe.openai_utils`

</code_context>

<specifics>
## Specific Ideas

- Project renamed to "tinyIC" to honor TinyTroupe heritage
- InvestorPersona should feel like a natural extension of TinyPerson, not a wrapper
- JSON config structure for personas should include attributable source references (books, letters, speeches)

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 01-foundation-and-tinytroupe-integration*
*Context gathered: 2026-03-20*
