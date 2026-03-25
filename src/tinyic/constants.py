"""tinyIC default configuration constants."""

# Model configuration
MODEL = "gpt-5.2"
REASONING_EFFORT = "xhigh"
MAX_COMPLETION_TOKENS = 128_000

# Debate defaults (stubs for future phases)
DEFAULT_DEBATE_ROUNDS = 4
MIN_PERSONAS = 2
MAX_PERSONAS = 6

MODEL_OPTIONS = [
    {
        "id": "gpt-5.2",
        "display_name": "GPT-5.2",
        "description": "Deep reasoning, highest quality",
    },
    {
        "id": "codex-5.3",
        "display_name": "Codex-5.3",
        "description": "Faster, strong analytical",
    },
]
