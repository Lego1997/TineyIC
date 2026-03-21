# Phase 2 Deep Research: Investment Philosophy Distillation

**Research Date:** 2026-03-21
**Mode:** Full (7 parallel research agents)
**Total Corpus:** 4,264 lines across 7 research documents (~50,000 words)
**Sources Used:** Shareholder letters, books, speeches, memos, lectures, interviews via web search

---

## Executive Summary

This deep research produced comprehensive philosophy distillations for all six investors, a 14-dimension differentiation matrix, blind taste tests, and anti-convergence configuration rules. The key finding: **differentiation is achievable and grounded in real philosophical differences**, not artificial separation. Each investor asks a fundamentally different FIRST QUESTION when evaluating a company, uses different metrics, and communicates in a distinct voice.

The research confirms the Phase 2 plan's core design decisions:
1. `style` field is the #1 lever — each investor has a genuinely different reasoning STRUCTURE
2. Contrastive beliefs work because the investors genuinely DISAGREE on key questions
3. Signature vocabulary exists naturally — each has coined unique terms
4. Negative constraints are grounded in real philosophical rejections

---

## Research Corpus Index

| Document | Location | Lines | Content |
|----------|----------|-------|---------|
| Buffett Bibliography | `src/tinyic/data/buffett_bibliography.md` | 487 | 8 mental models, Four Filters decision process, direct shareholder letter quotes |
| Munger Bibliography | `.planning/research/MUNGER_BIBLIOGRAPHY.md` | 657 | 25 Causes of Human Misjudgment, complete latticework by discipline, 11 intellectual heroes |
| Graham Bibliography | `.planning/research/GRAHAM_BIBLIOGRAPHY.md` | 687 | All quantitative formulas, full Mr. Market parable, Defensive/Enterprising criteria |
| Lynch Bibliography | `.planning/research/LYNCH_BIBLIOGRAPHY.md` | 848 | Six-category taxonomy with evaluation criteria, PEG formulation, 25 Golden Rules, Cocktail Party Theory |
| Marks Bibliography | `.planning/research/HOWARD_MARKS_BIBLIOGRAPHY.md` | 641 | 10 mental models, 18 annotated memos, "Taking the Temperature" checklist, full risk framework |
| Li Lu Bibliography | `.planning/research/LI_LU_BIBLIOGRAPHY.md` | 659 | 11 mental models, Civilizations 1.0/2.0/3.0 framework, BYD case study, 23 sourced references |
| Cross-Investor Differentiation | `src/tinyic/data/cross_investor_differentiation.md` | 285 | 14-dimension Apple analysis matrix, blind taste test, anti-convergence rules, forbidden concepts |

---

## Key Finding 1: The Six First Questions (Core Differentiation)

When given the same company to analyze, each investor asks a fundamentally different FIRST question:

| Investor | First Question | Entry Point |
|----------|---------------|-------------|
| **Graham** | "Is it trading below net current asset value or 15x trailing earnings?" | Quantitative screens |
| **Buffett** | "Does this business have an unbreachable moat — and will it still have one in 20 years?" | Business quality |
| **Munger** | "What could kill this business? What second-order dynamics am I missing?" | Inversion / failure analysis |
| **Lynch** | "What category is this — stalwart, fast grower, cyclical? What's the story?" | Classification + narrative |
| **Marks** | "Where are we in the cycle? Is consensus making this too expensive?" | Cycle positioning |
| **Li Lu** | "Is this a long-term compounder in a modernizing economy?" | Civilizational trajectory |

**Implication for persona construction:** The `style` field must encode the REASONING ENTRY POINT, not just tone. Each persona should start their analysis with their signature first question.

---

## Key Finding 2: Genuine Philosophical Fault Lines

These are not artificial distinctions — these investors genuinely disagree:

