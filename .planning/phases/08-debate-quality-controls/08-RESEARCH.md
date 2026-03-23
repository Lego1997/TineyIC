# Phase 8: Debate Quality Controls - Research

**Researched:** 2026-03-23
**Domain:** Multi-agent LLM debate quality -- anti-convergence, devil's advocate roles, differentiation testing
**Confidence:** HIGH

## Summary

Phase 8 adds structural controls to prevent persona opinions from collapsing during multi-round debates, introduces a rotating devil's advocate role during cross-examination, and creates lightweight automated tests to detect convergence regression. The existing codebase provides strong foundations: `DebateOrchestrator._step()` already iterates agents sequentially per phase, `broadcast_internal_goal()` and `agent.listen()` provide per-agent injection points, and `TinyPerson.reset_prompt()` already re-injects the persona system prompt before each `act()` call (confirmed in codebase).

The core insight from multi-agent debate research is that convergence happens primarily through two mechanisms: (1) agents seeing others' agreeable responses and adapting toward consensus (social conformity), and (2) the model's inherent tendency toward "helpful agreement" overpowering persona-specific convictions. Since TinyPerson already re-injects persona prompts each turn, the solution must add *structural* interventions -- contrastive persona-specific reinforcement prompts injected before each agent acts, and an explicit devil's advocate role assignment during cross-examination with a modified prompt. For differentiation testing, TF-IDF cosine similarity via scikit-learn (already installed at v1.8.0) provides a proven, deterministic measurement approach that works well with mocked/recorded debate outputs.

**Primary recommendation:** Add per-agent contrastive reinforcement via `agent.listen()` before each `act()` call in `_step()`, implement devil's advocate as a rotating role injection during cross-examination only, and use TF-IDF cosine similarity for differentiation regression tests on fixed recorded fixtures.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| DEBT-04 | Anti-convergence controls preventing opinion collapse during multi-round debates | Contrastive persona reinforcement prompts + "hold your ground" framing injected per-agent before each act(); architecture patterns and code examples provided |
| DEBT-05 | Rotating devil's advocate during cross-examination with unconstrained final votes | Per-agent role injection via listen() during CROSS_EXAM phase only; rotation strategy and prompt templates provided |
| PERS-08 | Lightweight automated differentiation regression tests (PERS-08-lite) | TF-IDF cosine similarity on recorded fixtures; test patterns and threshold recommendations provided |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| scikit-learn | 1.8.0 | TF-IDF vectorization + cosine similarity for differentiation tests | Already installed; `TfidfVectorizer` + `cosine_similarity` is the standard approach for vocabulary-based text comparison |
| textdistance | (installed) | Jaccard similarity for token-level overlap checks | Already a TinyTroupe dependency; provides normalized similarity with common interface |
| pytest | (installed) | Test framework for differentiation regression tests | Already the project standard |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| collections.Counter | stdlib | Word frequency counting for simple overlap metrics | Lightweight alternative when full TF-IDF is overkill |
| re | stdlib | Text preprocessing (strip formatting, extract key reasoning sections) | Cleaning LLM output before similarity comparison |
| json | stdlib | Loading/saving recorded debate fixtures | Fixture management for offline tests |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| TF-IDF cosine | Sentence embeddings (e.g., sentence-transformers) | More semantically accurate but adds a heavy dependency, requires model download, and is overkill for vocabulary overlap detection at regression-test granularity |
| TF-IDF cosine | textdistance.jaccard | Simpler but operates on character n-grams by default; less suited for document-level vocabulary comparison |
| Per-agent listen() injection | TinyTroupe Intervention system | Interventions use LLM-based precondition checks (expensive); direct injection is simpler and deterministic |

**Installation:**
```bash
# No new dependencies needed -- scikit-learn, textdistance, and pytest are already installed
```

## Architecture Patterns

