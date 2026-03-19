# Technology Stack

**Project:** openIC -- AI-Powered Investment Committee Simulator
**Researched:** 2026-03-20

## Recommended Stack

### Python Runtime

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.12.x | Runtime | Best balance of library compatibility and modern features. 3.13+ is too new for some scientific/financial packages (scipy, llama-index). TinyTroupe requires >=3.10; Streamlit requires >=3.10. Python 3.12 is the safe, well-tested sweet spot. |

**Confidence:** HIGH -- verified against TinyTroupe, Streamlit, and yfinance requirements on PyPI.

### Package Management

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| uv | >=0.10 | Package manager, venv, lockfile | 10-100x faster than pip. Single binary replaces pip + venv + pip-tools. Native pyproject.toml support. Industry momentum in 2026 -- the default choice for new Python projects. |

**Confidence:** HIGH -- uv is the consensus recommendation for new projects in 2026 per multiple independent sources.

Do NOT use: Poetry (slower, heavier, library-publishing oriented), pip + requirements.txt (no lockfile, no reproducibility), conda (overkill, environment conflicts with pip packages).

### Core Framework (Multi-Agent Simulation)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| TinyTroupe (fork) | 0.6.0 (base) | Multi-agent persona simulation engine | Explicit project requirement. Provides TinyPerson agents, TinyWorld environments, parallel agent execution, action correction, and cost tracking. Fork enables deep customization for investment debate flow without fighting upstream API. |

**Confidence:** HIGH -- v0.6.0 confirmed as latest release (Feb 2, 2026) via GitHub releases page.

Key TinyTroupe dependencies that come along for the ride:
- `openai >= 1.65` -- LLM API client (aligns with our OpenAI requirement)
- `pydantic >= 2.5.0` -- Data validation
- `tiktoken` -- Token counting
- `rich` -- Terminal formatting
- `chevron` -- Mustache templating (used for persona prompts)
- `llama-index` + embedding packages -- Document indexing (may not be needed for v1 curated-prompt approach, but comes with TinyTroupe)
- `pandas`, `matplotlib` -- Data manipulation and visualization

**Fork strategy:** Clone the repo at tag v0.6.0. Maintain as a local package within the monorepo rather than as a git submodule. This allows direct modification of agent.py, environment.py, and the interaction loop without submodule friction.

### LLM Provider

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| OpenAI API via `openai` SDK | SDK >=2.29.0 | LLM inference for agent personas | Project constraint: OpenAI only. SDK v2.x required by latest ecosystem (TinyTroupe already pins openai >= 1.65, but the SDK is at 2.29.0 -- need to verify TinyTroupe compatibility with v2.x). |
| Model: `gpt-5.2` | -- | Primary reasoning model | 400K context window, 128K max output tokens. Supports function calling, structured outputs, reasoning effort levels (none/low/medium/high/xhigh). Priced at $1.75/1M input, $14/1M output. Good for complex multi-persona financial reasoning. |
| Model: `gpt-5.2-mini` (fallback) | -- | Cost-efficient fallback | For development/testing. TinyTroupe v0.6.0 defaults to gpt-5-mini. Use mini for iteration, full gpt-5.2 for production-quality debates. |

**Confidence:** HIGH for SDK version (verified PyPI). MEDIUM for model names -- `gpt-5.2` confirmed via OpenAI docs, but the PROJECT.md mentions "GPT 5.2 extra high / Codex 5.3 extra high" which likely refers to reasoning effort level `xhigh` rather than a separate model. The API call would be `model="gpt-5.2"` with `reasoning_effort="high"` or `"xhigh"` parameter.

**Important note on Codex 5.3:** GPT-5.3-Codex is a coding-specialized model (announced Feb 2026). It is optimized for code generation, NOT financial analysis or debate simulation. Use `gpt-5.2` for the investment committee personas. Codex 5.3 would only be useful if building code-generation features, which openIC does not need.

**OpenAI SDK v1 vs v2 compatibility warning:** TinyTroupe 0.6.0 pins `openai >= 1.65`. The current SDK is at 2.29.0. The OpenAI Agents SDK explicitly requires v2.x. You MUST verify that TinyTroupe's fork works with openai v2.x or pin to the latest 1.x release. This is a potential compatibility issue that needs early validation.

