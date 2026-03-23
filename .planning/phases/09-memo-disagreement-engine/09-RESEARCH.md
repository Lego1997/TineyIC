# Phase 9: Memo & Disagreement Engine - Research

**Researched:** 2026-03-23
**Domain:** LLM-based document synthesis, cross-agent disagreement extraction, Markdown/DOCX export
**Confidence:** HIGH

## Summary

Phase 9 adds three capabilities: (1) a full narrative investment memo synthesized from debate transcript + scorecard + data package via LLM, (2) cross-persona disagreement extraction identifying where investors diverged most, and (3) Markdown/DOCX export for all artifacts. The existing codebase provides strong foundations: `DebateResult` already contains the transcript, scorecard, and phases_completed; `client().send_message()` provides direct LLM access for synthesis; `extract_json()` handles parsing; and `ArtifactExporter` already supports DOCX export via pypandoc (both pypandoc 1.17 and pandoc are installed on this system).

The core architectural insight is that memo generation and disagreement extraction are **post-debate LLM synthesis tasks**, not per-agent extraction tasks. TinyTroupe's `ResultsExtractor` operates per-agent and cannot synthesize across multiple agents. Instead, we call `client().send_message()` directly with a carefully structured prompt containing the full debate context, and parse the structured JSON response via `extract_json()`. This follows the same pattern used by `TinyEnricher` for content enrichment.

**Primary recommendation:** Create a `MemoGenerator` class in `memo.py` that takes `DebateResult` + `DataPackage`, makes two LLM calls (one for memo synthesis, one for disagreement extraction), and returns `InvestmentMemo` + `DisagreementAnalysis` Pydantic models. Export is handled by a separate `ExportManager` class in `export.py` that wraps `ArtifactExporter` for DOCX and implements direct file writing for Markdown.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| OUTP-03 | Full narrative investment memo with structured sections synthesized via LLM | `client().send_message()` for synthesis; structured JSON output parsed via `extract_json()`; Pydantic models for InvestmentMemo/MemoSection |
| OUTP-04 | Cross-persona disagreement extraction with evidence quotes | Separate LLM call analyzing transcript for divergence dimensions; evidence quotes extracted inline |
| OUTP-05 | Markdown + DOCX export with graceful fallback | Direct file write for Markdown; ArtifactExporter wrapping pypandoc for DOCX; pandoc availability check |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pydantic | (installed) | InvestmentMemo, MemoSection, DisagreementAnalysis models | Already the project standard for data models |
| tinytroupe.clients | (installed) | `client().send_message()` for LLM synthesis calls | TinyTroupe's built-in OpenAI client with cost tracking |
| tinytroupe.utils | (installed) | `extract_json()` for parsing structured LLM output | Already used in ResultsExtractor; handles messy LLM JSON |
| pypandoc | 1.17 | Markdown → HTML → DOCX conversion | Already installed; used by ArtifactExporter |
| markdown | (installed) | Markdown → HTML intermediate conversion | Already imported by ArtifactExporter |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pathlib | stdlib | File path handling for export | Export file management |
| shutil | stdlib | `shutil.which('pandoc')` for pandoc availability check | DOCX export gating |
| json | stdlib | JSON serialization for export metadata | Export helpers |
| logging | stdlib | Structured logging for generation/export | Error handling |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Direct `client().send_message()` | ResultsExtractor.extract_results_from_world() | ResultsExtractor uses world interaction history which is available, but its Mustache template is designed for per-entity extraction, not cross-agent synthesis with specific section structure |
| Two LLM calls (memo + disagreement) | Single combined LLM call | Combined call risks worse quality on both; separate prompts allow each to be optimized independently |
| ArtifactExporter for DOCX | Direct pypandoc call | ArtifactExporter handles path composition and directory creation, but its API is more complex than needed; we can wrap it cleanly |
| Direct file write for Markdown | ArtifactExporter for Markdown | ArtifactExporter uses `utils.dedent()` which strips indentation from Markdown content; direct write is more predictable |

**Installation:**
```bash
# No new dependencies needed -- all libraries already installed
```

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/debate/
    models.py               # MODIFIED: add InvestmentMemo, MemoSection, DisagreementAnalysis, Disagreement
    memo.py                 # NEW: MemoGenerator class (LLM synthesis)
    extraction.py           # UNCHANGED
    orchestrator.py         # UNCHANGED
    prompts.py              # UNCHANGED
    __init__.py             # MODIFIED: export new classes
src/tinyic/
    export.py               # NEW: ExportManager for Markdown/DOCX