| Question | The Split |
|----------|-----------|
| **Pay up for quality?** | Graham: Never. Buffett/Munger/Li Lu: Yes. Lynch: Yes if PEG < 1. Marks: Only at the right cycle point. |
| **How many stocks?** | Graham: 30+. Lynch: 30-60. Marks: varies. Buffett: 5-10. Munger: 1-3. Li Lu: few "fat pitches." |
| **Does macro matter?** | Buffett/Lynch: No. Graham: Only for overall market level. Munger: Indirectly through incentives. Marks: Can't predict but must observe cycle. Li Lu: Yes — civilizational trajectory matters enormously. |
| **Holding period?** | Graham: 1-3 years (mechanical exit). Lynch: Until the story changes. Marks: Full cycle. Buffett: Forever. Munger: Decades. Li Lu: 10+ years through 50% drawdowns. |
| **What is risk?** | Graham: Permanent principal loss (manage via diversification). Buffett: Not knowing what you're doing. Munger: Unrecognized psychological biases. Lynch: Owning what you don't understand. Marks: Probability distribution of outcomes (NOT volatility). Li Lu: Self-deception about knowledge boundaries. |

---

## Key Finding 3: Anti-Convergence Configuration Rules

The differentiation matrix produced specific "forbidden concepts" per persona — things a persona should NEVER say:

| Persona | Must NEVER Use |
|---------|---------------|
| **Graham** | "moat," "forever holding period," concentrated positions, growth investing, market cycles, mental models |
| **Buffett** | PEG ratio, stock categories, market cycles/pendulums, NCAV screens, latticework of mental models, civilizational modernization |
| **Munger** | PEG ratio, stock categories, NCAV screens, folksy/populist language, civilizational framework |
| **Lynch** | "moat" (uses "story" instead), NCAV screens, mental models, inversion, market cycles, civilizational framework |
| **Marks** | PEG ratio, stock categories, NCAV screens, "holding period is forever," folksy parables, civilizational framework |
| **Li Lu** | Stock categories (Lynch), mechanical sell rules (Graham), cycle timing (Marks), folksy parables (Buffett), psychological bias catalog (Munger) |

**Implication for persona construction:** The `beliefs` field should include both POSITIVE beliefs AND explicit rejections. The `personality.traits` field should include negative constraints.

---

## Key Finding 4: The Overlapping Pairs Are Genuinely Differentiable

### Graham → Buffett (Teacher-Student)
- **Graham**: quantitative screens → price → statistical baskets → 1-3 year mechanical exit
- **Buffett**: business quality → moat → concentrated ownership → hold forever
- **The pivot quote**: "It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price" (Buffett, 1989 letter)
- **Key insight**: Graham would reject Apple at ANY price above tangible book. Buffett called it one of his safest investments because the moat makes earnings predictable.

### Buffett ↔ Munger (Partners)
- **Buffett**: positive case first ("What makes this wonderful?") → folksy metaphors → seeks simplicity
- **Munger**: failure analysis first ("What could go wrong?") → cross-disciplinary references → embraces complexity
- **Key insight**: Munger pushed Buffett away from "cigar butt" investing. Munger's intellectual contribution was DIFFERENT from Buffett's, not just supportive.

### Graham ↔ Munger (Opposing Poles)
- **Graham**: numbers over judgment, statistical baskets, formulaic criteria
- **Munger**: cross-disciplinary reasoning, extreme concentration, mental model checklists
- **Key insight**: Graham would tell Munger "Your system depends on genius-level judgment that can't be replicated." Munger would reply "Your system worked for asset-heavy industrial companies. It fails for intangible-asset businesses."

---

## Key Finding 5: Signature Vocabulary Is Natural and Distinctive

Each investor has coined unique terms or uses distinctive language that no other investor on this list would use:

| Investor | Unique Terms | Source |
|----------|-------------|--------|
| **Graham** | "net-net," "defensive investor," "enterprising investor," "P/E × P/B < 22.5" | Security Analysis, The Intelligent Investor |
| **Buffett** | "economic moat," "owner earnings," "circle of competence," "our favorite holding period is forever" | Shareholder letters (1986-present) |
| **Munger** | "latticework of mental models," "lollapalooza effect," "invert, always invert," "show me the incentive" | Poor Charlie's Almanack, speeches |
| **Lynch** | "ten-bagger," "PEG ratio," "stalwart/fast grower/slow grower/cyclical/turnaround/asset play" | One Up on Wall Street |
| **Marks** | "second-level thinking," "the pendulum," "where are we in the cycle?," "patient opportunism" | Oaktree memos, The Most Important Thing |
| **Li Lu** | "Civilization 1.0/2.0/3.0," "take the macro as it is," "fish where the fish are," "knowledge honesty" | Columbia lectures, Peking University speeches |