### Recommended Project Structure
```
src/tinyic/debate/
    orchestrator.py          # Modified: anti-convergence + devil's advocate logic
    prompts.py               # Modified: new prompt templates for reinforcement + DA
    models.py                # Unchanged (or minor additions)
    extraction.py            # Unchanged
    __init__.py              # Unchanged
tests/
    test_debate.py           # Extended: new test classes for DEBT-04, DEBT-05
    test_differentiation.py  # NEW: PERS-08-lite convergence detection tests
    fixtures/                # NEW: recorded debate outputs for offline testing
        recorded_debate_apple.json
```

### Pattern 1: Per-Agent Contrastive Reinforcement (DEBT-04)
**What:** Before each agent acts in `_step()`, inject a persona-specific "hold your ground" reinforcement prompt via `agent.listen()`. This is structurally different from the system prompt re-injection that `reset_prompt()` does -- it adds a *contrastive* stimulus that explicitly names the agent's unique perspective and instructs them not to converge.

**When to use:** Every phase of the debate (OPENING, CROSS_EXAM, REBUTTAL, VERDICT).

**Example:**
```python
# In DebateOrchestrator._step(), before agent.act():

REINFORCEMENT_TEMPLATE = (
    "IMPORTANT REMINDER: You are {name}. Your investment philosophy is fundamentally "
    "distinct from the other committee members. {philosophy_hook} "
    "Do NOT soften your position to match others. If you disagree, say so clearly "
    "and explain WHY from YOUR framework. A unanimous committee is a failed committee -- "
    "the value of this debate comes from genuine disagreement."
)

# philosophy_hook is persona-specific, e.g.:
# Buffett: "You evaluate businesses based on durable moats and owner earnings."
# Graham: "You focus on margin of safety and quantitative cheapness."
# Marks: "You focus on cycle positioning and risk/reward asymmetry."

def _get_reinforcement_prompt(self, agent) -> str:
    """Build persona-specific anti-convergence reinforcement."""
    hook = self._philosophy_hooks.get(agent.name, "Stay true to your unique perspective.")
    return REINFORCEMENT_TEMPLATE.format(name=agent.name, philosophy_hook=hook)
```

### Pattern 2: Rotating Devil's Advocate (DEBT-05)
**What:** During the CROSS_EXAM phase only, assign one persona as devil's advocate. This persona receives an additional prompt instructing them to argue the strongest counter-position to the emerging consensus. The assignment rotates (e.g., round-robin across debates, or the persona whose OPENING position most aligned with the emerging consensus).

**When to use:** CROSS_EXAM phase only. The DA role does NOT constrain final votes.

**Example:**
```python
# In DebateOrchestrator._step(), when phase == CROSS_EXAM:

DEVILS_ADVOCATE_PROMPT = (
    "SPECIAL ROLE FOR THIS PHASE: You have been designated as the Devil's Advocate. "
    "Regardless of your personal view, you MUST argue the STRONGEST possible "
    "counter-position to the emerging consensus. Challenge every assumption. "
    "Find the weakest points in the majority view. Present the best case for "
    "the opposite conclusion. This role applies ONLY to this cross-examination phase -- "
    "your final vote in the verdict phase should reflect your TRUE opinion."
)

def _select_devils_advocate(self) -> TinyPerson:
    """Select the devil's advocate for cross-examination.

    Strategy: rotate through agents based on debate index or round.
    Simple round-robin using a stored index.
    """
    idx = getattr(self, '_da_index', 0)
    da = self.agents[idx % len(self.agents)]
    self._da_index = idx + 1
    return da
```

### Pattern 3: Recorded Fixture Testing (PERS-08)
**What:** Save a canonical debate output (real or crafted) as a JSON fixture. The test loads this fixture, extracts each persona's key reasoning text, computes pairwise TF-IDF cosine similarity, and asserts no pair exceeds a threshold (e.g., 0.70).

**When to use:** In pytest suite, runs without API keys.