### UI Framework

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Streamlit | >=1.55.0 | Web UI for debate view, inputs, outputs | Project constraint. Python-native, fast to build. Built-in `st.chat_message` and `st.chat_input` components are perfect for the debate transcript UI. `st.write_stream` enables real-time streaming of agent responses. `st.session_state` maintains debate history across reruns. |

**Confidence:** HIGH -- v1.55.0 confirmed on PyPI (released March 3, 2026). Chat components well-documented.

Key Streamlit components for openIC:
- `st.chat_message` -- Display investor persona messages with custom avatars
- `st.chat_input` -- User can steer the debate conversation
- `st.write_stream` -- Stream agent responses in real-time
- `st.session_state` -- Maintain debate state, history, scores
- `st.tabs` -- Organize debate view, scorecard, memo sections
- `st.columns` / `st.metric` -- Display financial data and vote tallies
- `st.download_button` -- Export investment memo as PDF/markdown

### Financial Data

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| yfinance | >=1.2.0 | Stock prices, financials, fundamentals | Free, no API key needed. Covers historical prices, income statements, balance sheets, cash flow, analyst estimates, institutional holders. The de facto standard for free financial data in Python. |
| edgartools | latest (>=3.x) | SEC EDGAR filings | Free, no API key, no rate limits. Parses 10-K, 10-Q, 8-K, proxy statements into typed Python objects with XBRL support. Better than sec-edgar-downloader (gives raw files) or sec-api (requires paid API key). MIT licensed. |

**Confidence:** HIGH for yfinance (v1.2.0 verified on PyPI, Feb 2026). MEDIUM for edgartools (confirmed on PyPI, actively maintained, but version number not precisely confirmed -- latest release Mar 13, 2026).

Do NOT use: `sec-api` (paid API, violates free-only constraint), `alpha_vantage` (rate-limited free tier, inferior data), `polygon.io` (paid).

### News Data

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| NewsAPI (`newsapi-python`) | latest | Recent news for company context | Free tier for development. 100 requests/day on free plan. Covers headlines and articles. Simple Python client. |
| GNews API (fallback) | -- | Alternative news source | Free for non-commercial/academic use. 80,000+ sources. Use as backup if NewsAPI rate limits are hit. |

**Confidence:** MEDIUM -- NewsAPI free tier is confirmed but limited (100 req/day, 1-month history). For v1 single-user tool, this is sufficient. The free tier restriction means production scaling would need a paid plan or alternative.

**Alternative approach:** yfinance itself provides basic news via `Ticker.news` property. This may be sufficient for v1 without adding a separate news API dependency. Start with yfinance news; add NewsAPI only if richer news context is needed.

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | >=2.5.0 | Data models for personas, scores, memos | Always. Type-safe structured data. Already a TinyTroupe dependency. |
| rich | latest | Terminal output formatting | Development/debugging. Already a TinyTroupe dependency. |
| tiktoken | latest | Token counting for cost estimation | Track per-debate API costs. Already a TinyTroupe dependency. |
| python-dotenv | latest | Environment variable management | Store OpenAI API key. Standard practice. |
| pytest | latest | Testing | All phases. Already a TinyTroupe dependency. |

### Development Tools

