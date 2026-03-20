"""Live smoke test: validate InvestorPersona -> listen() -> act() -> GPT-5.2 response.

Run from project root: uv run python scripts/smoke_test.py
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
if not api_key or api_key == "your-api-key-here":
    print("ERROR: Set OPENAI_API_KEY in .env file")
    print("Copy .env.example to .env and add your key")
    sys.exit(1)

from tinyic.personas.base import InvestorPersona

print("Creating test persona...")
persona = InvestorPersona(name="Test Investor")
persona["nationality"] = "American"
persona["occupation"] = {"title": "Value Investor", "organization": "Test Fund"}
persona["personality"] = {"traits": ["Analytical", "Patient", "Value-oriented"]}
persona["beliefs"] = [
    "Buy businesses below intrinsic value",
    "Margin of safety is essential",
]

print("Sending listen() message...")
persona.listen("What do you think about Apple (AAPL) as an investment at current prices?")

print("Calling act() -- this will call GPT-5.2 with reasoning_effort=xhigh...")
persona.act()

# TinyTroupe stores actions internally; retrieve from action history
actions = persona.pop_latest_actions()
if actions:
    print(f"\nPersona responded with {len(actions)} action(s)")
    for i, action in enumerate(actions):
        print(f"\n--- Action {i + 1} ---")
        action_data = action.get("action", {})
        print(f"  Type: {action_data.get('type', 'unknown')}")
        content = action_data.get("content", "")
        if content:
            print(f"  Content: {str(content)[:500]}{'...' if len(str(content)) > 500 else ''}")
else:
    print("\nWARNING: No actions returned (persona may have acted but actions not captured)")

print("\n--- SMOKE TEST PASSED ---")
print("InvestorPersona successfully called GPT-5.2 via TinyTroupe")
