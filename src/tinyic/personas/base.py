"""Base class for investor personas in tinyIC."""

import json
from pathlib import Path

from tinytroupe.agent import TinyPerson
from tinytroupe.session import Session

#: Anti-sycophancy temperament (FR-4.3) when a config omits one. Kept as a plain
#: string here so the persona layer does not depend on the debate package; the
#: reinforcement builder maps any unknown value to this same balanced clause.
DEFAULT_TEMPERAMENT = "balanced"


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
        session: Session | None = None,
        **kwargs,
    ):
        super().__init__(name=name, session=session, **kwargs)
        self._philosophy_config_path = philosophy_config_path
        # Anti-sycophancy temperament (FR-4.3); a config may override it below.
        self.temperament = DEFAULT_TEMPERAMENT

        try:
            if philosophy_config_path:
                self._load_philosophy(philosophy_config_path)
        except Exception:
            # TinyPerson registration occurs in ``super().__init__``. Keep
            # persona construction atomic when config loading or merging fails.
            self.session.unregister_agent(self)
            raise

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

        # Temperament is top-level metadata (sibling to "persona"), so it steers
        # the debate reinforcement without being merged into the TinyTroupe
        # persona definitions. Normalize; an absent/blank value stays balanced.
        raw_temperament = str(spec.get("temperament") or "").strip().lower()
        if raw_temperament:
            self.temperament = raw_temperament

        # The .agent.json format has persona data under the "persona" key
        persona_data = spec.get("persona", spec).copy()

        # Remove "name" from persona data to avoid conflict with the
        # already-set name (TinyPerson's merge_dicts raises on scalar conflicts).
        persona_data.pop("name", None)

        self.include_persona_definitions(persona_data)

    def analyze_company(self, data_package: dict) -> dict:
        """Analyze a company from this investor's perspective.

        Constructs a prompt from the data_package, sends it through
        TinyTroupe's listen/act pipeline, and returns structured analysis.

        Args:
            data_package: Dict with keys like company_name, ticker,
                description, financials.

        Returns:
            Dict with investor, company, analysis, and raw_actions.
        """
        company = data_package.get(
            "company_name", data_package.get("ticker", "Unknown Company")
        )

        prompt = f"Analyze {company} as a potential investment. "
        if "description" in data_package:
            prompt += f"Company description: {data_package['description']}. "
        if "financials" in data_package:
            prompt += f"Key financials: {data_package['financials']}. "
        prompt += (
            "Provide your investment analysis based on your investment philosophy. "
            "Include your assessment of the business quality, valuation, "
            "key risks, and whether you would invest."
        )

        self.listen(prompt)
        self.act()
        actions = self.pop_latest_actions()

        analysis_text = ""
        for action in actions:
            if action.get("type") == "TALK" and action.get("content"):
                analysis_text += action["content"] + "\n"

        return {
            "investor": self.name,
            "company": company,
            "analysis": analysis_text.strip(),
            "raw_actions": actions,
        }

    def format_vote(self) -> dict:
        """Format this investor's buy/hold/sell vote with reasoning.

        Should be called after analyze_company() so the persona has context.
        Asks the persona to distill their analysis into a structured vote.

        Returns:
            Dict with investor, vote_text, and raw_actions.
        """
        self.listen(
            "Based on your analysis, provide your final investment verdict. "
            "State clearly: BUY, HOLD, or SELL. "
            "Then give your top 3 reasons for this verdict, each in one sentence."
        )
        self.act()
        actions = self.pop_latest_actions()

        vote_text = ""
        for action in actions:
            if action.get("type") == "TALK" and action.get("content"):
                vote_text += action["content"] + "\n"

        return {
            "investor": self.name,
            "vote_text": vote_text.strip(),
            "raw_actions": actions,
        }