| Tool | Purpose | Why |
|------|---------|-----|
| ruff | Linting + formatting | Single tool replaces flake8 + black + isort. Fastest Python linter (written in Rust). 2026 standard. |
| mypy | Type checking | Catch type errors in financial calculations. Use with pydantic for model validation. |
| pre-commit | Git hooks | Enforce ruff + mypy on every commit. |

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Agent framework | TinyTroupe (fork) | LangChain/LangGraph | Project requirement is TinyTroupe fork. LangChain adds massive dependency bloat and abstraction overhead for what TinyTroupe already handles natively. |
| Agent framework | TinyTroupe (fork) | CrewAI | CrewAI focuses on task-oriented agents, not persona simulation. TinyTroupe's persona system (TinyPerson) is purpose-built for character simulation with distinct personalities. |
| Agent framework | TinyTroupe (fork) | AutoGen | Microsoft's other agent framework is enterprise-oriented with complex setup. TinyTroupe is lighter and persona-focused. |
| UI | Streamlit | Gradio | Streamlit has superior chat components and better layout control. Gradio is better for ML model demos, not multi-panel debate UIs. |
| UI | Streamlit | FastAPI + React | Massive complexity increase for v1. Streamlit gets us to working UI in hours, not weeks. Upgrade path exists for v2 if needed. |
| Financial data | yfinance | pandas-datareader | yfinance is more actively maintained, richer API (fundamentals, not just prices), better error handling. |
| SEC data | edgartools | sec-edgar-downloader | edgartools gives parsed, typed Python objects. sec-edgar-downloader gives raw files you have to parse yourself. |
| Package manager | uv | Poetry | uv is 10-100x faster, simpler (single binary), growing ecosystem dominance in 2026. Poetry adds unnecessary complexity for an application (vs library). |
| LLM model | gpt-5.2 | gpt-5.4 | gpt-5.4 is newer but more expensive and potentially overkill for this use case. gpt-5.2 at xhigh reasoning effort is well-suited for financial analysis. If cost is not a concern, gpt-5.4 is a viable upgrade. |
| LLM model | gpt-5.2 | Claude (Anthropic) | Project constraint: OpenAI only. |

## Version Compatibility Matrix

| Package | Min Version | Tested With | Python |
|---------|-------------|-------------|--------|
| TinyTroupe | 0.6.0 | 0.6.0 | >=3.10 |
| openai | >=1.65 (TinyTroupe req) | 2.29.0 (latest) | >=3.9 |
| streamlit | 1.55.0 | 1.55.0 | >=3.10 |
| yfinance | 1.2.0 | 1.2.0 | >=3.6 |
| edgartools | latest | latest | >=3.10 |
| pydantic | >=2.5.0 | latest | >=3.8 |

**Common denominator: Python >=3.10.** Recommended: Python 3.12.x for maximum compatibility and stability.

## Installation

```bash
# Initialize project with uv
uv init openIC --python 3.12
cd openIC

# Core dependencies
uv add openai streamlit yfinance edgartools pydantic python-dotenv tiktoken rich chevron

# TinyTroupe fork (install from local path after forking)
uv add --editable ./tinytroupe

# News API (optional, start with yfinance news first)
uv add newsapi-python

# Dev dependencies
uv add --dev pytest pytest-cov ruff mypy pre-commit

# Note: TinyTroupe brings llama-index, pandas, matplotlib, etc.
# These come transitively and don't need explicit installation.
```

## Project Structure Recommendation

```
openIC/
  pyproject.toml          # uv-managed project config
  uv.lock                 # Lockfile for reproducibility
  .env                    # OPENAI_API_KEY (gitignored)
  .python-version         # Pin to 3.12
  tinytroupe/             # Forked TinyTroupe as local package
    pyproject.toml
    tinytroupe/
      agent.py            # Customized for investor personas
      environment.py      # Customized for debate environment
      ...
  src/
    openic/
      personas/           # Investor persona definitions
      data/               # Financial data fetching layer
      debate/             # Debate orchestration logic
      scoring/            # Scorecard and memo generation
      ui/                 # Streamlit app
  tests/
```

## Sources

- TinyTroupe releases: https://github.com/microsoft/TinyTroupe/releases (v0.6.0 confirmed)
- TinyTroupe pyproject.toml: https://github.com/microsoft/TinyTroupe/blob/main/pyproject.toml
- OpenAI Python SDK: https://pypi.org/project/openai/ (v2.29.0)
- GPT-5.2 model docs: https://developers.openai.com/api/docs/models/gpt-5.2
- GPT-5.2 pricing: https://developers.openai.com/api/docs/pricing
- Streamlit: https://pypi.org/project/streamlit/ (v1.55.0)
- Streamlit chat elements: https://docs.streamlit.io/develop/api-reference/chat
- yfinance: https://pypi.org/project/yfinance/ (v1.2.0)
- edgartools: https://github.com/dgunning/edgartools
- edgartools docs: https://edgartools.readthedocs.io/
- uv package manager: https://docs.astral.sh/uv/
- Python version support: https://devguide.python.org/versions/
- NewsAPI: https://newsapi.org/