**Example:**
```python
# tests/test_differentiation.py

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import json

def compute_pairwise_similarity(texts: list[str]) -> list[tuple[str, str, float]]:
    """Compute pairwise TF-IDF cosine similarity between texts."""
    vectorizer = TfidfVectorizer(stop_words='english')
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    pairs = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            pairs.append((f"persona_{i}", f"persona_{j}", sim_matrix[i][j]))
    return pairs

def test_persona_differentiation_on_fixture():
    """PERS-08: No two personas share >70% vocabulary overlap."""
    with open("tests/fixtures/recorded_debate_apple.json") as f:
        fixture = json.load(f)

    reasoning_texts = [p["reasoning_text"] for p in fixture["personas"]]
    pairs = compute_pairwise_similarity(reasoning_texts)

    for name_a, name_b, sim in pairs:
        assert sim < 0.70, (
            f"{name_a} and {name_b} have {sim:.2%} vocabulary overlap "
            f"(threshold: 70%)"
        )
```

### Anti-Patterns to Avoid
- **Modifying TinyPerson.reset_prompt() for anti-convergence:** This would affect all TinyTroupe usage globally and is fragile. Keep anti-convergence at the orchestrator level.
- **Using the TinyTroupe Intervention system for this:** Interventions use LLM-based precondition checks (`Proposition` evaluation), which adds API calls and latency. Direct injection via `listen()` is simpler, cheaper, and deterministic.
- **Constraining devil's advocate votes in the VERDICT phase:** The requirement explicitly states final votes must remain unconstrained. The DA prompt must clearly state it applies to cross-examination only.
- **Using semantic embeddings for the differentiation test:** Adding sentence-transformers or similar would bring a heavy model dependency just for a regression test. TF-IDF is sufficient for detecting vocabulary convergence.
- **Making differentiation tests depend on live API calls:** PERS-08-lite must run offline with mocked/recorded fixtures. API-dependent tests belong in a separate `live_api` marked test class.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Text similarity measurement | Custom word-counting overlap function | `sklearn.feature_extraction.text.TfidfVectorizer` + `sklearn.metrics.pairwise.cosine_similarity` | Handles tokenization, stop words, TF-IDF weighting, and cosine computation correctly; already installed |
| Token-level Jaccard similarity | Manual set intersection/union | `textdistance.jaccard.normalized_similarity()` | Already a TinyTroupe dependency with a clean API |
| Per-agent message injection | Custom message passing system | `TinyPerson.listen()` / `TinyPerson.internalize_goal()` | These are TinyTroupe's built-in stimulus injection methods; they integrate with memory and prompt properly |
| Phase-specific prompt routing | Separate orchestrator subclass per phase | Phase check in existing `_step()` method | The orchestrator already has phase awareness; adding a conditional is simpler than inheritance |

**Key insight:** The TinyTroupe framework already provides all the injection mechanisms needed. Anti-convergence and devil's advocate are *prompt engineering* problems at the orchestrator level, not framework extension problems.

## Common Pitfalls

### Pitfall 1: Reinforcement Prompt Overwhelming Persona Identity
**What goes wrong:** If the anti-convergence reinforcement prompt is too long or too specific, it can dominate the agent's behavior and make all responses sound formulaic ("As Warren Buffett, I disagree because...").
**Why it happens:** The reinforcement competes with the persona system prompt for attention in the context window.
**How to avoid:** Keep reinforcement prompts short (2-3 sentences). Focus on *reminding* the persona of their framework, not *restating* it. The system prompt already contains the full persona definition.
**Warning signs:** Agents start using identical phrasing patterns; agents reference the reinforcement prompt explicitly in their responses.

