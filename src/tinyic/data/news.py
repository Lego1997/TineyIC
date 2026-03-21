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
            articles.append({
                "title": item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "link": item.get("link", ""),
                "publish_time": str(item.get("providerPublishTime", "")),
            })

        return NewsSummary(articles=articles)

    except Exception as e:
        logger.warning("Failed to fetch news for %s: %s", ticker, e)
        return None
