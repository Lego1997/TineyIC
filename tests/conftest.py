"""Shared test fixtures for tinyIC."""

import os
import warnings
import pytest
from dotenv import load_dotenv


def pytest_configure(config):
    """Load .env file and pre-import noisy third-party modules."""
    load_dotenv()

    # Pre-import edgartools with warnings suppressed so its internal
    # import-level deprecation warnings fire outside the test context.
    # This prevents them from being captured/errored during test runs.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            import edgar  # noqa: F401
        except ImportError:
            pass


@pytest.fixture(autouse=True)
def isolate_tinyic_run_logs(tmp_path, monkeypatch):
    """Keep default logs and per-install state inside each offline test sandbox.

    Redirecting ``TINYIC_STATE_DIR`` per test means the devil's-advocate rotation
    counter starts fresh at 0 for each test (deterministic DA = agents[0]) and a
    debate never reads or writes the developer's real ``~/.tinyic/state.json``.
    """
    monkeypatch.setenv("TINYIC_RUNS_DIR", str(tmp_path / "tinyic-runs"))
    monkeypatch.setenv("TINYIC_STATE_DIR", str(tmp_path / "tinyic-state"))
    # The user config overlay (~/.tinyic/tinyic.toml) deep-merges over every
    # loaded config; point it into the sandbox so a developer's real overlay
    # never leaks into (or is written by) the offline suite.
    monkeypatch.setenv(
        "TINYIC_USER_CONFIG", str(tmp_path / "tinyic-user" / "tinyic.toml")
    )
    monkeypatch.setenv(
        "TINYIC_PERSONAS_DIR", str(tmp_path / "tinyic-user" / "personas")
    )


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """Never launch a real browser from the offline suite.

    The onboarding wizard auto-opens device-code verification URLs via
    :mod:`webbrowser`; tests that reach the device-wait stage without
    injecting their own opener must record, not open.  Returns the list of
    URLs that would have been opened.
    """
    import webbrowser

    opened: list[str] = []

    def _record(url, *args, **kwargs):
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", _record)
    return opened


@pytest.fixture
def has_api_key():
    """Check if OPENAI_API_KEY is available, skip if not."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or key == "your-api-key-here":
        pytest.skip("OPENAI_API_KEY not set -- skipping live API test")
    return key


@pytest.fixture
def has_xai_key():
    """Check if XAI_API_KEY (the Grok credential) is available, skip if not."""
    key = os.getenv("XAI_API_KEY")
    if not key:
        pytest.skip("XAI_API_KEY not set -- skipping Grok API test")
    return key
