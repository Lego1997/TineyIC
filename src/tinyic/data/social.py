"""xAI API X/Twitter sentiment fetcher."""

import logging
from typing import Optional

from ..models.binding import ModelBinding
from ..models.credentials import CredentialProvider, EnvCredentialProvider
from ._credentials import has_api_key_lane, resolve_api_key_secret
from .models import SocialSentiment

logger = logging.getLogger(__name__)

# x_search is an xAI Platform (API-key) surface; the grok-cli subscription
# OAuth lane must not be assumed to serve it, so credentials resolve through
# the API-key lane only.
SOCIAL_MODEL = "grok-4.3"
_SOCIAL_BINDING = ModelBinding(f"grok/{SOCIAL_MODEL}")
_SOCIAL_CREDENTIAL_REFS = ("XAI_API_KEY",)


def social_lane_available(credentials: Optional[CredentialProvider]) -> bool:
    """Whether an xAI API-key lane is configured for social sentiment."""
    resolved = credentials if credentials is not None else EnvCredentialProvider()
    return has_api_key_lane(_SOCIAL_BINDING, _SOCIAL_CREDENTIAL_REFS, resolved)


def fetch_social_sentiment(
    ticker: str,
    company_name: str,
    credentials: Optional[CredentialProvider] = None,
) -> Optional[SocialSentiment]:
    """Fetch X/Twitter sentiment via xAI Responses API with x_search tool.

    Uses the OpenAI SDK with base_url override (xAI is OpenAI-compatible).
    The xAI key resolves through ``credentials`` (an ``AuthManager`` or plain
    :class:`CredentialProvider`); only an API-key lane is honored so a selected
    grok subscription lane is never used for this Platform-billed surface.

    Args:
        ticker: Stock ticker symbol.
        company_name: Company name for search context.
        credentials: Credential provider; defaults to the process environment.

    Returns:
        SocialSentiment with summary and bull/bear points, or None on failure.
    """
    resolved = credentials if credentials is not None else EnvCredentialProvider()
    api_key = resolve_api_key_secret(
        _SOCIAL_BINDING, _SOCIAL_CREDENTIAL_REFS, resolved
    )
    if not api_key:
        logger.warning(
            "No xAI API-key lane configured, skipping X/Twitter sentiment for %s",
            ticker,
        )
        return None

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        response = client.responses.create(
            model=SOCIAL_MODEL,
            input=[
                {
                    "role": "user",
                    "content": (
                        f"Search X/Twitter for recent discussion about {company_name} "
                        f"(${ticker}) as a stock investment. Provide:\n"
                        f"1. Overall sentiment (bullish/bearish/mixed)\n"
                        f"2. Top 3-5 bullish arguments/viewpoints people are making\n"
                        f"3. Top 3-5 bearish/contrarian arguments/viewpoints people are making\n"
                        f"Be specific, cite actual viewpoints from X posts. "
                        f"Keep total response under 1500 characters."
                    ),
                }
            ],
            tools=[{"type": "x_search"}],
        )

        text = response.output_text
        if not text:
            logger.warning("Empty response from xAI for %s", ticker)
            return None

        # Truncate to budget
        text = text[:1500]

        return SocialSentiment(
            query=f"${ticker} {company_name}",
            summary=text,
            # bullish_points and bearish_points left empty for v1;
            # the summary contains both. Structured extraction can be
            # added in a future version if needed.
        )

    except Exception as e:
        logger.warning("Failed to fetch social sentiment for %s: %s", ticker, e)
        return None
