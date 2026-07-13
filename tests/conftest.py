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


@pytest.fixture
def has_api_key():
    """Check if OPENAI_API_KEY is available, skip if not."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or key == "your-api-key-here":
        pytest.skip("OPENAI_API_KEY not set -- skipping live API test")
    return key


@pytest.fixture
def has_xai_key():
    """Check if XAI_API_KEY is available, skip if not."""
    key = os.getenv("XAI_API_KEY")
    if not key:
        pytest.skip("XAI_API_KEY not set -- skipping xAI API test")
    return key
