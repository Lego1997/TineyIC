"""Differentiation regression tests for persona convergence detection.

Uses TF-IDF cosine similarity on recorded debate fixtures to ensure
persona outputs remain distinct. Runs offline without API keys.
"""

import json
from pathlib import Path

import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

SIMILARITY_THRESHOLD = 0.70
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "recorded_debate_apple.json"


def measure_differentiation(persona_texts: dict[str, str]) -> dict:
    """Compute pairwise TF-IDF cosine similarity between persona texts.

    Args:
        persona_texts: Mapping of persona name to reasoning text.

    Returns:
        Dict with:
            - "pairs": list of (name_a, name_b, similarity_float) tuples
              (upper-triangle only, no self-comparisons)
            - "max_similarity": float, the highest pairwise similarity
    """
    names = list(persona_texts.keys())
    texts = [persona_texts[name] for name in names]

    vectorizer = TfidfVectorizer(stop_words="english", min_df=1)
    tfidf_matrix = vectorizer.fit_transform(texts)
    sim_matrix = cosine_similarity(tfidf_matrix)

    pairs: list[tuple[str, str, float]] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            pairs.append((names[i], names[j], float(sim_matrix[i][j])))

    max_sim = max(sim for _, _, sim in pairs) if pairs else 0.0
    return {"pairs": pairs, "max_similarity": max_sim}


class TestMeasureDifferentiation:
    """Unit tests for the measure_differentiation utility function."""

    def test_measure_differentiation_structure(self):
        """Result has 'pairs' (list) and 'max_similarity' (float), with 1 pair for 2 inputs."""
        result = measure_differentiation({"A": "hello world foo bar", "B": "goodbye moon baz qux"})
        assert "pairs" in result
        assert "max_similarity" in result
        assert isinstance(result["pairs"], list)
        assert isinstance(result["max_similarity"], float)
        assert len(result["pairs"]) == 1

    def test_measure_differentiation_identical_texts(self):
        """Two identical texts should have similarity close to 1.0."""
        result = measure_differentiation({
            "A": "the cat sat on the mat",
            "B": "the cat sat on the mat",
        })
        assert result["max_similarity"] > 0.99

    def test_measure_differentiation_distinct_texts(self):
        """Two very different texts should have similarity well below threshold."""
        result = measure_differentiation({
            "A": "quantum physics experiments with particle accelerators",
            "B": "organic farming techniques for sustainable agriculture",
        })
        assert result["max_similarity"] < 0.30


class TestPersonaDifferentiation:
    """Regression tests using the recorded debate fixture."""

    def test_fixture_has_minimum_personas(self):
        """Fixture contains at least 3 personas."""
        with open(FIXTURE_PATH) as f:
            fixture = json.load(f)
        assert len(fixture["personas"]) >= 3

    def test_persona_differentiation_on_fixture(self):
        """No pairwise TF-IDF cosine similarity exceeds the threshold on the fixture."""
        with open(FIXTURE_PATH) as f:
            fixture = json.load(f)
        persona_texts = {p["name"]: p["reasoning_text"] for p in fixture["personas"]}
        result = measure_differentiation(persona_texts)
        for name_a, name_b, sim in result["pairs"]:
            assert sim < SIMILARITY_THRESHOLD, (
                f"Persona pair ({name_a}, {name_b}) exceeded similarity threshold: "
                f"{sim:.4f} >= {SIMILARITY_THRESHOLD} (over by {sim - SIMILARITY_THRESHOLD:.4f})"
            )

    def test_no_unanimous_convergence_pattern(self):
        """Max similarity across all pairs stays below threshold."""
        with open(FIXTURE_PATH) as f:
            fixture = json.load(f)
        persona_texts = {p["name"]: p["reasoning_text"] for p in fixture["personas"]}
        result = measure_differentiation(persona_texts)
        assert result["max_similarity"] < SIMILARITY_THRESHOLD, (
            f"Max pairwise similarity {result['max_similarity']:.4f} exceeds "
            f"threshold {SIMILARITY_THRESHOLD}"
        )
