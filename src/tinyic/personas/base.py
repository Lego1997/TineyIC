"""Base class for investor personas in tinyIC."""

import json
from pathlib import Path

from tinytroupe.agent import TinyPerson


class InvestorPersona(TinyPerson):
    """An investor persona that extends TinyPerson with investment-specific behavior.

    Adds investment analysis stub methods and philosophy config loading
    for Phase 2 implementation. In Phase 1, this validates that the
    TinyPerson -> GPT-5.2 chain works correctly.
    """

    def __init__(
        self,
        name: str,
        philosophy_config_path: str | None = None,
        **kwargs,
    ):
        super().__init__(name=name, **kwargs)
        self._philosophy_config_path = philosophy_config_path

        if philosophy_config_path:
            self._load_philosophy(philosophy_config_path)

    def _load_philosophy(self, config_path: str) -> None:
        """Load persona definitions from a .agent.json file and merge into this persona.

        Note: TinyPerson.load_specification() is a static factory method that
        creates a *new* TinyPerson. We instead load the JSON and merge its
        persona definitions into this existing instance using
        include_persona_definitions().

        Args:
            config_path: Path to a .agent.json file.
        """
        path = Path(config_path)
        with open(path) as f:
            spec = json.load(f)

        # The .agent.json format has persona data under the "persona" key
        persona_data = spec.get("persona", spec).copy()

        # Remove "name" from persona data to avoid conflict with the
        # already-set name (TinyPerson's merge_dicts raises on scalar conflicts).
        persona_data.pop("name", None)

        self.include_persona_definitions(persona_data)

    def analyze_company(self, data_package: dict) -> dict:
        """Analyze a company from this investor's perspective.

        Implemented in Phase 2 with actual persona logic.

        Args:
            data_package: Financial data package for a company.

        Returns:
            Analysis dict with thesis, risks, and reasoning.
        """
        raise NotImplementedError(
            "analyze_company() will be implemented in Phase 2"
        )

    def format_vote(self) -> dict:
        """Format this investor's buy/hold/sell vote with reasoning.

        Implemented in Phase 2 with structured output.

        Returns:
            Vote dict with decision and key_reasoning fields.
        """
        raise NotImplementedError(
            "format_vote() will be implemented in Phase 2"
        )
