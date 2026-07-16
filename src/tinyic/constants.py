"""tinyIC default configuration constants."""

# Model configuration
MODEL = "gpt-5.6-sol"
REASONING_EFFORT = "xhigh"
MAX_COMPLETION_TOKENS = 128_000

# Debate defaults (stubs for future phases)
DEFAULT_DEBATE_ROUNDS = 4
MIN_PERSONAS = 2
MAX_PERSONAS = 6

MODEL_OPTIONS = [
    {
        "id": "gpt-5.6-sol",
        "display_name": "GPT-5.6 Sol",
        "description": "Flagship deep reasoning, highest quality",
    },
    {
        "id": "gpt-5.6-terra",
        "display_name": "GPT-5.6 Terra",
        "description": "Strong analytical, balanced cost",
    },
    {
        "id": "gpt-5.6-luna",
        "display_name": "GPT-5.6 Luna",
        "description": "Fast and inexpensive",
    },
]