tests/
    test_memo.py            # NEW: memo generation + disagreement extraction tests
    test_export.py          # NEW: export functionality tests
```

### Pattern 1: LLM Synthesis via client().send_message() (OUTP-03)
**What:** Call the OpenAI API directly with a structured prompt containing the full debate context (transcript + scorecard + data package) and request structured JSON output with specific memo sections.

**When to use:** After debate completes, when generating the investment memo.

**Example:**
```python
from tinytroupe.clients import client
from tinytroupe.utils import extract_json

MEMO_SYSTEM_PROMPT = """You are an expert investment analyst synthesizing a structured investment memo from a committee debate.

You will receive:
1. The full debate transcript (opening statements, cross-examination, rebuttal, final verdicts)
2. The scorecard with each investor's vote, confidence, and key reasoning
3. The financial data package used in the debate

Produce a JSON object with these sections:
{
    "executive_summary": {
        "content": "2-3 paragraph executive summary of the committee's findings",
        "contributing_personas": ["names of personas whose arguments shaped this section"],
        "supporting_data": ["specific data points from the financial package referenced"]
    },
    "investment_thesis": { ... same structure ... },
    "key_risks": { ... },
    "valuation_discussion": { ... },
    "final_verdict": { ... }
}

RULES:
- Every claim must be grounded in the debate transcript or financial data -- do NOT introduce facts not discussed
- Name specific personas when attributing arguments
- Reference specific financial metrics from the data package
- The final verdict section must reflect the actual vote distribution, not your own opinion
"""

