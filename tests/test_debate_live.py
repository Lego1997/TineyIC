"""Live API integration test for the debate engine.

Requires OPENAI_API_KEY. Runs a full debate with 2 personas.
Marked with pytest.mark.live_api so it can be skipped in CI.
"""

import pytest

from tinyic.debate.models import Confidence, VoteChoice

pytestmark = pytest.mark.live_api


@pytest.mark.timeout(300)
def test_live_debate(has_api_key):
    """Run a complete 2-persona debate with live API calls.

    This is an expensive test (~$1-2 in API costs). Run manually:
        uv run pytest tests/test_debate_live.py -x -v -m live_api --timeout=300
    """
    from tinyic.debate import run_debate

    result = run_debate(
        ticker="AAPL",
        persona_names=["warren_buffett", "benjamin_graham"],
    )

    # Verify structure
    assert result.ticker == "AAPL"
    assert result.company_name  # non-empty
    assert len(result.scorecard.votes) == 2
    assert len(result.phases_completed) == 4
    assert result.transcript  # non-empty

    # Verify each vote is valid
    for vote in result.scorecard.votes:
        assert vote.vote in [VoteChoice.BUY, VoteChoice.HOLD, VoteChoice.SELL]
        assert vote.confidence in [Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]
        assert len(vote.reasoning) > 0
        assert vote.investor  # non-empty name

    # Verify scorecard
    assert result.scorecard.bull_count + result.scorecard.bear_count + result.scorecard.hold_count == 2

    # Print results for manual inspection
    print("\n" + result.scorecard.to_markdown())