### Pitfall 2: Devil's Advocate Persona Contamination
**What goes wrong:** The DA prompt carries over into the REBUTTAL and VERDICT phases, causing the designated persona to argue against their true position even in the final vote.
**Why it happens:** TinyPerson stores stimuli in episodic memory, so the DA instruction persists beyond the cross-examination phase.
**How to avoid:** Add a clear "role release" message via `agent.listen()` at the start of the REBUTTAL phase: "Your Devil's Advocate role has ended. From now on, argue and vote based on your TRUE investment conviction." This creates a counter-stimulus in memory.
**Warning signs:** The DA persona's final vote contradicts their OPENING statement without clear reasoning for the change.

### Pitfall 3: TF-IDF Threshold Too Strict or Too Loose
**What goes wrong:** Setting the similarity threshold at 0.70 might fail for legitimate reasons (e.g., two value investors naturally share terminology like "margin of safety") or pass when convergence has actually occurred.
**Why it happens:** TF-IDF cosine similarity is sensitive to vocabulary but blind to argument structure and logical position.
**How to avoid:** Start with 0.70 as a threshold, but also check for unanimous vote convergence as a complementary signal. Consider computing similarity only on the "key reasoning" sections, not the full verbose output. Run the test on a known-good fixture first to calibrate.
**Warning signs:** The test passes but manual review shows all personas reaching the same conclusion with slightly different wording.

### Pitfall 4: Fixture Staleness
**What goes wrong:** The recorded debate fixture becomes stale as persona configs evolve, leading to false confidence in the test.
**Why it happens:** Persona configs change but the fixture is not regenerated.
**How to avoid:** Document which persona configs and data package were used to generate the fixture. Include a fixture metadata header with generation date and persona versions. Consider having a `live_api` marked test that regenerates the fixture periodically.
**Warning signs:** Differentiation test passes but live debates show convergence.

### Pitfall 5: Message Queue Interaction with Reinforcement
**What goes wrong:** User steering messages injected via `message_queue` before an agent acts could conflict with or be overridden by the reinforcement prompt, or vice versa.
**Why it happens:** Both user messages and reinforcement prompts are injected via `listen()` before `agent.act()`.
**How to avoid:** Process the message queue first (as currently done), then inject reinforcement. This way user steering gets priority and reinforcement serves as a consistent background reminder.
**Warning signs:** User messages seem to have no effect during debates with reinforcement active.

## Code Examples

Verified patterns from the existing codebase:

### Injecting Per-Agent Stimulus Before Act
```python
# Source: DebateOrchestrator._step() pattern (orchestrator.py:104-113)
# This is the existing pattern we extend for reinforcement:
for agent in self.agents:
    self._process_message_queue()

    # NEW: inject anti-convergence reinforcement
    reinforcement = self._get_reinforcement_prompt(agent)
    agent.listen(reinforcement)

    # NEW: inject devil's advocate role if applicable
    if phase == DebatePhase.CROSS_EXAM and agent == self._current_devils_advocate:
        agent.listen(DEVILS_ADVOCATE_PROMPT)

    if self.on_agent_start:
        self.on_agent_start(agent.name, phase.value)

    actions = agent.act(return_actions=True)
    # ... rest unchanged
```

### Building Philosophy Hooks from Persona Config
```python
# Source: InvestorPersona._load_philosophy() pattern (base.py:29-51)
# Persona data is available via agent.get("beliefs"), agent.get("style"), etc.

PHILOSOPHY_HOOKS = {
    "Warren Buffett": "You evaluate businesses based on durable competitive moats and owner earnings -- not market sentiment.",
    "Charlie Munger": "You apply mental models from multiple disciplines and look for businesses that are so good an idiot could run them.",
    "Benjamin Graham": "You demand quantitative margin of safety -- intrinsic value backed by hard numbers, not stories.",
    "Peter Lynch": "You find investments in everyday life -- growth at a reasonable price, not abstract financial engineering.",
    "Howard Marks": "You focus on where we stand in the cycle, risk/reward asymmetry, and second-level thinking.",
    "Li Lu": "You seek companies with enduring competitive advantages in large addressable markets, especially in Asia.",
}
```

