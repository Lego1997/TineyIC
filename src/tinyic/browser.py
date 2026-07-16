"""Best-effort browser launching shared by onboarding and the web viewer.

Browser opening is deliberately a tiny injectable seam. Failure is never fatal:
callers keep the URL visible so headless machines and unusual desktop setups can
continue manually.
"""

from __future__ import annotations


def open_browser(url: str) -> bool:
    """Open *url* in the user's default browser, returning ``False`` on failure."""
    import webbrowser

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


__all__ = ["open_browser"]
