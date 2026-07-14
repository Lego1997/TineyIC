"""Search-capable provider backends for the cited persona factory.

The package is a sibling of the ordinary debate adapters because research
needs provider citation metadata that the normalized chat stream intentionally
discards. It still uses the same credential and injected HTTP seams.
"""

from ._base import (
    PURPOSE_SEARCH,
    PURPOSE_SYNTHESIS,
    PURPOSE_VERIFICATION,
    ResearchResponseError,
    SEARCH_TOOL_FEES_USD,
)
from .gemini import GEMINI_BASE_URL, GeminiResearchBackend
from .kimi import KIMI_BASE_URL, KimiResearchBackend
from .responses import (
    GROK_BASE_URL,
    OPENAI_BASE_URL,
    GrokResearchBackend,
    OpenAIResearchBackend,
)
from .selector import (
    NoSearchCapableLaneError,
    RESEARCH_PROVIDER_PRIORITY,
    UnsupportedResearchProviderError,
    make_research_backend,
    select_research_backend,
)

__all__ = [
    "GEMINI_BASE_URL",
    "GROK_BASE_URL",
    "KIMI_BASE_URL",
    "OPENAI_BASE_URL",
    "GeminiResearchBackend",
    "GrokResearchBackend",
    "KimiResearchBackend",
    "NoSearchCapableLaneError",
    "OpenAIResearchBackend",
    "PURPOSE_SEARCH",
    "PURPOSE_SYNTHESIS",
    "PURPOSE_VERIFICATION",
    "RESEARCH_PROVIDER_PRIORITY",
    "ResearchResponseError",
    "SEARCH_TOOL_FEES_USD",
    "UnsupportedResearchProviderError",
    "make_research_backend",
    "select_research_backend",
]
