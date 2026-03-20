"""Shared test fixtures for tinyIC."""

import os
import pytest
from dotenv import load_dotenv


def pytest_configure(config):
    """Load .env file for tests."""
    load_dotenv()


@pytest.fixture
def has_api_key():
    """Check if OPENAI_API_KEY is available, skip if not."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or key == "your-api-key-here":
        pytest.skip("OPENAI_API_KEY not set -- skipping live API test")
    return key
