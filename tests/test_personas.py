"""Tests for investor persona configs and registry."""

import json
from pathlib import Path

import pytest
from tinytroupe.agent import TinyPerson

from tinyic.personas.base import InvestorPersona
from tinyic.personas.registry import PERSONA_REGISTRY, list_personas, load_persona

CONFIGS_DIR = Path(__file__).parent.parent / "src" / "tinyic" / "personas" / "configs"


@pytest.fixture(autouse=True)
def clear_agent_registry():
    """Clear TinyPerson's global agent registry between tests."""
    yield
    TinyPerson.all_agents.clear()


class TestPersonaRegistry:
    def test_registry_has_six_entries(self):
        assert len(PERSONA_REGISTRY) == 6

    def test_list_personas_returns_all(self):
        names = list_personas()
        assert len(names) == 6
        assert names == sorted(names)

    def test_load_graham(self):
        p = load_persona("benjamin_graham")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Benjamin Graham"

    def test_load_buffett(self):
        p = load_persona("warren_buffett")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Warren Buffett"

    def test_load_munger(self):
        p = load_persona("charlie_munger")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Charlie Munger"

    def test_persona_names_correct(self):
        expected = {
            "benjamin_graham": "Benjamin Graham",
            "warren_buffett": "Warren Buffett",
            "charlie_munger": "Charlie Munger",
        }
        for key, display_name in expected.items():
            p = load_persona(key)
            assert p.name == display_name

    def test_load_unknown_persona_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown persona"):
            load_persona("john_doe")

    def test_all_six_personas_load_successfully(self):
        """All 6 registered personas have configs and load without error."""
        for name in list_personas():
            p = load_persona(name)
            assert isinstance(p, InvestorPersona), f"{name} failed to load"


