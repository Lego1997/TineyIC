# Phase 11 Context: Model Selection

## Requirements

### CONFIG-01: Model Dropdown in Sidebar
The Streamlit sidebar contains a model dropdown populated from a configurable model list. Default includes GPT-5.2 and Codex 5.3.

### CONFIG-02: Runtime Model Override
Selecting a model overrides the `config.ini` MODEL setting at runtime for that debate session. All `TinyPerson.act()` calls during the debate use the selected model.

### CONFIG-03: Model Info Display
Each model option displays a brief description of its strengths to help users choose (e.g., reasoning depth, speed, cost).

## Success Criteria

1. The Streamlit sidebar contains a model dropdown populated from a configurable model list (default includes GPT-5.2 and Codex 5.3)
2. Selecting a model overrides the `config.ini` MODEL setting at runtime for that debate session -- all `TinyPerson.act()` calls during the debate use the selected model
3. Each model option displays a brief description of its strengths to help users choose
4. The default selection matches the current `config.ini` MODEL value so existing behavior is preserved when no change is made
5. Model selection is locked during an active debate (consistent with existing sidebar lock behavior) and only takes effect on the next debate

## Key Architecture Notes

- **ConfigManager** (`tinytroupe/__init__.py:226`): `config_manager.update("model", value)` modifies the runtime model. Read via `config_manager.get("model")`.
- **OpenAI client** (`tinytroupe/clients/openai_client.py:147`): `send_message()` uses `@config_manager.config_defaults(model="model")` decorator — if `model=None`, it reads from config.
- **Sidebar disabled flag** (`app.py:130`): `disabled = st.session_state.status in ("fetching", "debating", "extracting", "generating")` — already locks all sidebar inputs during debate.
- **Worker thread** (`app.py:457`): `_debate_worker()` runs in a daemon thread, communicates via `ui_queue`. Model override must happen early in worker (before any LLM call).
- **Single-process model**: Streamlit runs one Python process. `config_manager` is a global singleton. Worker should restore original model after debate to avoid leaking state.