def generate_memo(debate_result, data_package):
    user_prompt = f"""## Debate Transcript
{debate_result.transcript}

## Scorecard
{debate_result.scorecard.to_markdown()}

## Financial Data
{data_package.to_context_string()}
"""
    messages = [
        {"role": "system", "content": MEMO_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    response = client().send_message(messages, temperature=0.7)
    return extract_json(response["content"])
```

### Pattern 2: Disagreement Extraction via LLM (OUTP-04)
**What:** A separate LLM call that analyzes the debate transcript to identify the top 3 dimensions of disagreement, with evidence quotes from each side.

**When to use:** After debate completes, alongside or after memo generation.

**Example:**
```python
DISAGREEMENT_SYSTEM_PROMPT = """You are analyzing an investment committee debate to identify the key areas of disagreement.

Produce a JSON object:
{
    "disagreements": [
        {
            "dimension": "Name of the disagreement dimension (e.g., 'Valuation Methodology')",
            "description": "1-2 sentence description of what they disagreed about",
            "sides": [
                {
                    "persona": "Warren Buffett",
                    "position": "Brief summary of their position",
                    "evidence_quote": "Direct quote from the transcript"
                },
                {
                    "persona": "Benjamin Graham",
                    "position": "Brief summary of their opposing position",
                    "evidence_quote": "Direct quote from the transcript"
                }
            ],
            "resolution": "How (or whether) this was resolved in the final votes"
        }
    ]
}

Return exactly 3 disagreements, ranked by significance (most impactful to the final verdict first).
Only include evidence_quote text that appears verbatim in the transcript.
"""
```

### Pattern 3: Export with Graceful DOCX Fallback (OUTP-05)
**What:** Export artifacts as Markdown (always) and DOCX (when pandoc is available). The DOCX path uses ArtifactExporter's existing pipeline.

**When to use:** After memo/disagreement generation, when user requests export.

**Example:**
```python
import shutil
from pathlib import Path

def has_pandoc() -> bool:
    return shutil.which("pandoc") is not None

class ExportManager:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_memo_markdown(self, memo: InvestmentMemo, filename: str) -> Path:
        path = self.output_dir / f"{filename}.md"
        path.write_text(memo.to_markdown(), encoding="utf-8")
        return path

    def export_memo_docx(self, memo: InvestmentMemo, filename: str) -> Path | None:
        if not has_pandoc():
            return None
        # Use ArtifactExporter for DOCX conversion
        from tinytroupe.extraction import ArtifactExporter
        exporter = ArtifactExporter(str(self.output_dir))
        exporter.export(
            artifact_name=filename,
            artifact_data=memo.to_markdown(),
            content_type="",  # no subfolder
            content_format="md",
            target_format="docx",
        )
        return self.output_dir / f"{filename}.docx"
```

### Anti-Patterns to Avoid
- **Using ResultsExtractor for memo synthesis:** It's designed for per-agent extraction, not cross-agent synthesis. Its Mustache template expects agent interaction history, not a synthesis prompt.
- **Embedding memo generation in the debate loop:** Memo generation is a post-debate step. Don't inject it into `_step()` or `run_debate()`.
- **Generating DOCX without checking pandoc:** Will crash on systems without pandoc installed. Always check with `shutil.which("pandoc")`.
- **Using ArtifactExporter for Markdown:** It runs `utils.dedent()` which strips indentation from Markdown content, potentially breaking formatting. Write Markdown directly.
- **Combining memo + disagreement in a single LLM call:** Each task benefits from a focused prompt. Combining them risks worse quality on both and makes the output harder to parse.
- **Including raw DataPackage JSON in the memo:** The memo should reference specific data points in natural language, not dump raw JSON. The data package is context for the LLM, not content for the memo.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JSON extraction from LLM output | Custom regex parser | `tinytroupe.utils.extract_json()` | Handles edge cases (invalid escapes, code blocks, partial JSON) |
| DOCX conversion | Custom XML generation | `ArtifactExporter._export_as_docx()` (via pypandoc) | Mature pipeline; already tested in TinyTroupe |
| OpenAI API calls | Raw `openai.Client()` | `tinytroupe.clients.client().send_message()` | Handles streaming, retries, cost tracking, config |
| Pydantic model validation | Manual dict parsing | Pydantic `model_validate()` with fallback via `to_pydantic_or_sanitized_dict()` | Consistent with project patterns; handles edge cases |

## Common Pitfalls

### Pitfall 1: Memo Hallucinating Claims Not in Transcript
**What goes wrong:** The LLM generates plausible-sounding financial analysis that wasn't actually discussed in the debate.
**Why it happens:** LLMs have strong financial knowledge priors that override the grounding constraint.
**How to avoid:** Include explicit grounding instructions in the system prompt: "Every claim must be attributable to a specific persona statement or data package metric." Require `contributing_personas` and `supporting_data` fields in each section.
**Warning signs:** Memo references financial metrics not present in the DataPackage; attributes arguments to personas who didn't make them.

### Pitfall 2: Transcript Exceeds Context Window
**What goes wrong:** Long debates with 6 personas produce transcripts that, combined with the system prompt and data package, exceed the model's context window.
**Why it happens:** Each persona generates 200-500 tokens per phase × 4 phases × 6 personas = 4800-12000 tokens of transcript alone.
**How to avoid:** Truncate the transcript if it exceeds ~8000 characters, prioritizing cross-examination and verdict phases (most informative for memo). Include the full scorecard (structured, compact) even when truncating transcript.
**Warning signs:** API errors about maximum context length; memo quality degrades with larger panels.

### Pitfall 3: Disagreement Evidence Quotes Not Verbatim
**What goes wrong:** The LLM paraphrases transcript content instead of quoting verbatim, undermining the evidence quality.
**Why it happens:** LLMs default to paraphrasing unless strongly instructed to quote exactly.
**How to avoid:** In the disagreement prompt, emphasize: "evidence_quote must be copied VERBATIM from the transcript." In tests, verify quotes appear in the original transcript (when using recorded fixtures).
**Warning signs:** Quotes contain phrasing not found anywhere in the transcript.

### Pitfall 4: ArtifactExporter dedent Stripping Markdown
**What goes wrong:** `ArtifactExporter.export()` calls `utils.dedent()` on string input, which can strip meaningful indentation from Markdown (e.g., nested lists, code blocks).
**Why it happens:** `dedent()` is designed for Python code, not Markdown content.
**How to avoid:** For Markdown export, write directly to file. Only use ArtifactExporter for DOCX conversion (where the intermediate step is HTML, so indentation doesn't matter).
**Warning signs:** Exported Markdown has flattened list indentation or broken code block formatting.

### Pitfall 5: Export Tests Dependent on Pandoc
**What goes wrong:** DOCX export tests fail on CI or other machines without pandoc installed.
**Why it happens:** pypandoc requires the pandoc binary, which is not a Python package.
**How to avoid:** Gate DOCX tests with `@pytest.mark.skipif(not has_pandoc(), reason="pandoc not installed")`. Always test Markdown export path (no external dependencies).
**Warning signs:** Tests pass locally but fail in CI.

## Code Examples

### Direct LLM Call Pattern (verified from codebase)
```python
# Source: tinytroupe/clients/openai_client.py send_message()
# Used by ResultsExtractor, TinyEnricher, TinyPerson.act()

from tinytroupe.clients import client
from tinytroupe.utils import extract_json

messages = [
    {"role": "system", "content": "System prompt here"},
    {"role": "user", "content": "User prompt with context"},
]

response = client().send_message(
    messages,
    temperature=0.7,        # Balance creativity and consistency
    frequency_penalty=0.0,  # Don't penalize repetition (synthesis needs it)
    presence_penalty=0.0,   # Don't force diversity (structured output)
)

content = response["content"]
data = extract_json(content)  # Returns dict; empty {} on failure
```

### Pydantic Model Pattern (verified from models.py)
```python
# Source: tinyic/debate/models.py Vote, Scorecard pattern

class MemoSection(BaseModel):
    """A single section of the investment memo."""
    title: str
    content: str
    contributing_personas: list[str] = Field(default_factory=list)
    supporting_data: list[str] = Field(default_factory=list)

class InvestmentMemo(BaseModel):
    """Full narrative investment memo."""
    ticker: str
    company_name: str
    executive_summary: MemoSection
    investment_thesis: MemoSection
    key_risks: MemoSection
    valuation_discussion: MemoSection
    final_verdict: MemoSection
    generated_at: datetime = Field(default_factory=datetime.now)

    def to_markdown(self) -> str:
        """Render the memo as a Markdown document."""
        sections = [
            self.executive_summary,
            self.investment_thesis,
            self.key_risks,
            self.valuation_discussion,
            self.final_verdict,
        ]
        lines = [f"# Investment Memo: {self.company_name} ({self.ticker})", ""]
        for section in sections:
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
            if section.contributing_personas:
                lines.append(f"*Contributors: {', '.join(section.contributing_personas)}*")
            if section.supporting_data:
                lines.append(f"*Data references: {', '.join(section.supporting_data)}*")
            lines.append("")
        return "\n".join(lines)
```

### Disagreement Model Pattern
```python
class Disagreement(BaseModel):
    """A single dimension of disagreement between personas."""
    dimension: str  # e.g., "Valuation Methodology"
    description: str  # 1-2 sentence description
    sides: list[dict]  # [{persona, position, evidence_quote}, ...]
    resolution: str  # How/whether resolved in final votes

class DisagreementAnalysis(BaseModel):
    """Top disagreements from the debate."""
    ticker: str
    company_name: str
    disagreements: list[Disagreement] = Field(default_factory=list)

    def to_markdown(self) -> str:
        """Render disagreement analysis as Markdown."""
        lines = [f"# Disagreement Analysis: {self.company_name} ({self.ticker})", ""]
        for i, d in enumerate(self.disagreements, 1):
            lines.append(f"## {i}. {d.dimension}")
            lines.append("")
            lines.append(d.description)
            lines.append("")
            for side in d.sides:
                lines.append(f"**{side['persona']}:** {side['position']}")
                lines.append(f"> \"{side['evidence_quote']}\"")
                lines.append("")
            lines.append(f"**Resolution:** {d.resolution}")
            lines.append("")
        return "\n".join(lines)
```

### Mock Pattern for LLM Calls (verified from test_debate.py)
```python
# Source: tests/test_debate.py TestVoteExtraction pattern

from unittest.mock import patch, MagicMock

@patch("tinyic.debate.memo.client")
def test_generate_memo(self, mock_client_fn):
    """Mock the LLM client to return a known memo structure."""
    mock_client = MagicMock()
    mock_client_fn.return_value = mock_client
    mock_client.send_message.return_value = {
        "role": "assistant",
        "content": json.dumps({
            "executive_summary": {
                "content": "The committee analyzed Apple...",
                "contributing_personas": ["Warren Buffett", "Benjamin Graham"],
                "supporting_data": ["P/E ratio of 28x", "Revenue $394B"]
            },
            # ... other sections
        })
    }

    result = generate_memo(debate_result, data_package)
    assert result.executive_summary.content == "The committee analyzed Apple..."
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Manual report writing from debate notes | LLM-synthesized structured memos from full debate context | 2024-2025 | Dramatically faster turnaround; consistent section structure |
| Keyword extraction for disagreement finding | LLM-based semantic analysis of transcript divergence | 2024-2025 | Captures nuanced disagreements beyond vocabulary-level differences |
| PDF export via LaTeX | Markdown → HTML → DOCX via pypandoc | Established | Simpler pipeline; widely compatible; no LaTeX dependency |
| Monolithic report generation | Section-by-section structured generation with grounding constraints | 2025-2026 | Better verifiability; each section traceable to source material |

## Open Questions

1. **Transcript truncation strategy**
   - What we know: With 6 personas × 4 phases, transcripts can be 5000-12000 tokens. Combined with system prompt and data package, this may approach context limits.
   - What's unclear: Exact token budget for memo synthesis prompt.
   - Recommendation: Set transcript budget to 8000 characters. If exceeded, truncate OPENING phase first (least informative), keeping CROSS_EXAM and VERDICT in full. Include full scorecard regardless.

2. **Memo generation latency**
   - What we know: A single `send_message()` call to GPT-5.2 takes 5-30 seconds depending on output length.
   - What's unclear: Whether users expect memo generation to be synchronous with debate completion or asynchronous.
   - Recommendation: For Phase 9 (backend only), implement as synchronous call. Phase 10 (UI) can add async/loading state.

3. **Evidence quote verification**
   - What we know: LLMs may paraphrase instead of quoting verbatim.
   - What's unclear: Whether post-processing verification (checking quotes against transcript) is worth the complexity in v1.1.
   - Recommendation: Rely on strong prompting for v1.1. Add quote verification in v2 if quality is insufficient.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (installed) |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_memo.py tests/test_export.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q --timeout=120` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| OUTP-03 | generate_memo() returns InvestmentMemo with 5 sections | unit | `uv run pytest tests/test_memo.py::TestMemoGeneration -x` | No -- Plan 01 |
| OUTP-03 | Each memo section has contributing_personas and supporting_data | unit | `uv run pytest tests/test_memo.py::TestMemoGeneration::test_memo_section_grounding -x` | No -- Plan 01 |
| OUTP-03 | InvestmentMemo.to_markdown() produces valid Markdown | unit | `uv run pytest tests/test_memo.py::TestMemoModels::test_memo_to_markdown -x` | No -- Plan 01 |
| OUTP-04 | extract_disagreements() returns DisagreementAnalysis with 3 disagreements | unit | `uv run pytest tests/test_memo.py::TestDisagreementExtraction -x` | No -- Plan 01 |
| OUTP-04 | Each disagreement has sides with evidence quotes | unit | `uv run pytest tests/test_memo.py::TestDisagreementExtraction::test_disagreement_has_evidence -x` | No -- Plan 01 |
| OUTP-05 | Markdown export produces valid .md file | unit | `uv run pytest tests/test_export.py::TestExportManager::test_export_memo_markdown -x` | No -- Plan 02 |
| OUTP-05 | DOCX export produces valid .docx file (when pandoc available) | unit | `uv run pytest tests/test_export.py::TestExportManager::test_export_memo_docx -x` | No -- Plan 02 |
| OUTP-05 | DOCX export gracefully falls back when pandoc unavailable | unit | `uv run pytest tests/test_export.py::TestExportManager::test_docx_fallback_without_pandoc -x` | No -- Plan 02 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_memo.py tests/test_export.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q --timeout=120`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `src/tinyic/debate/memo.py` -- MemoGenerator class (new file)
- [ ] `src/tinyic/export.py` -- ExportManager class (new file)
- [ ] `tests/test_memo.py` -- memo + disagreement tests (new file)
- [ ] `tests/test_export.py` -- export tests (new file)
- [ ] Extended `src/tinyic/debate/models.py` -- InvestmentMemo, DisagreementAnalysis models

## Sources

### Primary (HIGH confidence)
- openIC codebase: `models.py`, `extraction.py`, `orchestrator.py` -- verified DebateResult, Scorecard, Vote, extract_votes() APIs
- TinyTroupe codebase: `openai_client.py` -- verified `client().send_message()` API, parameters, streaming, cost tracking
- TinyTroupe codebase: `artifact_exporter.py` -- verified DOCX export pipeline (markdown → HTML → DOCX via pypandoc)
- TinyTroupe codebase: `utils/llm.py` -- verified `extract_json()` for parsing structured LLM output
- TinyTroupe codebase: `results_extractor.py` -- verified per-agent extraction pattern (confirms NOT suitable for cross-agent synthesis)
- System check: pypandoc 1.17 installed, pandoc binary at /opt/homebrew/bin/pandoc

### Secondary (MEDIUM confidence)
- TinyTroupe codebase: `tiny_enricher.py`, `tiny_word_processor.py` -- reference patterns for document generation
- openIC codebase: `test_debate.py` -- mock patterns for LLM client and ResultsExtractor

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed; no new dependencies
- Architecture: HIGH -- patterns directly use verified TinyTroupe APIs; memo generation is a straightforward LLM synthesis pattern
- Pitfalls: MEDIUM -- transcript truncation threshold and LLM grounding quality need calibration during implementation
- Testing: HIGH -- mock patterns well-established from Phase 4/8; export tests straightforward

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable -- no fast-moving dependencies)