class TestGrahamPersona:
    def test_graham_beliefs_contain_margin_of_safety(self):
        p = load_persona("benjamin_graham")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert "margin of safety" in beliefs_lower

    def test_graham_style_is_quantitative(self):
        p = load_persona("benjamin_graham")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["quantitative", "formula", "statistical"])

    def test_graham_has_sources(self):
        data = json.loads((CONFIGS_DIR / "benjamin_graham.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["intelligent investor", "security analysis"])

    def test_graham_has_contrastive_beliefs(self):
        p = load_persona("benjamin_graham")
        beliefs = p.get("beliefs")
        beliefs_text = " ".join(beliefs).lower()
        assert "reject" in beliefs_text

    def test_graham_has_negative_personality_constraints(self):
        p = load_persona("benjamin_graham")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestBuffettPersona:
    def test_buffett_beliefs_contain_moat(self):
        p = load_persona("warren_buffett")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert any(w in beliefs_lower for w in ["moat", "economic moat"])

    def test_buffett_style_is_folksy(self):
        p = load_persona("warren_buffett")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["folksy", "everyday", "accessible", "storytelling"])

    def test_buffett_has_sources(self):
        data = json.loads((CONFIGS_DIR / "warren_buffett.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["berkshire", "shareholder letter"])

    def test_buffett_has_contrastive_beliefs(self):
        p = load_persona("warren_buffett")
        beliefs = p.get("beliefs")
        beliefs_text = " ".join(beliefs).lower()
        assert "reject" in beliefs_text

    def test_buffett_has_negative_personality_constraints(self):
        p = load_persona("warren_buffett")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestMungerPersona:
    def test_munger_beliefs_contain_inversion(self):
        p = load_persona("charlie_munger")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert any(w in beliefs_lower for w in ["inversion", "invert", "mental model"])

    def test_munger_style_is_acerbic(self):
        p = load_persona("charlie_munger")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["acerbic", "blunt", "cross-disciplinary"])

    def test_munger_has_sources(self):
        data = json.loads((CONFIGS_DIR / "charlie_munger.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["poor charlie", "almanack"])

    def test_munger_has_contrastive_beliefs(self):
        p = load_persona("charlie_munger")
        beliefs = p.get("beliefs")
        beliefs_text = " ".join(beliefs).lower()
        assert "reject" in beliefs_text

    def test_munger_has_negative_personality_constraints(self):
        p = load_persona("charlie_munger")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestClassicClusterDifferentiation:
    def test_graham_buffett_style_differs(self):
        g = load_persona("benjamin_graham")
        b = load_persona("warren_buffett")
        g_style = g.get("style").lower()
        b_style = b.get("style").lower()
        # Graham is quantitative, Buffett is not
        assert any(w in g_style for w in ["quantitative", "formula"])
        assert not any(w in b_style for w in ["quantitative", "formula"])
        # Buffett is folksy, Graham is not
        assert any(w in b_style for w in ["folksy", "everyday", "accessible"])
        assert not any(w in g_style for w in ["folksy", "everyday"])

    def test_buffett_munger_style_differs(self):
        b = load_persona("warren_buffett")
        m = load_persona("charlie_munger")
        b_style = b.get("style").lower()
        m_style = m.get("style").lower()
        # Buffett is folksy, Munger is not
        assert any(w in b_style for w in ["folksy", "everyday", "accessible"])
        assert not any(w in m_style for w in ["folksy", "everyday"])
        # Munger is acerbic/blunt, Buffett is not
        assert any(w in m_style for w in ["acerbic", "blunt"])
        assert not any(w in b_style for w in ["acerbic", "blunt"])

    def test_graham_munger_style_differs(self):
        g = load_persona("benjamin_graham")
        m = load_persona("charlie_munger")
        g_style = g.get("style").lower()
        m_style = m.get("style").lower()
        # Graham is quantitative/formulaic, Munger is not
        assert any(w in g_style for w in ["quantitative", "formula"])
        assert not any(w in m_style for w in ["quantitative", "formula"])
        # Munger is acerbic, Graham is not
        assert any(w in m_style for w in ["acerbic", "blunt"])
        assert not any(w in g_style for w in ["acerbic", "blunt"])


class TestLynchPersona:
    def test_load_lynch(self):
        p = load_persona("peter_lynch")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Peter Lynch"

    def test_lynch_beliefs_contain_peg(self):
        p = load_persona("peter_lynch")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert any(w in beliefs_lower for w in ["peg", "ten-bagger", "growth at a reasonable"])

    def test_lynch_style_is_conversational(self):
        p = load_persona("peter_lynch")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["conversational", "anecdotal", "enthusiastic"])

    def test_lynch_has_sources(self):
        data = json.loads((CONFIGS_DIR / "peter_lynch.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["one up on wall street", "beating the street"])

    def test_lynch_has_contrastive_beliefs(self):
        p = load_persona("peter_lynch")
        beliefs_text = " ".join(p.get("beliefs")).lower()
        assert "reject" in beliefs_text

    def test_lynch_has_negative_constraints(self):
        p = load_persona("peter_lynch")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestMarksPersona:
    def test_load_marks(self):
        p = load_persona("howard_marks")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Howard Marks"

    def test_marks_beliefs_contain_second_level(self):
        p = load_persona("howard_marks")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert any(w in beliefs_lower for w in ["second-level", "cycle", "pendulum"])

    def test_marks_style_is_philosophical(self):
        p = load_persona("howard_marks")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["essay", "philosophical", "measured", "socratic"])

    def test_marks_has_sources(self):
        data = json.loads((CONFIGS_DIR / "howard_marks.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["most important thing", "oaktree", "mastering the market cycle"])

    def test_marks_has_contrastive_beliefs(self):
        p = load_persona("howard_marks")
        beliefs_text = " ".join(p.get("beliefs")).lower()
        assert "reject" in beliefs_text

    def test_marks_has_negative_constraints(self):
        p = load_persona("howard_marks")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestLiLuPersona:
    def test_load_li_lu(self):
        p = load_persona("li_lu")
        assert isinstance(p, InvestorPersona)
        assert p.name == "Li Lu"

    def test_li_lu_beliefs_contain_emerging(self):
        p = load_persona("li_lu")
        beliefs = p.get("beliefs")
        assert beliefs and len(beliefs) >= 5
        beliefs_lower = " ".join(beliefs).lower()
        assert any(w in beliefs_lower for w in ["emerging", "china", "macro", "modernization", "civilization"])

    def test_li_lu_style_is_scholarly(self):
        p = load_persona("li_lu")
        style = p.get("style").lower()
        assert len(p.get("style")) > 100
        assert any(w in style for w in ["scholarly", "reflective", "globally", "philosophical"])

    def test_li_lu_has_sources(self):
        data = json.loads((CONFIGS_DIR / "li_lu.agent.json").read_text())
        assert "_sources" in data
        primary = [s.lower() for s in data["_sources"]["primary"]]
        primary_text = " ".join(primary)
        assert any(t in primary_text for t in ["columbia", "himalaya", "civilization", "peking university"])

    def test_li_lu_has_contrastive_beliefs(self):
        p = load_persona("li_lu")
        beliefs_text = " ".join(p.get("beliefs")).lower()
        assert "reject" in beliefs_text

    def test_li_lu_has_negative_constraints(self):
        p = load_persona("li_lu")
        personality = p.get("personality")
        traits = personality.get("traits", []) if isinstance(personality, dict) else []
        traits_text = " ".join(traits).lower()
        assert "never" in traits_text


class TestAllPersonasDifferentiation:
    def test_all_six_styles_unique(self):
        """Each persona has a unique dominant style descriptor."""
        style_keywords = {
            "benjamin_graham": ["quantitative", "formula"],
            "warren_buffett": ["folksy", "accessible"],
            "charlie_munger": ["acerbic", "blunt"],
            "peter_lynch": ["conversational", "anecdotal"],
            "howard_marks": ["essay", "philosophical", "measured"],
            "li_lu": ["scholarly", "reflective"],
        }
        for name, keywords in style_keywords.items():
            p = load_persona(name)
            style = p.get("style").lower()
            assert any(w in style for w in keywords), (
                f"{name} style missing expected keywords {keywords}"
            )

    def test_no_two_personas_share_dominant_style(self):
        """No two personas share the same dominant style word."""
        style_markers = {
            "quantitative": [], "folksy": [], "acerbic": [],
            "conversational": [], "essay": [], "scholarly": [],
        }
        for name in ["benjamin_graham", "warren_buffett", "charlie_munger",
                      "peter_lynch", "howard_marks", "li_lu"]:
            p = load_persona(name)
            style = p.get("style").lower()
            for marker in style_markers:
                if marker in style:
                    style_markers[marker].append(name)
        # Each marker should appear in at most 1 persona's style
        for marker, personas in style_markers.items():
            assert len(personas) <= 1, (
                f"Style marker '{marker}' shared by: {personas}"
            )

    def test_all_configs_have_sources(self):
        """Every persona config has _sources with at least 1 primary source."""
        for name in ["benjamin_graham", "warren_buffett", "charlie_munger",
                      "peter_lynch", "howard_marks", "li_lu"]:
            config_file = CONFIGS_DIR / f"{name}.agent.json"
            data = json.loads(config_file.read_text())
            assert "_sources" in data, f"{name} missing _sources"
            assert len(data["_sources"]["primary"]) >= 1, (
                f"{name} has no primary sources"
            )


class TestAnalyzeCompanyAndFormatVote:
    def test_analyze_company_not_stub(self):
        """analyze_company() is implemented (not NotImplementedError)."""
        import inspect
        src = inspect.getsource(InvestorPersona.analyze_company)
        assert "NotImplementedError" not in src

    def test_format_vote_not_stub(self):
        """format_vote() is implemented (not NotImplementedError)."""
        import inspect
        src = inspect.getsource(InvestorPersona.format_vote)
        assert "NotImplementedError" not in src


PERSONA_KEYWORDS = {
    "benjamin_graham": ["margin of safety", "net-net", "intrinsic value", "p/e", "book value", "defensive investor"],
    "warren_buffett": ["moat", "owner earnings", "circle of competence", "wonderful company", "competitive advantage"],
    "charlie_munger": ["invert", "mental model", "lollapalooza", "psychology", "incentive", "checklist"],
    "peter_lynch": ["peg", "ten-bagger", "category", "fast grower", "stalwart", "invest in what you know"],
    "howard_marks": ["cycle", "second-level", "pendulum", "risk", "asymmetric", "contrarian"],
    "li_lu": ["china", "emerging", "compounder", "macro", "long-term", "geopolitical"],
}


class TestLiveAPIDifferentiation:
    @pytest.mark.live_api
    def test_differentiation(self, has_api_key):
        """Send same prompt to all 6 personas and check for signature keywords."""
        results = {}
        matches = 0
        for name in list_personas():
            try:
                p = load_persona(name)
            except FileNotFoundError:
                continue
            analysis = p.analyze_company({
                "company_name": "Apple Inc.",
                "ticker": "AAPL",
                "description": "Consumer technology company that designs, manufactures, and markets smartphones, personal computers, tablets, wearables, and accessories. Also operates a growing services ecosystem including App Store, Apple Music, iCloud, and Apple TV+.",
                "financials": "Market cap ~$3T, P/E ~30, revenue ~$385B, services revenue ~$85B, gross margin ~45%, net income ~$100B, $60B+ annual buybacks",
            })
            results[name] = analysis["analysis"]
            print(f"\n{'='*60}")
            print(f"=== {name.upper()} ===")
            print(f"{'='*60}")
            print(analysis["analysis"][:1000])

            # Check for signature keywords
            text = analysis["analysis"].lower()
            keywords = PERSONA_KEYWORDS.get(name, [])
            if any(kw in text for kw in keywords):
                matches += 1
                print(f"  >> MATCH: Found signature keywords")
            else:
                print(f"  >> MISS: No signature keywords found")

        assert matches >= 4, (
            f"Only {matches}/6 personas used signature vocabulary (need >= 4)"
        )

    @pytest.mark.live_api
    def test_analyze_company_returns_structure(self, has_api_key):
        """analyze_company returns dict with expected keys."""
        p = load_persona("warren_buffett")
        result = p.analyze_company({
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
        })
        assert "investor" in result
        assert "company" in result
        assert "analysis" in result
        assert result["investor"] == "Warren Buffett"
        assert result["company"] == "Apple Inc."
        assert len(result["analysis"]) > 0

    @pytest.mark.live_api
    def test_format_vote_returns_structure(self, has_api_key):
        """format_vote returns dict with expected keys after analyze_company."""
        p = load_persona("warren_buffett")
        p.analyze_company({
            "company_name": "Apple Inc.",
            "ticker": "AAPL",
        })
        vote = p.format_vote()
        assert "investor" in vote
        assert "vote_text" in vote
        assert vote["investor"] == "Warren Buffett"
        assert len(vote["vote_text"]) > 0