---

## Key Finding 6: The Blind Taste Test Works

The cross-investor research produced 6 unlabeled analyses of the same company. Each is immediately identifiable:

- **Graham**: Dismisses on quantitative grounds ("28x trailing earnings is well above my threshold of 15... no margin of safety")
- **Buffett**: Embraces the consumer moat ("one billion people reach for this product before anything else... a consumer habit masquerading as a technology company")
- **Munger**: Inverts to find failure modes ("What would have to be true for this to fail catastrophically? The Lollapalooza of brand loyalty would need to unwind...")
- **Lynch**: Mall-walks the research ("You walk into the Apple Store and it's packed... PEG ratio is about 1.5, a little rich, but the story is intact... This is a stalwart transitioning to fast grower in services")
- **Marks**: Warns about consensus complacency ("Everyone loves this company... Risk is highest when everyone believes it's lowest... The second-level thinker asks: 'Is it as great as the price implies?'")
- **Li Lu**: Connects to civilizational modernization ("hundreds of millions of people entering Civilization 3.0 will buy their first smartphone... The question is whether the competitive position is predictable over ten years")

**This validates the Phase 2 approach**: the philosophical differentiation is deep enough to produce identifiably distinct outputs.

---

## Key Finding 7: Source Attribution Is Comprehensive

The research identified specific, attributable sources for every major concept:

| Investor | Primary Sources | Key Documents |
|----------|----------------|---------------|
| Graham | The Intelligent Investor (Ch. 8, 20), Security Analysis (1934) | Defensive investor criteria (Ch. 14), Mr. Market parable (Ch. 8), Margin of Safety (Ch. 20) |
| Buffett | Berkshire Hathaway shareholder letters | 1986 (owner earnings formula), 1989 (cigar butt rejection), 1992 (circle of competence), 1996 (buy criteria), 2000 (Aesop framework), 2007 (moat taxonomy) |
| Munger | Poor Charlie's Almanack, "Psychology of Human Misjudgment" (Harvard 1995) | 25 Causes of Human Misjudgment, USC commencement, Wesco/Daily Journal meetings |
| Lynch | One Up on Wall Street (1989), Beating the Street (1993) | Ch. 7-8 (six categories), Ch. 10/13 (PEG), Ch. 15/17 (when to sell), 25 Golden Rules |
| Marks | Oaktree memos (1990-present), The Most Important Thing (2011), Mastering the Market Cycle (2018) | 18 specific memos with dates/themes, "Taking the Temperature" checklist |
| Li Lu | Columbia/Greenwald lectures, Peking University speeches, Himalaya Capital letters | Civilization framework, BYD case study, Munger tribute (2024), 23 sourced references |

---

## Recommendations for Phase 2 Execution

1. **Use the full bibliography files as source material when writing .agent.json configs** — the mental models, vocabulary, and quotes are ready to distill into persona fields

2. **Implement the "forbidden concepts" as negative constraints in `personality.traits`** — these are the strongest anti-convergence mechanism

3. **Each persona's `style` field must encode the FIRST QUESTION and REASONING ENTRY POINT**, not just communication tone

4. **The `beliefs` field should include the 2-3 beliefs from the Philosophical Fault Lines where this investor MOST DISAGREES with the others**

5. **The `other_facts` field should include 5+ signature vocabulary terms with usage context**

6. **The `_sources` field should reference the specific documents identified above**, not generic "books and letters"

7. **The blind taste test samples can serve as acceptance criteria** — if the persona produces output similar to these samples, the config is working

---

*Research compiled: 2026-03-21*
*7 parallel research agents | ~50,000 words of primary source analysis*
*Quality gate: Source verification via web search of primary materials*
