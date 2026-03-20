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
actions = persona.act()

print(f"\nPersona responded with {len(actions)} action(s)")
for i, action in enumerate(actions):
    print(f"\n--- Action {i + 1} ---")
    print(f"  Type: {action.get('type', 'unknown')}")
    if "content" in action:
        content = str(action["content"])
        print(f"  Content: {content[:500]}{'...' if len(content) > 500 else ''}")
    elif "action" in action:
        action_data = action["action"]
        if isinstance(action_data, dict) and "content" in action_data:
            content = str(action_data["content"])
            print(f"  Content: {content[:500]}{'...' if len(content) > 500 else ''}")
        else:
            print(f"  Action: {str(action_data)[:500]}")

print("\n--- SMOKE TEST PASSED ---")
print("InvestorPersona successfully called GPT-5.2 via TinyTroupe")
