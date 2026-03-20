# Phase 1: Foundation and TinyTroupe Integration - Research

**Researched:** 2026-03-20
**Domain:** Python project scaffolding, TinyTroupe multi-agent framework, OpenAI SDK integration
**Confidence:** HIGH

## Summary

Phase 1 establishes the tinyIC project skeleton as a uv workspace monorepo containing two sibling packages (`src/tinytroupe/` and `src/tinyic/`) and validates that a TinyPerson subclass can call GPT-5.2 with `reasoning_effort=xhigh` through the forked TinyTroupe framework.

TinyTroupe v0.6.0 (released 2026-02-02) already supports GPT-5 series models and has `REASONING_EFFORT` as a first-class config parameter in `config.ini`. The OpenAI Python SDK is at v2.29.0 (released 2026-03-17), and TinyTroupe's dependency constraint `openai >= 1.65` accepts v2.x. The v2.0.0 breaking change is narrow (function call output type broadened) and does not affect TinyTroupe's core chat completions usage. This means SDK compatibility risk -- originally rated HIGH -- is substantially lower than expected.

The main engineering work is: (1) structuring the uv workspace with two packages, (2) copying TinyTroupe v0.6.0 source into `src/tinytroupe/`, (3) configuring `config.ini` to use `gpt-5.2` with `reasoning_effort=xhigh`, (4) verifying that TinyTroupe's model detection logic in `openai_client.py` correctly handles GPT-5.2 with reasoning_effort (GPT-5.2 enforces `temperature=1.0` when reasoning is active), and (5) creating a minimal InvestorPersona subclass with a live smoke test.