### TF-IDF Cosine Similarity for Differentiation
```python
# Source: scikit-learn 1.8.0 official API
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def measure_differentiation(persona_texts: dict[str, str]) -> dict:
    """Measure pairwise differentiation between persona reasoning texts.

    Args:
        persona_texts: Mapping of persona name -> reasoning text.

    Returns:
        Dict with 'pairs' (list of (name_a, name_b, similarity)) and
        'max_similarity' (highest pairwise similarity found).
    """
    names = list(persona_texts.keys())
    texts = list(persona_texts.values())

    vectorizer = TfidfVectorizer(stop_words='english', min_df=1)
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    pairs = []
    max_sim = 0.0
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sim = float(sim_matrix[i][j])
            pairs.append((names[i], names[j], sim))
            max_sim = max(max_sim, sim)

    return {"pairs": pairs, "max_similarity": max_sim}
```

### Recorded Fixture Structure
```json
{
    "metadata": {
        "generated": "2026-03-23",
        "company": "Apple Inc.",
        "ticker": "AAPL",
        "personas": ["Warren Buffett", "Benjamin Graham", "Howard Marks"]
    },
    "personas": [
        {
            "name": "Warren Buffett",
            "opening_text": "Apple represents...",
            "cross_exam_text": "I challenge Graham's...",
            "rebuttal_text": "While the criticism of...",
            "verdict_text": "BUY with HIGH confidence...",
            "reasoning_text": "Apple possesses an extraordinary economic moat through its ecosystem lock-in, brand loyalty, and services revenue stream that compounds..."
        }
    ]
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Homogeneous agent pools (same model, same prompt) | Heterogeneous agents with persona-specific prompts | 2024-2025 (A-HMAD, Debate-to-Write papers) | Significantly reduces convergence; personas must be genuinely different |
| Static role assignment (fixed debater/judge) | Dynamic/rotating role assignment | 2025-2026 (Dynamic Role Assignment paper) | 74.8% improvement over uniform assignment; rotation prevents one agent from dominating |
| Simple majority voting for consensus | Structured debate phases with explicit dissent roles | 2024-2025 (IUI-24 Devil's Advocate paper) | Devil's advocate improves decision quality even when the dissenter is wrong |
| Semantic similarity for text comparison | TF-IDF + cosine for vocabulary-level overlap detection | Established (scikit-learn stable) | Appropriate for detecting vocabulary convergence without heavy model dependencies |

**Deprecated/outdated:**
- Using a single "judge" agent to resolve debates is considered inferior to structured multi-phase debate with rotating roles (per ICLR 2025 analysis)
- Relying solely on system prompt re-injection (what TinyPerson.reset_prompt() does) is insufficient for anti-convergence -- additional structural controls are needed (this is exactly what DEBT-04 addresses)

## Open Questions

1. **Philosophy hook maintenance**
   - What we know: Each persona needs a 1-2 sentence philosophy hook for the reinforcement prompt. The full persona config JSON has all the data needed.
   - What's unclear: Should hooks be hard-coded in PHILOSOPHY_HOOKS dict, or dynamically extracted from the persona config's `beliefs` or `style` field?
   - Recommendation: Hard-code hooks for the 6 known personas. This is simpler and allows precise phrasing. If custom personas are added (v2), extract dynamically from config then.

2. **Devil's advocate selection strategy**
   - What we know: Round-robin rotation is simple and ensures fairness. Alternative: pick the persona whose OPENING position most aligned with the emerging consensus.
   - What's unclear: Whether consensus-based selection (requiring analysis of opening statements) adds meaningful value vs. complexity.
   - Recommendation: Start with simple round-robin (stored index on orchestrator). This is deterministic, easy to test, and satisfies the "rotating" requirement. The consensus-based approach can be added later if needed.

3. **Differentiation threshold calibration**
   - What we know: 0.70 TF-IDF cosine similarity is a reasonable starting threshold. Value investors (Buffett, Munger, Graham) naturally share more vocabulary than, say, Marks.
   - What's unclear: The exact threshold that reliably catches problematic convergence without false positives.
   - Recommendation: Create the fixture, run the test, and adjust. Start at 0.70 and lower if needed. Also include a complementary check for unanimous vote patterns.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (installed via tinytroupe dependency) |
| Config file | `pyproject.toml` [tool.pytest.ini_options] |
| Quick run command | `uv run pytest tests/test_differentiation.py -x -q` |
| Full suite command | `uv run pytest tests/ -x -q --timeout=120` |

### Phase Requirements -> Test Map
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

### Sampling Rate
- **Per task commit:** `uv run pytest tests/test_debate.py tests/test_differentiation.py -x -q`
- **Per wave merge:** `uv run pytest tests/ -x -q --timeout=120`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_differentiation.py` -- covers PERS-08 (new file)
- [ ] `tests/fixtures/recorded_debate_apple.json` -- recorded debate fixture for offline testing
- [ ] New test classes in `tests/test_debate.py` -- TestAntiConvergence, TestDevilsAdvocate (extend existing file)

