"""xAI API X/Twitter sentiment fetcher."""

import logging
import os
from typing import Optional

from .models import SocialSentiment

logger = logging.getLogger(__name__)


def fetch_social_sentiment(
    ticker: str, company_name: str
) -> Optional[SocialSentiment]:
    """Fetch X/Twitter sentiment via xAI Responses API with x_search tool.

    Uses the OpenAI SDK with base_url override (xAI is OpenAI-compatible).
    Requires XAI_API_KEY environment variable.

    Args:
        ticker: Stock ticker symbol.
        company_name: Company name for search context.

    Returns:
        SocialSentiment with summary and bull/bear points, or None on failure.
    """
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        logger.warning("XAI_API_KEY not set, skipping X/Twitter sentiment for %s", ticker)
        return None

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )

        response = client.responses.create(
            model="grok-4-1-fast-non-reasoning",
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
