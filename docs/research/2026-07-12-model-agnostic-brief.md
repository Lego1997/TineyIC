# Research Brief: Model-Agnostic Backend, Subscription Auth & MoA

*Synthesized 2026-07-12 by Claude (main assistant) from the raw claim bundle in [2026-07-12-model-agnostic-claims.md](2026-07-12-model-agnostic-claims.md) — 14 sources deep-read, 84 falsifiable claims (81 primary-source, 3 adversarially verified, 0 refuted). Every factual statement cites a numbered source [S#]; the claim digest holds the verbatim evidence.*

*(Corrected 2026-07-12, same day: the original draft's Anthropic-prohibition framing was stale — it missed the June 15, 2026 reinstatement. This version reflects the corrected timeline; see §1.)*

**Bottom line up front.** Three findings dominate. (1) Subscription auth is **mechanically asymmetric**: OpenAI supports ChatGPT-subscription OAuth in external tools directly, while Anthropic (after an April 2026 ban and a June 15, 2026 reinstatement) permits third-party subscription usage **only through identity-honest official plumbing** — the user's own signed-in Claude Code / Agent SDK — while still prohibiting third parties from running their own Claude.ai login. Both lanes are shippable; they need different mechanisms, not one "connect your subscription" abstraction. (2) The Self-MoA paper **undercuts the assumption that mixing different models improves output**: proposer *quality* dominates *diversity* 1.4–3.2×, so per-persona models should ship as a cost/character knob with single-strong-model defaults, not be marketed as a quality feature. (3) LiteLLM covers the API-key half of model-agnosticism essentially for free (unified `reasoning_effort`, 100+ providers, normalized `reasoning_content` that can feed the thinking UI) — but no library covers the subscription half; that layer is custom regardless, which is exactly how OpenClaw and Hermes both built it.

---

## 1. Subscription auth — mechanics and the ToS asymmetry

### What the evidence says

**OpenAI / ChatGPT side (sanctioned).** Codex CLI officially supports two first-class auth paths: sign-in with a ChatGPT subscription (browser OAuth; usage *included* in Free/Go/Plus/Pro/Business/Edu/Enterprise plans) or a per-token API key [S3][S8]. A device-code flow (beta, `codex login --device-auth`) exists for headless environments [S3]. Credentials live in `~/.codex/auth.json` (plaintext by default; `cli_auth_credentials_store = file|keyring|auto` upgrades to the OS keychain), with automatic access/refresh-token rotation [S3]. OpenClaw implements the full OAuth 2.0 + PKCE dance itself: `auth.openai.com/oauth/authorize` with scope `openid profile email offline_access`, loopback callback on `localhost:1455` with a paste-the-redirect fallback, token exchange, and `{access, refresh, expires, accountId}` stored per-agent; subscription traffic then routes through **`chatgpt.com/backend-api` via a bundled native "Codex app-server" runtime**, not the Platform API [S5][S6]. Some flagship models are *subscription-only* — `openai/gpt-5.6-sol` and `gpt-5.3-codex-spark` are rejected on API-key routes [S7]. OpenClaw's docs assert "OpenAI explicitly supports subscription OAuth usage in external tools" — that sentence is uncited on their page (flagged by the verification pass), but is corroborated by press coverage of OpenAI opening ChatGPT subscriptions to OpenClaw [S5][S6].

**The budget reality.** Subscription usage is metered as **local messages per rolling 5-hour window**, not tokens: ChatGPT Plus ≈ 20–110 GPT-5.6 Terra messages/5h; Pro is 5×/20×; the pool is *shared* with other agentic features; OpenAI officially endorses API-key overflow and switching to cheaper tiers when near limits [S8]. A 6-persona × 4-phase debate is easily 30+ messages — one debate can eat a Plus window.

**Anthropic side (reinstated June 15, 2026 — through official plumbing only).** The policy moved three times in 2026 and the original draft of this brief caught only the first move. Timeline: **April 4** — Anthropic banned Claude-subscription usage in third-party agentic tools, citing usage patterns subscriptions weren't built for [S16]. **May 13** — announced a monthly "Agent SDK credit" system ($20 Pro / $200 Max) for Agent-SDK-authenticated third-party apps, effective June 15 [S15]. **June 15** — paused the credit plan on its effective day and **reinstated**: Claude Agent SDK, `claude -p`, and third-party app usage draw from the signed-in subscription's normal Pro/Max limits, with a commitment to notice before future changes [S15][S17]. Today's official support page frames it as discretionary-but-recognized: Anthropic "may at its discretion allow paid subscribers … to use certain third-party tools," reserves the right to draw such use from usage credits rather than subscription limits in the future, and prohibits tools that "**misrepresent their identity** to Anthropic's servers" or force traffic against subscription limits [S17]. What has NOT changed: the legal page still bars third parties from **offering Claude.ai login** themselves (running their own OAuth capture) [S4], and the `claude setup-token` facility (1-year, inference-only token via `CLAUDE_CODE_OAUTH_TOKEN`) remains a Claude Code feature [S1].

**How OpenClaw and Hermes actually handle Anthropic (current).** Neither runs its own Claude OAuth — which is exactly why they survived the policy whiplash. OpenClaw's current Claude-Max path wraps the **user's own authenticated Claude Code CLI as a subprocess** (community `claude-max-api-proxy`, exposing an OpenAI-compatible endpoint; their docs still caution "technical compatibility only … verify Anthropic's current billing rules") [S18][S5]; it also supports `claude -p` as a per-model runtime (`agentRuntime.id: "claude-cli"`) and user-supplied setup-tokens [S7]. Hermes "routes as Claude Code" and prefers Claude Code's own credential store [S11]. The pattern to copy: identity-honest reuse of the user's own login through official surfaces — never token capture.

**The token-sink hazard (applies to both providers).** OAuth providers mint a new refresh token on every login/refresh and invalidate the prior one — so if TineyIC *owns* a copied refresh token, TineyIC and the user's own Codex/Claude CLI can silently log each other out. OpenClaw's mitigation: prefer *read-through reuse* of the CLI's credential store over taking ownership; never wrap OAuth material in static secret refs [S5].

### What it means for TineyIC + recommendation

Ship **asymmetric lanes** behind one credential-resolver interface:

- **OpenAI lane (full subscription support):** "Sign in with ChatGPT" via device-code OAuth (works in notebooks/headless kernels) plus read-through of an existing `~/.codex/auth.json` when Codex CLI is present. Show a live 5-hour-window usage meter and support **automatic API-key overflow** mid-debate — the hybrid OpenAI itself endorses [S3][S8][S5].
- **Anthropic lane (subscription via official plumbing):** a first-class "Use your Claude subscription" option implemented as an **Agent SDK / `claude -p` runtime binding** that reuses the user's own Claude Code login — identity-honest, drawing from their Pro/Max limits per the June 15 reinstatement [S15][S17]. Never an in-product "Log in with Claude.ai" OAuth flow (still prohibited) [S4]. API key remains the parallel lane. Given the April→June whiplash, the lane sits behind a provider-policy abstraction with a usage-source badge (subscription limits vs credits) so a future policy flip degrades gracefully instead of stranding users [S15][S17][S18].
- **Storage:** OS keyring first, `0600` file fallback (the Codex `auto` pattern); tokens never in notebook outputs, never dumped into kernel env [S3][S5].

## 2. Mixture of Agents — what to copy, what to correct

### What the evidence says

**Together MoA (the canonical design)** [S9][S10]: layers of heterogeneous "proposers" generate in parallel; each later layer sees all previous outputs; a final "aggregator" synthesizes. Key results: an all-open-source MoA beat GPT-4o (65.1% vs 57.5% AlpacaEval 2.0 LC); the "collaborativeness" finding — models produce better answers when shown other models' outputs, even weaker ones — is the empirical justification for debate itself; MoA-Lite (2 layers, one aggregator) already beats a frontier model, so *shallow committees work*; proposer skill and aggregator skill are separable (WizardLM: great proposer, poor aggregator). The aggregator prompt ("critically evaluate… some of it may be biased or incorrect") is Apache-2.0 and directly vendorable as a skeptical memo-writer template [S10].

**Hermes Agent's MoA (the production shape)** [S11][S12]: single-layer only — N `reference_models` run in parallel *without tool schemas*, their outputs appended as private tail-context (preserving prompt caching) for one `aggregator` who alone acts. Config is named **presets** of explicit `{provider, model}` pairs per reference model + aggregator, with `reference_max_tokens` as the latency lever (wall time scales with advisor tokens) and per-surface selection (`/moa` one-shot). Auxiliary tasks (vision, web-summarize, MoA) each route to independently configurable models defaulting to `auto` (inherit main model) [S11][S12].

**Self-MoA (the correction)** [S13]: aggregating N samples of the *single best* model **beats** mixed-model MoA by 6.6 points on AlpacaEval (and +3.8% avg across MMLU/CRUX/MATH); regression shows output quality is 1.4–3.2× more sensitive to proposer *quality* than *diversity*; mixing helps only when proposers are of *comparable quality* (then +0.17–0.35%) or are *task-specialized on their own domains*; Self-MoA-Seq's sliding-window aggregation (reserve slots for the running synthesis) matches full-batch quality within bounded context.

### What it means for TineyIC + recommendation

The committee maps onto MoA cleanly — 6 personas = proposers (each with its own `{provider, model, thinking}` binding), memo-writer = aggregator — and Hermes proves the per-role-routing config shape in a Python/uv product [S11][S12]. But **Self-MoA reframes the pitch**: persona diversity should come from the persona *prompts* (which the sycophancy findings also demand), not from model heterogeneity. Therefore: (a) default = one strong model for all six + a top-tier synthesizer; (b) per-persona model assignment ships as a *cost/latency/character* knob, with guidance to stay within one quality tier; (c) the one evidence-backed heterogeneous win — task specialization — suggests e.g. a strong-math model for Graham's quant screens; (d) the synthesizer aggregates per-phase with a Self-MoA-Seq-style sliding window instead of one giant end-of-debate prompt (this also fixes today's 8K-char transcript truncation, bug B4-adjacent) [S13]; (e) vendor Together's skeptical aggregator prompt [S10]; (f) adopt Hermes' preset schema (named committee configs with default) [S12].

## 3. Provider abstraction & the thinking dial

### What the evidence says

Neither OpenClaw nor Hermes adopted a third-party router as their core: OpenClaw keeps a generic inference loop + per-provider plugins (`registerProvider(...)`) that own auth, catalogs, OAuth refresh, failover, and thinking profiles, normalizing custom endpoints into **four wire-format families** (OpenAI Chat / OpenAI Responses / Anthropic / auto-detect) [S5][S7][S2]; Hermes runs three transports (`chat_completions` / `anthropic_messages` / `codex_responses`) plus a `fallback_providers` chain that swaps provider+model mid-session on errors [S11]. Both treat LiteLLM as merely an optional endpoint [S5][S7][S11].

**LiteLLM itself** [S14]: 100+ providers behind one OpenAI-shaped `completion()`; actively maintained (v1.92.0 released 2026-07-12 — literally today); per-call `model="provider/model"`; Router with retries/fallbacks; sync/async/streaming; **unified `reasoning_effort`** plus Anthropic `thinking={budget_tokens}` passthrough; normalized **`reasoning_content` returned across all providers** (+ `thinking_blocks` for Anthropic) — i.e., the thinking-UI feed comes standardized. Limitation: env-var API keys only; **no OAuth/subscription auth of any kind** [S14].

**Thinking normalization precedents.** OpenClaw: one canonical ladder `off|minimal|low|medium|high|xhigh|adaptive|max|ultra`, mapped per provider (OpenAI Responses `reasoning.effort`, Anthropic effort + adaptive extended thinking, Gemini `thinkingBudget:-1`/`thinkingLevel`, DeepSeek `reasoning_effort`, Ollama `think`), with `resolveThinkingProfile(ctx)` capability-gating — unsupported levels are rejected with valid options or remapped; fields are *omitted* for models that reject them [S5][S7][S6]. Hermes: `agent.reasoning_effort` ladder `xhigh|high|medium|low|minimal|none` with per-provider adaptation (Grok "reasons by default, no param"; vLLM `extra_body.enable_thinking`) [S11]. Critical Codex nuance (verified claim): on the **subscription surface there is no numeric reasoning knob** — effort is expressed as model *tier* (Sol/Terra/Luna) plus a Fast-mode toggle — so the dial must degrade to tier-mapping on that lane [S8].

### What it means for TineyIC + recommendation

Use **LiteLLM as the API-key inference core** (it erases the 100-provider adapter zoo, unifies reasoning params, and hands the thinking UI normalized `reasoning_content`), wrapped in a thin TineyIC `ModelBinding` layer that adds what LiteLLM lacks: the two subscription runtimes (Codex app-server lane; optional Claude-CLI lane), an ordered per-provider auth-profile chain with usage-limit rotation (OpenClaw's `auth.order` pattern: subscription first, API-key overflow) [S6], and a **capability-gated thinking ladder** (adopt OpenClaw's level set; map per provider; on the Codex-subscription lane map effort→model tier; omit fields where rejected) [S5][S7][S8]. Per-persona binding = `{provider/model, auth_profile, thinking_level}` as three independent knobs [S6][S7]. This is the OpenClaw architecture with LiteLLM compressing the long tail.

## 4. Onboarding

### What the evidence says

OpenClaw's `openclaw onboard` is the strongest template: **inference-first and verification-gated** — it auto-detects existing access from three sources (configured models, API-key env vars, *locally authenticated AI CLIs*), refuses to proceed until one candidate passes a **real completion test**, persists only the verified route, and re-running is an idempotent verify-and-repair pass that never silently replaces a working config [S2]. Its chooser is a two-branch fork per provider — "connect subscription (OAuth)" vs "paste API key" — each with "Best for:" copy and a concrete verify step, plus doctor-style probes with reason codes (`missing_credential`, `expired`, …) and a live runtime indicator (`/status` → "Runtime: OpenAI Codex") [S2][S6]. Codex's own onboarding contributes the browser-handoff + device-code + paste-a-code fallbacks for headless contexts [S3][S1]; Claude Code contributes the credential-precedence chain (env token → key → helper → OAuth) and keychain/0600 storage discipline [S1]. Hermes shows the whole thing can live in a single terminal wizard (`hermes model`) while in-session switching is deliberately limited to already-configured providers [S11].

### What it means for TineyIC + recommendation

Onboarding is a **setup notebook + doctor**, not a page: `00_welcome.ipynb` hosts an anywidget wizard — detect (env keys + `~/.codex/auth.json` + Claude CLI presence) → choose lane per provider (subscription vs API key, "Best for:" copy, ToS notice on the Anthropic-convenience lane) → **live 1-token verification** → persist to keyring → summary card showing each persona's resolved `{model, auth, thinking}`. Re-run = repair. A `tinyic doctor` Python API mirrors it headlessly with reason-coded probes. The stage widget carries a per-persona runtime/auth badge and, on subscription lanes, the usage meter [S2][S3][S6][S8].

---

## Recommendations summary

| Decision | Recommendation | Confidence | Key sources |
|---|---|---|---|
| OpenAI subscription auth | First-class: device-code OAuth + `~/.codex/auth.json` read-through; usage meter; API-key overflow | High | S3, S8, S5, S6 |
| Anthropic subscription auth | First-class via **Agent SDK / `claude -p` runtime reuse** of the user's own Claude Code login (sanctioned since June 15, 2026, draws from Pro/Max limits); no in-product Claude.ai OAuth (still prohibited); usage-source badge + policy-shift guard | High on mechanism; Medium on policy durability (changed 3× in 2026) | S15, S17, S4, S18 |
| Token handling | Read-through CLI creds over token ownership (refresh-rotation hazard); keyring + 0600 fallback | High | S5, S3, S1 |
| Inference core | LiteLLM SDK (no proxy) + thin `ModelBinding` wrapper; subscription lanes custom | High | S14, S5, S11 |
| Thinking dial | One capability-gated ladder (OpenClaw's), per-provider mapping; effort→tier on Codex-subscription lane | High | S5, S7, S8, S14 |
| Per-persona models | Ship as `{provider/model, auth_profile, thinking}` triple; **default single strong model**; diversity via prompts; specialization as the heterogeneity use-case | High | S13, S9, S12 |
| Memo synthesizer | Dedicated aggregator binding; Together's skeptical prompt; Self-MoA-Seq sliding window per phase | Medium-High | S10, S13 |
| Onboarding | Setup notebook wizard + `tinyic doctor`; inference-first, verification-gated, idempotent | High | S2, S3, S6 |

## Open questions the PRD must settle (→ interview)

1. **Subscription-auth scope for v1** — both lanes as recommended, OpenAI-only (defer the contested Anthropic convenience lane), or API-keys-only v1 with subscription lanes as fast-follow?
2. **Provider layer** — accept the LiteLLM dependency as the API-key core, or build fully in-house adapters (OpenClaw-style) for maximum control at ~4 providers' worth of extra work?
3. Onboarding surface confirmed as setup-notebook + doctor (defaulted; veto available).

## Sources

- **[S1]** Authentication — Claude Code Docs (Anthropic official) — https://code.claude.com/docs/en/authentication
- **[S2]** Onboarding (CLI) — OpenClaw docs — https://docs.openclaw.ai/start/wizard
- **[S3]** Codex Authentication — OpenAI/ChatGPT official docs — https://learn.chatgpt.com/docs/auth
- **[S4]** Legal and compliance — Claude Code Docs (Anthropic official) — https://code.claude.com/docs/en/legal-and-compliance
- **[S5]** openclaw/openclaw — canonical GitHub repo (ex-Clawdbot/Moltbot) — https://github.com/openclaw/openclaw
- **[S6]** OpenAI provider — OpenClaw docs — https://docs.openclaw.ai/providers/openai
- **[S7]** Model providers — OpenClaw docs — https://github.com/openclaw/openclaw/blob/main/docs/concepts/model-providers.md
- **[S8]** Codex Pricing & Limits — OpenAI/ChatGPT official docs — https://learn.chatgpt.com/docs/pricing
- **[S9]** Mixture-of-Agents Enhances LLM Capabilities (Wang et al., ICLR 2025) — https://arxiv.org/abs/2406.04692
- **[S10]** togethercomputer/MoA — reference implementation (Apache-2.0) — https://github.com/togethercomputer/MoA
- **[S11]** NousResearch/hermes-agent — https://github.com/NousResearch/hermes-agent
- **[S12]** Mixture of Agents — Hermes Agent user guide — https://hermes-agent.nousresearch.com/docs/user-guide/features/mixture-of-agents
- **[S13]** Rethinking Mixture-of-Agents: Is Mixing Different LLMs Beneficial? (Self-MoA) — https://arxiv.org/abs/2502.00674
- **[S14]** BerriAI/litellm — https://github.com/BerriAI/litellm
- **[S15]** Anthropic reinstates OpenClaw and third-party agent usage on Claude subscriptions — with a catch (VentureBeat) — https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch
- **[S16]** Anthropic cuts off third-party tools like OpenClaw for Claude subscribers (The Decoder) — https://the-decoder.com/anthropic-cuts-off-third-party-tools-like-openclaw-for-claude-subscribers-citing-unsustainable-demand/
- **[S17]** Logging in to your Claude account — Anthropic support (current third-party-tools language) — https://support.claude.com/en/articles/13189465-logging-in-to-your-claude-account
- **[S18]** Claude Max API proxy — OpenClaw docs (current community mechanism) — https://docs.openclaw.ai/providers/claude-max-api-proxy