## Sources

### Primary (HIGH confidence)
- TinyTroupe codebase: `tiny_person.py` -- verified `reset_prompt()`, `listen()`, `internalize_goal()`, `act()` APIs
- TinyTroupe codebase: `tiny_world.py` -- verified `broadcast_internal_goal()`, `_step()`, `_handle_actions()`, `Intervention` system
- openIC codebase: `orchestrator.py` -- verified `_step()` override, sequential agent execution, phase management
- openIC codebase: `prompts.py` -- verified existing phase prompt templates
- scikit-learn 1.8.0 docs: `TfidfVectorizer`, `cosine_similarity` API -- [TfidfVectorizer docs](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)

### Secondary (MEDIUM confidence)
- [Debate-to-Write: A Persona-Driven Multi-Agent Framework (COLING 2025)](https://aclanthology.org/2025.coling-main.314.pdf) -- contrastive prompting and perspective-locking patterns for persona maintenance during debate
- [Multi-LLM-Agents Debate: Performance, Efficiency, and Scaling Challenges (ICLR Blogposts 2025)](https://d2jud02ci9yv69.cloudfront.net/2025-04-28-mad-159/blog/mad/) -- convergence failure analysis, heterogeneous agent pools
- [Dynamic Role Assignment for Multi-Agent Debate (arXiv 2601.17152)](https://arxiv.org/abs/2601.17152) -- dynamic role assignment with 74.8% improvement over uniform assignments
- [Amplifying Minority Voices: AI-Mediated Devil's Advocate System (arXiv 2502.06251)](https://arxiv.org/html/2502.06251v1) -- devil's advocate architecture with paraphrase agent and conversation agent patterns
- [Enhancing AI-Assisted Group Decision Making through LLM-Powered Devil's Advocate (IUI 2024)](https://dl.acm.org/doi/10.1145/3640543.3645199) -- devil's advocate role implementation patterns

### Tertiary (LOW confidence)
- [The Devil's Advocate Architecture (Medium)](https://medium.com/@jsmith0475/the-devils-advocate-architecture-how-multi-agent-ai-systems-mirror-human-decision-making-9c9e6beb09da) -- general patterns, not peer-reviewed
- [textdistance library (PyPI)](https://pypi.org/project/textdistance/) -- API reference for Jaccard similarity (already a dependency)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all libraries already installed and verified in the project
- Architecture: HIGH -- patterns directly use existing TinyTroupe APIs verified in codebase; no framework extensions needed
- Pitfalls: MEDIUM -- based on synthesis of research papers and multi-agent debate literature; some threshold values may need calibration
- Testing: HIGH -- scikit-learn TF-IDF API is well-documented and stable; fixture-based testing is a proven pattern in this project

**Research date:** 2026-03-23
**Valid until:** 2026-04-23 (stable -- no fast-moving dependencies)
