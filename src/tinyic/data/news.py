"""yfinance news fetcher."""

import logging
from typing import Optional

from .models import NewsSummary

logger = logging.getLogger(__name__)


def fetch_news(ticker: str, count: int = 8) -> Optional[NewsSummary]:
    """Fetch recent news articles via yfinance.

    Returns NewsSummary with up to `count` articles,
    or None if fetching fails.
    """
    try:
        import yfinance as yf

        t = yf.Ticker(ticker.upper().strip())
        news_items = t.get_news(count=count)
        if not news_items:
            logger.warning("No news found for %s", ticker)
            return None

        articles = []
        for item in news_items[:count]:
            # yfinance 1.2.x nests data under "content" key
            content = item.get("content", item)
            provider = content.get("provider", {})
            canonical_url = content.get("canonicalUrl", {})
            articles.append({
                "title": content.get("title", ""),
                "publisher": provider.get("displayName", "") if isinstance(provider, dict) else str(provider),
                "link": canonical_url.get("url", "") if isinstance(canonical_url, dict) else str(canonical_url),
                "publish_time": content.get("pubDate", ""),
            })

        return NewsSummary(articles=articles)

    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return None