**Primary recommendation:** Clone TinyTroupe v0.6.0 into `src/tinytroupe/`, configure it for GPT-5.2 with `reasoning_effort=xhigh`, and focus validation effort on the `_raw_model_call()` method in `tinytroupe/clients/openai_client.py` to ensure GPT-5.2 is handled correctly.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions
- Project renamed from "openIC" to **tinyIC** in honor of TinyTroupe
- Python package name: `tinyic` (lowercase, no separator)
- Imports: `from tinyic.personas.base import InvestorPersona`
- Full copy of TinyTroupe v0.6.0 into the repo (not minimal subset, not git subtree)
- Preserve original TinyTroupe module/class names (TinyPerson, TinyWorld, etc.) -- openIC code subclasses and imports from `tinytroupe`
- Keep and adapt TinyTroupe's existing test suite for regression safety during SDK patching
- Permanent diverging fork -- no intent to contribute patches upstream, free to modify aggressively
- Patch TinyTroupe's `openai_utils.py` directly for OpenAI SDK v2.x (no adapter/shim layer)
- Global default `reasoning_effort=xhigh`, with per-call override capability for future phases
- API key via `OPENAI_API_KEY` environment variable (TinyTroupe's existing convention)
- Validate compatibility with a live API smoke test (actual GPT-5.2 call, not mocks)
- Sibling packages under `src/`: `src/tinytroupe/` (fork) and `src/tinyic/` (application code)
- Domain-based module organization inside tinyic: `personas/`, `data/`, `debate/`, `ui/`
- Minimal config: `.env` for API keys + `constants.py` or `config.py` for defaults (model name, reasoning effort)
- No config framework (pydantic-settings etc.) -- add complexity only when needed in later phases
- uv package manager with Python 3.12, proper lockfile
- Minimal InvestorPersona subclass of TinyPerson with extension points for Phase 2
- Fields: `model` (gpt-5.2), `reasoning_effort` (xhigh), `philosophy` (loaded from config)
- Stub methods: `analyze_company(data_package)` and `format_vote()` -- raise NotImplementedError
- Philosophy stored in JSON config files following TinyTroupe's `.agent.json` pattern
- Test persona gives a simple financial opinion on a well-known company (e.g., Apple)

### Claude's Discretion
- Exact patching approach for openai_utils.py (depends on what TinyTroupe v0.6.0 actually does)
- Test file organization and naming conventions
- .gitignore setup and dev tooling (linting, formatting)
- pyproject.toml structure and dependency specification details

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FOUND-01 | Project uses Python 3.12 with uv package manager and proper directory structure | uv workspace docs confirm sibling package layout; `uv init --lib` creates src/ layout; single lockfile across workspace members |
| FOUND-02 | TinyTroupe v0.6.0 is forked and integrated as a local package in monorepo | TinyTroupe v0.6.0 exists (released 2026-02-02); module structure documented; agent.json format verified; clients/ module replaces old openai_utils.py |
| FOUND-03 | OpenAI GPT-5.2 integration works through TinyTroupe with reasoning_effort=xhigh | GPT-5.2 supports reasoning_effort values: none, low, medium, high, xhigh; TinyTroupe v0.6.0 has REASONING_EFFORT config parameter; openai_client.py handles reasoning models specially |
| FOUND-04 | OpenAI SDK v2.x compatibility with TinyTroupe fork is validated | TinyTroupe requires `openai >= 1.65`; SDK v2.0.0 breaking change is narrow (function call output type only); chat.completions.create API unchanged; reasoning_effort is a first-class parameter in SDK v2.x |

</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| TinyTroupe | 0.6.0 | Multi-agent persona simulation framework | Project is built as a fork of this; provides TinyPerson, TinyWorld, agent config patterns |
| openai | >=2.0.0 (current: 2.29.0) | OpenAI API client | Official Python SDK; TinyTroupe's dependency; chat.completions.create with reasoning_effort |
| uv | 0.10.12 | Python package/project manager | User-specified; handles workspace, lockfile, Python version management |
| Python | 3.12 | Runtime | User-specified; TinyTroupe requires >=3.10; 3.12 is stable and well-supported |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | latest | Load .env file for API keys | Development convenience; TinyTroupe uses OPENAI_API_KEY env var |
| pydantic | >=2.5.0 | Data validation | Already a TinyTroupe dependency; used for structured outputs |
| pytest | latest | Test framework | Smoke tests and regression testing |
| tiktoken | latest | Token counting | TinyTroupe dependency; useful for cost estimation |
| rich | latest | Console output formatting | TinyTroupe dependency; already available |

### TinyTroupe's Full Dependency Tree
These come with TinyTroupe v0.6.0 and must be preserved in the fork's pyproject.toml:
- pandas, scipy, matplotlib (data processing/analysis)
- httpx, requests (HTTP clients)
- chevron (Mustache templates for prompts)
- llama-index ecosystem (semantic memory -- may not be needed immediately but keep)
- pypandoc, python-docx, markdown (document processing)
- textdistance (similarity metrics)
- msal (Azure auth -- not needed but harmless)

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Full TinyTroupe copy | git subtree/submodule | Copy is simpler for aggressive modification; subtree adds git complexity |
| config.ini (TinyTroupe's) | pydantic-settings | config.ini works fine; pydantic-settings adds unnecessary dependency for Phase 1 |
| uv workspaces | pip + virtualenv | uv is faster, handles Python versions, single lockfile; user decision |

**Installation:**
```bash
# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Initialize project
uv init --lib tinyic
cd tinyic

# Python 3.12 will be managed by uv
uv python install 3.12
```

## Architecture Patterns

### Recommended Project Structure
```
tinyic/                          # Repository root
├── pyproject.toml               # Root workspace pyproject.toml
├── uv.lock                     # Single lockfile for workspace
├── .python-version             # "3.12"
├── .env                        # OPENAI_API_KEY (gitignored)
├── .gitignore
├── config.ini                  # TinyTroupe config (MODEL=gpt-5.2, REASONING_EFFORT=xhigh)
├── src/
│   ├── tinytroupe/             # Fork of TinyTroupe v0.6.0
│   │   ├── __init__.py
│   │   ├── agent.py            # TinyPerson class
│   │   ├── environment.py      # TinyWorld class
│   │   ├── extraction.py       # ResultsExtractor
│   │   ├── control.py          # Simulation control
│   │   ├── config.ini          # Default config (overridden by root config.ini)
│   │   ├── clients/
│   │   │   ├── __init__.py     # Client registry
│   │   │   ├── openai_client.py # OpenAI API calls (KEY FILE TO PATCH)
│   │   │   ├── azure_client.py
│   │   │   └── ollama_client.py
│   │   ├── examples/
│   │   │   └── agents/         # Example .agent.json files
│   │   ├── utils.py
│   │   ├── validation.py
│   │   ├── factory.py
│   │   ├── profiling.py
│   │   ├── enrichment.py
│   │   ├── steering.py
│   │   ├── tools.py
│   │   ├── ui.py
│   │   └── experimentation.py
│   └── tinyic/                 # Application code
│       ├── __init__.py
│       ├── constants.py        # MODEL="gpt-5.2", REASONING_EFFORT="xhigh"
│       ├── personas/
│       │   ├── __init__.py
│       │   ├── base.py         # InvestorPersona(TinyPerson)
│       │   └── configs/        # .agent.json files for each investor
│       ├── data/               # Phase 3: financial data pipeline (stub)
│       ├── debate/             # Phase 4: debate engine (stub)
│       └── ui/                 # Phase 5: Streamlit UI (stub)
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_investor_persona.py  # Phase 1 smoke tests
│   └── tinytroupe/               # Adapted TinyTroupe regression tests
└── scripts/
    └── smoke_test.py            # Live GPT-5.2 validation script
```

### Pattern 1: uv Workspace with Sibling Packages

**What:** Root pyproject.toml defines a workspace with two members (tinytroupe and tinyic). Each has its own pyproject.toml but shares a single lockfile.

**When to use:** When you need two importable packages in the same repo with shared dependencies.

**Example:**

Root `pyproject.toml`:
```toml
[project]
name = "tinyic-workspace"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv.workspace]
members = ["src/tinytroupe", "src/tinyic"]

[tool.uv.sources]
tinytroupe = { workspace = true }
tinyic = { workspace = true }
```

`src/tinytroupe/pyproject.toml`:
```toml
[project]
name = "tinytroupe"
version = "0.6.0"
requires-python = ">=3.12"
dependencies = [
    "openai>=2.0.0",
    "pydantic>=2.5.0",
    "pandas",
    "tiktoken",
    "rich",
    "chevron",
    "httpx",
    "scipy",
    # ... rest of TinyTroupe deps
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
```

`src/tinyic/pyproject.toml`:
```toml
[project]
name = "tinyic"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "tinytroupe",
    "python-dotenv",
]

[tool.uv.sources]
tinytroupe = { workspace = true }

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
```

### Pattern 2: TinyPerson Subclassing

**What:** InvestorPersona extends TinyPerson, adding investment-specific fields and stub methods.

**When to use:** Creating domain-specific agents that inherit TinyTroupe's conversation/LLM infrastructure.

**Example:**
```python
# src/tinyic/personas/base.py
from tinytroupe.agent import TinyPerson

class InvestorPersona(TinyPerson):
    """Base class for investor personas in tinyIC.

    Extends TinyPerson with investment-specific configuration
    and stub methods for Phase 2 implementation.
    """

    def __init__(
        self,
        name: str,
        philosophy_config_path: str | None = None,
        **kwargs,
    ):
        super().__init__(name=name, **kwargs)

        # Investment-specific defaults
        self._philosophy_config_path = philosophy_config_path

        # Load philosophy from .agent.json if provided
        if philosophy_config_path:
            self.load_specification(philosophy_config_path)

    def analyze_company(self, data_package: dict) -> dict:
        """Analyze a company from this investor's perspective.

        Implemented in Phase 2 with actual persona logic.
        """
        raise NotImplementedError(
            "analyze_company() will be implemented in Phase 2"
        )

    def format_vote(self) -> dict:
        """Format this investor's buy/hold/sell vote.

        Implemented in Phase 2 with structured output.
        """
        raise NotImplementedError(
            "format_vote() will be implemented in Phase 2"
        )
```

### Pattern 3: TinyTroupe Agent JSON Configuration

**What:** Personas are defined as `.agent.json` files following TinyTroupe's established pattern. The `load_specification()` method loads these into the agent.

**When to use:** Defining persona attributes (name, age, personality, beliefs, etc.) outside of Python code.

**Example:** (based on TinyTroupe's actual Lisa.agent.json format)
```json
{
  "type": "TinyPerson",
  "persona": {
    "name": "Warren Buffett",
    "age": 95,
    "gender": "Male",
    "nationality": "American",
    "residence": "Omaha, Nebraska, USA",
    "education": "University of Nebraska (BS), Columbia Business School (MS Economics) under Benjamin Graham",
    "long_term_goals": [
      "Allocate Berkshire Hathaway's capital to create long-term shareholder value",
      "Identify businesses with durable competitive advantages trading below intrinsic value"
    ],
    "occupation": {
      "title": "Chairman and CEO",
      "organization": "Berkshire Hathaway",
      "description": "Oversees a conglomerate of wholly-owned businesses and a portfolio of public equity investments. Known for patient, value-oriented capital allocation."
    },
    "style": "Folksy, plainspoken, uses homespun metaphors and analogies. Explains complex financial concepts in simple terms. Self-deprecating humor.",
    "personality": {
      "traits": ["Patient capital allocator", "Voracious reader", "Rational decision-maker", "Long-term thinker"],
      "big_five": {
        "openness": "Medium—intellectually curious but conservative in approach",
        "conscientiousness": "Very High—methodical, disciplined, follows process",
        "extraversion": "Medium—charming in public but prefers solitary reading",
        "agreeableness": "Medium—kind but unflinching in business decisions",
        "neuroticism": "Very Low—famously calm during market panics"
      }
    },
    "beliefs": [
      "Buy wonderful companies at fair prices rather than fair companies at wonderful prices",
      "Economic moats protect long-term profitability",
      "Mr. Market offers opportunities, not guidance",
      "Owner earnings matter more than reported earnings"
    ],
    "skills": ["Valuation analysis", "Capital allocation", "Reading financial statements", "Pattern recognition across industries"],
    "other_facts": [
      "Philosophy sources: Berkshire Hathaway Shareholder Letters (1965-2024), The Essays of Warren Buffett",
      "Key concepts: circle of competence, margin of safety, owner earnings, economic moat"
    ]
  }
}
```

### Pattern 4: TinyTroupe config.ini for Model Configuration

**What:** TinyTroupe reads model settings from `config.ini` in the working directory. Override the defaults to use GPT-5.2 with xhigh reasoning.

**Example:**
```ini
[OpenAI]
API_TYPE=openai
MODEL=gpt-5.2
REASONING_MODEL=gpt-5.2
REASONING_EFFORT=xhigh
MAX_COMPLETION_TOKENS=128000
TIMEOUT=480
MAX_ATTEMPTS=5
WAITING_TIME=0
EXPONENTIAL_BACKOFF_FACTOR=5
EMBEDDING_MODEL=text-embedding-3-small
CACHE_API_CALLS=False
CACHE_FILE_NAME=openai_api_cache.pickle
MAX_CONTENT_DISPLAY_LENGTH=1024

[Simulation]
PARALLEL_AGENT_GENERATION=True
PARALLEL_AGENT_ACTIONS=True
RAI_HARMFUL_CONTENT_PREVENTION=True
RAI_COPYRIGHT_INFRINGEMENT_PREVENTION=True

[Logging]
LOGLEVEL=ERROR
LOGLEVEL_CONSOLE=INFO
```

### Anti-Patterns to Avoid
- **Wrapping TinyPerson instead of subclassing:** InvestorPersona should `extend` TinyPerson, not wrap it. The user explicitly wants it to "feel like a natural extension."
- **Bypassing TinyTroupe's config system:** Do not hardcode model parameters in Python. Use `config.ini` (TinyTroupe's pattern) + `constants.py` (tinyIC's pattern) -- they serve different purposes.
- **Creating an adapter/shim for SDK compatibility:** The user explicitly rejected this. Patch `openai_client.py` directly.
- **Using git subtree/submodule for the fork:** User decided on full copy. Do not add git tracking complexity.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| LLM API calls | Custom OpenAI client wrapper | TinyTroupe's `clients/openai_client.py` | Already handles retries, backoff, caching, cost tracking, structured outputs |
| Agent conversation management | Custom message history | TinyPerson's episodic_memory | TinyTroupe manages conversation turns, memory, cognitive state |
| Config management | Custom config loader | TinyTroupe's ConfigManager | Singleton with file + programmatic override support |
| Token counting | Manual estimation | tiktoken (already a TinyTroupe dep) | Accurate BPE tokenization for cost tracking |
| Structured LLM output | JSON parsing from raw text | `client.beta.chat.completions.parse()` | TinyTroupe already uses pydantic models for structured extraction |
| Agent serialization | Custom save/load | TinyPerson.save_specification/load_specification | Handles JSON with full persona state |

**Key insight:** TinyTroupe v0.6.0 provides a complete infrastructure for LLM-powered agents. The goal of Phase 1 is to plug into this infrastructure correctly, not rebuild any of it.

## Common Pitfalls

### Pitfall 1: GPT-5.2 Temperature Restriction with Reasoning
**What goes wrong:** GPT-5.2 with `reasoning_effort` set to anything other than `none` does NOT support custom `temperature`, `top_p`, or `logprobs`. Requests including these parameters will raise an error.
**Why it happens:** GPT-5.2 enforces `temperature=1.0` when reasoning is active. TinyTroupe's `_raw_model_call()` already has GPT-5-specific logic that removes temperature, but needs verification for GPT-5.2.
**How to avoid:** After cloning TinyTroupe, inspect `openai_client.py` `_raw_model_call()` to confirm GPT-5.2 is caught by the GPT-5 detection logic. If the check looks for `"gpt-5"` prefix, `"gpt-5.2"` should match. If it checks exact strings, patch needed.
**Warning signs:** Errors like "temperature is not supported for this model with reasoning_effort != none."

### Pitfall 2: REASONING_EFFORT vs reasoning_effort API Mismatch
**What goes wrong:** TinyTroupe's config.ini uses `REASONING_EFFORT=high` as default. The OpenAI Chat Completions API parameter is `reasoning_effort` (top-level). The newer Responses API uses `reasoning={"effort": "high"}` (nested). Confusing these causes silent failures.
**Why it happens:** OpenAI has two API styles (Chat Completions and Responses). TinyTroupe uses Chat Completions.
**How to avoid:** Verify TinyTroupe uses `reasoning_effort="xhigh"` as a top-level parameter in `chat.completions.create()`, NOT the Responses API nested format. The default config value of `high` needs to be changed to `xhigh`.
**Warning signs:** No reasoning in responses; model acting like reasoning_effort=none.

### Pitfall 3: TinyTroupe Config Loading Order
**What goes wrong:** TinyTroupe searches for `config.ini` in: (1) current working directory, (2) script directory, (3) built-in defaults. If the working directory doesn't have `config.ini`, it falls back to built-in defaults (`gpt-5-mini`, `reasoning_effort=high`).
**Why it happens:** Python's `os.getcwd()` varies by how the script is invoked.
**How to avoid:** Place `config.ini` in the project root AND ensure scripts/tests are run from the project root (e.g., `uv run` from root). Alternatively, set config programmatically.
**Warning signs:** Unexpectedly using `gpt-5-mini` instead of `gpt-5.2`; wrong reasoning_effort level.

### Pitfall 4: uv Workspace Package Resolution
**What goes wrong:** When `tinyic` depends on `tinytroupe` as a workspace member, forgetting `[tool.uv.sources] tinytroupe = { workspace = true }` causes uv to search PyPI for `tinytroupe` instead of using the local workspace package.
**Why it happens:** uv defaults to PyPI resolution. Workspace sources must be explicitly declared.
**How to avoid:** Both the root pyproject.toml AND `src/tinyic/pyproject.toml` need the `[tool.uv.sources]` table pointing to the workspace.
**Warning signs:** `uv sync` errors about package not found on PyPI; wrong version installed.

### Pitfall 5: TinyTroupe's openai_utils.py vs clients/ Module Confusion
**What goes wrong:** The CONTEXT.md references "patching openai_utils.py" but TinyTroupe v0.6.0 refactored this into `tinytroupe/clients/` module. The file to patch is `tinytroupe/clients/openai_client.py`, not `openai_utils.py`.
**Why it happens:** Earlier TinyTroupe versions had `openai_utils.py` at the top level. v0.6.0 moved this to `clients/openai_client.py`.
**How to avoid:** After cloning, verify the module structure. Patch `clients/openai_client.py`. If `openai_utils.py` still exists as a compatibility shim, check its contents.
**Warning signs:** File not found when trying to edit `openai_utils.py`.

### Pitfall 6: OpenAI SDK v2.0 Function Call Output Type Change
**What goes wrong:** SDK v2.0.0 changed `ResponseFunctionToolCallOutputItem.output` from `string` to `string | Array<ResponseInputText | ResponseInputImage | ResponseInputFile>`. Code assuming `.output` is always a string may break.
**Why it happens:** v2.0.0 added image/file support for function call outputs.
**How to avoid:** Check if TinyTroupe's extraction or tool-handling code accesses `.output` on function call responses. For Phase 1 (no function calls), this is low risk. Flag for later phases.
**Warning signs:** TypeError when processing tool call outputs.

## Code Examples

### Smoke Test: InvestorPersona with GPT-5.2
```python
# scripts/smoke_test.py
"""Live smoke test: validate InvestorPersona -> listen() -> act() -> GPT-5.2 response."""
import os
from dotenv import load_dotenv

# Load API key
load_dotenv()
assert os.getenv("OPENAI_API_KEY"), "Set OPENAI_API_KEY in .env"

# TinyTroupe reads config.ini from cwd
from tinyic.personas.base import InvestorPersona

# Create a minimal test persona
persona = InvestorPersona(name="Test Investor")

# Define minimal persona attributes
persona["nationality"] = "American"
persona["occupation"] = {"title": "Value Investor", "organization": "Test Fund"}
persona["personality"] = {"traits": ["Analytical", "Patient", "Value-oriented"]}
persona["beliefs"] = [
    "Buy businesses below intrinsic value",
    "Margin of safety is essential",
]

# Test the listen -> act cycle
persona.listen("What do you think about Apple (AAPL) as an investment at current prices?")
actions = persona.act()

print(f"Persona responded with {len(actions)} action(s)")
for action in actions:
    print(f"  Action type: {action.get('type', 'unknown')}")
    if 'content' in action:
        print(f"  Content preview: {str(action['content'])[:200]}...")

print("\nSmoke test PASSED: InvestorPersona successfully called GPT-5.2 via TinyTroupe")
```

### Configuring TinyTroupe Programmatically
```python
# Alternative to config.ini: set model via ConfigManager
from tinytroupe import config_manager

config_manager.update("model", "gpt-5.2")
config_manager.update("reasoning_effort", "xhigh")
config_manager.update("max_completion_tokens", 128000)
```

### Loading Agent from JSON Specification
```python
# TinyTroupe's built-in pattern for loading persona configs
from tinytroupe.agent import TinyPerson

# Load from .agent.json file
agent = TinyPerson.load_specification("path/to/warren_buffett.agent.json")

# Can still modify after loading
agent["beliefs"].append("Never invest in what you don't understand")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| TinyTroupe `openai_utils.py` (single file) | `tinytroupe.clients/` module (openai, azure, ollama) | v0.6.0 (Feb 2026) | Client code is now modular; patch `openai_client.py` not `openai_utils.py` |
| OpenAI SDK v1.x `openai.ChatCompletion.create()` | v2.x `client.chat.completions.create()` | v2.0.0 (Sep 2025) | TinyTroupe already uses v1.x+ client pattern; v2.x is backward-compatible for chat completions |
| `reasoning_effort` not available | First-class parameter in chat.completions.create | OpenAI SDK ~v1.50+ | GPT-5.2 supports none/low/medium/high/xhigh; `xhigh` new in 5.2 |
| TinyTroupe default `gpt-4o-mini` | Default `gpt-5-mini` | v0.6.0 (Feb 2026) | Config.ini already has GPT-5 defaults; just change model name and effort |
| TinyTroupe `config.ini` only | ConfigManager with programmatic override | v0.5.x+ | Can set model params in code, not just config file |

**Deprecated/outdated:**
- `openai_utils.py` (top-level): Replaced by `clients/` module in v0.6.0
- `openai.ChatCompletion.create()` (class method style): Replaced by `client.chat.completions.create()` (instance method) since openai SDK v1.0
- GPT-4o models: Still work but GPT-5.2 is the target for this project

## Open Questions

1. **Exact model detection logic in `_raw_model_call()`**
   - What we know: The method has special handling for reasoning models (o1/o3) and GPT-5 -- removes temperature, adds reasoning_effort. From earlier analysis, GPT-5 is detected and `temperature=1.0` is enforced.
   - What's unclear: Does the detection check `model.startswith("gpt-5")` (which would match "gpt-5.2") or use an exact match list? Does it apply `reasoning_effort` from config for GPT-5.2 or only for o-series models?
   - Recommendation: After cloning TinyTroupe v0.6.0, read `_raw_model_call()` in `clients/openai_client.py` thoroughly. This is the single most important piece of code to understand before patching.

2. **TinyTroupe's `openai_utils.py` backward compatibility**
   - What we know: v0.6.0 moved code to `clients/`. The CONTEXT.md references "patching openai_utils.py."
   - What's unclear: Does `openai_utils.py` still exist as a compatibility import? Or was it completely removed?
   - Recommendation: After cloning, check if `tinytroupe/openai_utils.py` exists. If it's a thin wrapper around `clients/`, it can be ignored. If removed, the CONTEXT.md reference is outdated but the intent (patch the OpenAI integration) still applies to `clients/openai_client.py`.

3. **TinyTroupe test suite scope for regression testing**
   - What we know: The user wants to "keep and adapt TinyTroupe's existing test suite." TinyTroupe uses pytest with markers (examples, notebooks, core, slow, gpt41mini).
   - What's unclear: How many tests depend on actual API calls vs mocks? Can the test suite run with `gpt-5.2` without modification?
   - Recommendation: After cloning, audit tests. Mark API-dependent tests and separate them from pure unit tests. Some test markers (like `gpt41mini`) will need updating.

4. **Cost of `reasoning_effort=xhigh` for smoke testing**
   - What we know: GPT-5.2 pricing is $1.75/1M input, $14.00/1M output. `xhigh` uses more reasoning tokens.
   - What's unclear: How many tokens does a typical listen/act cycle consume with xhigh? Could be $0.05 or $0.50 per call.
   - Recommendation: Run the smoke test once and check token usage. Consider using `reasoning_effort=medium` for development/testing, `xhigh` only for final validation.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (latest, TinyTroupe's test dependency) |
| Config file | None yet -- Wave 0 creates pytest.ini or pyproject.toml [tool.pytest] |
| Quick run command | `uv run pytest tests/test_investor_persona.py -x -v` |
| Full suite command | `uv run pytest tests/ -v --timeout=120` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FOUND-01 | Project structure importable with Python 3.12 + uv | smoke | `uv run python -c "from tinyic.personas.base import InvestorPersona"` | No -- Wave 0 |
| FOUND-02 | TinyTroupe fork importable as local package | smoke | `uv run python -c "from tinytroupe.agent import TinyPerson"` | No -- Wave 0 |
| FOUND-03 | GPT-5.2 + reasoning_effort=xhigh produces response | integration (live API) | `uv run pytest tests/test_investor_persona.py::test_listen_act_gpt52 -x` | No -- Wave 0 |
| FOUND-04 | OpenAI SDK v2.x works with TinyTroupe without errors | integration (live API) | `uv run pytest tests/test_investor_persona.py::test_sdk_v2_compatibility -x` | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run python -c "from tinyic.personas.base import InvestorPersona"` (fast, no API call)
- **Per wave merge:** `uv run pytest tests/test_investor_persona.py -v --timeout=120` (includes live API test)
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/conftest.py` -- shared fixtures, API key validation, skip-if-no-key marker
- [ ] `tests/test_investor_persona.py` -- covers FOUND-01 through FOUND-04
- [ ] `pyproject.toml [tool.pytest.ini_options]` -- pytest configuration with timeout, markers
- [ ] `.env.example` -- template for required environment variables

## Sources

### Primary (HIGH confidence)
- [TinyTroupe GitHub releases](https://github.com/microsoft/TinyTroupe/releases) -- v0.6.0 confirmed (Feb 2, 2026), default model gpt-5-mini, REASONING_EFFORT config parameter
- [TinyTroupe default config.ini](https://raw.githubusercontent.com/microsoft/TinyTroupe/main/tinytroupe/config.ini) -- REASONING_EFFORT=high, REASONING_MODEL=o3-mini, MODEL=gpt-5-mini
- [TinyTroupe clients/ module](https://github.com/microsoft/TinyTroupe/tree/main/tinytroupe/clients) -- openai_client.py, azure_client.py, ollama_client.py, __init__.py (registry)
- [TinyTroupe agent.json format](https://raw.githubusercontent.com/microsoft/TinyTroupe/main/tinytroupe/examples/agents/Lisa.agent.json) -- full persona specification with persona.name, personality, beliefs, skills, etc.
- [TinyTroupe API docs](https://microsoft.github.io/TinyTroupe/api/tinytroupe/index.html) -- module structure with 15 submodules
- [OpenAI Python SDK PyPI](https://pypi.org/project/openai/) -- v2.29.0 (Mar 17, 2026), Python 3.9+
- [OpenAI SDK v2.0.0 release](https://github.com/openai/openai-python/releases/tag/v2.0.0) -- single breaking change: function call output type broadened
- [GPT-5.2 model page](https://developers.openai.com/api/docs/models/gpt-5.2) -- 400K context, 128K output, reasoning_effort: none/low/medium/high/xhigh, $1.75/$14.00 per 1M tokens
- [OpenAI Reasoning Guide](https://developers.openai.com/api/docs/guides/reasoning) -- reasoning_effort parameter details, Chat Completions vs Responses API
- [uv Workspace Docs](https://docs.astral.sh/uv/concepts/projects/workspaces/) -- workspace member configuration, [tool.uv.sources], single lockfile
- [uv Project Init Docs](https://docs.astral.sh/uv/concepts/projects/init/) -- --lib flag, src layout, pyproject.toml structure

### Secondary (MEDIUM confidence)
- [GPT-5 Compatibility Matrix](https://community.openai.com/t/request-for-compatibility-matrix-reasoning-effort-sampling-parameters-across-gpt-5-series/1371738) -- community-compiled parameter support by model (verified against official docs)
- [DeepWiki TinyTroupe analysis](https://deepwiki.com/liyu1981/TinyTroupe/2.1-installation-and-dependencies) -- ConfigManager singleton pattern, config loading order, default values
- [TinyTroupe openai_client.py behavior](https://github.com/microsoft/TinyTroupe/blob/main/tinytroupe/clients/openai_client.py) -- _raw_model_call() handles reasoning models (o1/o3), GPT-5 temperature enforcement, reasoning_effort from config (fetched via WebSearch descriptions, not direct code review)

### Tertiary (LOW confidence)
- TinyTroupe's exact model detection logic for GPT-5.2 in _raw_model_call() -- inferred from descriptions, needs verification after cloning

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - TinyTroupe v0.6.0 and OpenAI SDK v2.29.0 are verified with official sources; uv workspace pattern documented in official docs
- Architecture: HIGH - Project structure follows both uv workspace conventions and TinyTroupe's established patterns; agent.json format verified from actual example files
- Pitfalls: HIGH - GPT-5.2 parameter restrictions verified via official model docs; TinyTroupe module reorganization confirmed via GitHub; config loading order documented
- SDK compatibility: HIGH - v2.0.0 breaking change is narrow and does not affect chat completions; TinyTroupe's `openai >= 1.65` constraint accepts v2.x
- Model detection logic: LOW - Exact _raw_model_call() branching for GPT-5.2 needs verification post-clone

**Research date:** 2026-03-20
**Valid until:** 2026-04-20 (30 days -- stable domain; TinyTroupe and OpenAI SDK move slowly)
