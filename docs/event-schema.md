# TinyIC Event Schema v1

The append-only JSONL event stream is the single channel between the debate engine and every renderer (TUI, headless stdout, HTML export, future notebook widget), the persistence format for replay, and the **public contract for AI agents** consuming TinyIC headlessly. Within schema v1, changes must be additive (new event types / new optional payload fields). Renderers must ignore unknown event types and unknown payload fields.

## Envelope

Every line is one JSON object:

```json
{"v": 1, "seq": 42, "ts": "2026-07-12T20:31:07.114Z", "debate_id": "aapl-20260712-a3f2", "type": "talk_delta", "payload": { ... }}
```

- `v` — schema major version (1)
- `seq` — monotonically increasing per debate, no gaps; renderers may resync by seq
- `ts` — UTC ISO-8601 with milliseconds
- `debate_id` — `<ticker>-<yyyymmdd>-<short random>`; also the run filename stem
- `type` / `payload` — below

Secrets, API keys, and OAuth material MUST never appear in any payload. Full LLM prompts are not events (they may be dumped separately under an explicit debug flag).

## Event types

### Lifecycle

| type | payload |
|---|---|
| `debate_started` | `ticker, company_name, preset, personas: [{name, model_ref, auth_profile, thinking_level, temperament}], moderator, aggregator, caps, config_hash, tinyic_version` |
| `data_ready` | `sources: [{name, status: ok\|degraded\|unavailable\|disabled_no_credential, warning?}], financials_summary, description, fetched_at` |
| `phase_started` | `phase: opening\|cross_exam\|rebuttal\|verdict, index, da_persona?` (cross_exam only) |
| `phase_completed` | `phase, index, turn_count` |
| `debate_completed` | `phases_completed, duration_s, result_ref` (path of result document) |
| `debate_error` | `stage, message, recoverable` |

### Turns & streaming

| type | payload |
|---|---|
| `turn_started` | `turn_id, persona, phase, role: statement\|challenge\|response\|rebuttal\|verdict, target_persona?` |
| `think_delta` | `turn_id, text` (append-only fragments of the persona's THINK content / provider reasoning stream) |
| `think_completed` | `turn_id, full_text` |
| `talk_delta` | `turn_id, text` |
| `talk_completed` | `turn_id, full_text` |
| `cognitive_state` | `turn_id, persona, goals, attention, emotions, context?` (one per committed turn) |
| `turn_completed` | `turn_id, persona, phase, interrupted: bool, usage_ref` |
| `turn_interrupted` | `turn_id, persona, by: user\|system, disposition: cancelled\|discarded_on_arrival` |

### Steering (Codex semantics)

| type | payload |
|---|---|
| `steering_submitted` | `msg_id, mode: steer\|queue, target_persona?, text, source: tui\|stdin\|api` |
| `steering_delivered` | `msg_id, delivered_before_turn_id` — delivered one at a time, at speaker-turn boundaries (steer) or phase boundaries (queue) |
| `steering_dropped` | `msg_id, reason` (e.g. debate ended first — must be explicit, never silent) |

### Structured artifacts & analysis

| type | payload |
|---|---|
| `thesis_recorded` | `persona, phase: opening, stance: bullish\|bearish\|neutral, claims: [..], confidence` |
| `vote_recorded` | `persona, vote: BUY\|HOLD\|SELL, confidence: HIGH\|MEDIUM\|LOW, reasoning: [..], key_risks: [..], changed_mind, source: structured\|extracted` |
| `scorecard` | `votes, consensus?, bull_count, bear_count, hold_count` |
| `memo_section` | `section: executive_summary\|investment_thesis\|key_risks\|valuation_discussion\|final_verdict, content, contributing_personas, supporting_data` |
| `disagreement` | `dimension, description, sides: [{persona, position, evidence_quote}], resolution` |
| `collapse_metric` | `persona, phase, stance_before, stance_after, caved: bool, note` ("caved under pressure" flags) |

### Usage & cost

| type | payload |
|---|---|
| `usage` | `turn_id?, persona?, purpose: turn\|extraction\|memo\|research, model_ref, input_tokens, output_tokens, cached_tokens, cost_usd?` (null on subscription lanes) |
| `usage_window` | `auth_profile, lane: subscription, window_used_msgs, window_estimate_msgs, resets_at?` (subscription meter snapshots) |

## Headless stdin steering (input, not events)

With `--steer-stdin`, each stdin line is `{"type": "steer"|"queue"|"interrupt", "target": "Warren Buffett"?, "text": "..."}` and is acknowledged by a corresponding `steering_submitted` event.

## Compatibility promises

1. `seq`/`ts`/`type` never change meaning within v1.
2. A debate is replayable iff its log begins with `debate_started` and ends with `debate_completed` or `debate_error`; renderers must handle truncated logs (crash mid-debate) gracefully.
3. `tinyic result <id> --json` returns a single document assembled from the log (scorecard, memo, disagreements, usage rollup) — agents that don't want streaming can consume only this.
