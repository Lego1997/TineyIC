"""Deep research pipeline: web search + LLM synthesis for comprehensive research briefs."""

import logging
from typing import Any, Optional

from tinytroupe import config_manager
from tinytroupe.clients import client
from tinytroupe.utils import extract_json

from ..models.binding import ModelBinding
from ..models.credentials import CredentialProvider, EnvCredentialProvider
from ..models.routing import activate as _route_client
from ._credentials import has_api_key_lane, resolve_api_key_secret
from .models import ResearchBrief

logger = logging.getLogger(__name__)

# Maximum chars per web search result to include in synthesis prompt
MAX_SEARCH_RESULT_LENGTH = 3000

# Hosted ``web_search_preview`` is an OpenAI Platform (API-key) surface; the
# codex/ChatGPT subscription lane must not be assumed to serve it, so the search
# key resolves through the API-key lane only.
_OPENAI_BINDING = ModelBinding("openai/web-search")
_OPENAI_CREDENTIAL_REFS = ("OPENAI_API_KEY",)

SYNTHESIS_SYSTEM_PROMPT = (
    "You are a senior equity research analyst producing a structured research brief "
    "from web search results about a public company.\n\n"
    "Produce a JSON object with exactly these 5 fields:\n"
    "{\n"
    '    "business_model": "2-3 paragraphs: competitive moat, revenue model, key advantages, '
    'market position",\n'
    '    "industry_trends": "2-3 paragraphs: macro tailwinds/headwinds, sector dynamics, '
    'regulatory landscape",\n'
    '    "management": "1-2 paragraphs: CEO/leadership track record, capital allocation '
    'philosophy, insider activity",\n'
    '    "recent_catalysts": "2-3 paragraphs: developments in the last 6 months -- earnings, '
    'product launches, deals, guidance changes",\n'
    '    "analyst_perspectives": "2-3 paragraphs: bull case with specific analysts/firms '
    'and price targets, bear case with specific concerns and downside risks"\n'
    "}\n\n"
    "RULES:\n"
    "- Ground every claim in the search results provided\n"
    "- Include specific numbers, dates, and named sources where available\n"
    "- If a topic has no search results, write 'Limited information available' for that field\n"
    "- Keep each field under 600 characters\n"
    "- Return ONLY valid JSON, no other text"
)


def research_lane_available(credentials: Optional[CredentialProvider]) -> bool:
    """Whether an OpenAI API-key lane is configured for deep research."""
    resolved = credentials if credentials is not None else EnvCredentialProvider()
    return has_api_key_lane(_OPENAI_BINDING, _OPENAI_CREDENTIAL_REFS, resolved)


def _web_search(query: str, *, api_key: str, model: str) -> str:
    """Perform a web search via OpenAI Responses API with web_search_preview tool.

    Args:
        query: Search query string.
        api_key: Resolved OpenAI API-key-lane secret.
        model: OpenAI model id for the Responses call.

    Returns:
        Synthesized search result text, or empty string on failure.
    """
    if not api_key:
        return ""

    try:
        from openai import OpenAI

        oai = OpenAI(api_key=api_key)

        response = oai.responses.create(
            model=model,
            input=[{"role": "user", "content": query}],
            tools=[{"type": "web_search_preview"}],
        )

        text = response.output_text or ""
        return text[:MAX_SEARCH_RESULT_LENGTH]

    except Exception as e:
        logger.warning("Web search failed for query '%s': %s", query[:80], e)
        return ""


def build_research_brief(
    ticker: str,
    company_name: str,
    description: Optional[str],
    credentials: Optional[CredentialProvider] = None,
    aggregator_client: Any = None,
) -> Optional[ResearchBrief]:
    """Build a comprehensive research brief via web search + LLM synthesis.

    Performs two focused web searches (company-specific and environment/analyst),
    then synthesizes the results into a structured ResearchBrief.

    The hosted web-search key resolves through ``credentials`` (an
    ``AuthManager`` or plain :class:`CredentialProvider`); only an API-key lane
    is honored, so a selected OpenAI subscription runtime is never used for this
    Platform-billed surface.  When ``aggregator_client`` is supplied the
    synthesis chat call is routed through it (an ordinary chat a subscription
    lane can serve); otherwise the legacy configured client is used.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        company_name: Resolved company name.
        description: Optional company description for search context.
        credentials: Credential provider; defaults to the process environment.
        aggregator_client: Optional binding client for the synthesis call.

    Returns:
        ResearchBrief with 5 populated sections, or None on failure.
    """
    resolved = credentials if credentials is not None else EnvCredentialProvider()
    api_key = resolve_api_key_secret(
        _OPENAI_BINDING, _OPENAI_CREDENTIAL_REFS, resolved
    )
    if not api_key:
        logger.warning(
            "No OpenAI API-key lane configured, skipping deep research for %s",
            ticker,
        )
        return None

    logger.info("Starting deep research for %s (%s)", ticker, company_name)

    model = config_manager.get("model") or "gpt-5.6-sol"

    # Stage 1: Web searches
    desc_hint = f" ({description[:100]})" if description else ""

    try:
        search_company = _web_search(
            f"{company_name} ({ticker}){desc_hint} business model competitive advantage "
            f"moat revenue breakdown management capital allocation track record",
            api_key=api_key,
            model=model,
        )
    except Exception as e:
        logger.warning("Company search failed for %s: %s", ticker, e)
        search_company = ""

    try:
        search_environment = _web_search(
            f"{company_name} ({ticker}) stock investment analysis industry trends "
            f"recent catalysts earnings 2026 analyst price target bull bear case",
            api_key=api_key,
            model=model,
        )
    except Exception as e:
        logger.warning("Environment search failed for %s: %s", ticker, e)
        search_environment = ""

    # Need at least some search results to synthesize
    if not search_company and not search_environment:
        logger.warning("All web searches returned empty for %s, skipping synthesis", ticker)
        return None

    # Stage 2: LLM synthesis into structured brief
    raw_material = ""
    if search_company:
        raw_material += f"## Company Research\n{search_company}\n\n"
    if search_environment:
        raw_material += f"## Market & Analyst Research\n{search_environment}\n\n"

    user_prompt = (
        f"Produce a structured research brief for {company_name} ({ticker}).\n\n"
        f"## Web Search Results\n{raw_material}"
    )

    messages = [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        # A ``None`` aggregator client is a no-op scope: synthesis then uses the
        # legacy configured client, preserving prior behavior.
        with _route_client(aggregator_client):
            response = client().send_message(messages, temperature=0.3)
        data = extract_json(response["content"])
    except Exception as e:
        logger.warning("Research synthesis LLM call failed for %s: %s", ticker, e)
        return None

    if not data:
        logger.warning("Research synthesis returned empty/invalid JSON for %s", ticker)
        return None

    return ResearchBrief(
        business_model=data.get("business_model", ""),
        industry_trends=data.get("industry_trends", ""),
        management=data.get("management", ""),
        recent_catalysts=data.get("recent_catalysts", ""),
        analyst_perspectives=data.get("analyst_perspectives", ""),
    )
