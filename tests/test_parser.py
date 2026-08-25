"""
Test suite for ReinforcementParser.

Covers all notation formats found in structural DWG files:
- Main bar specifications
- Full and shorthand stirrup notation
- Beam identification tags
"""

import pytest

from backend.app.services.parser_service import ReinforcementParser
from backend.app.models.reinforcement import MainSteel, Stirrup, BeamTag


class TestParseMainBar:
    """Tests for main reinforcement bar parsing."""

    @pytest.mark.parametrize("text,expected_count,expected_dia", [
        ("2K16", 2, 16),
        ("2K25", 2, 25),
        ("6K20", 6, 20),
        ("6K16", 6, 16),
    ])
    def test_valid_bar_specs(self, text: str, expected_count: int, expected_dia: int):
        result = ReinforcementParser.parse_main_bar(text)
        assert result is not None
        assert result.bar_count == expected_count
        assert result.diameter == expected_dia

    def test_returns_correct_type(self):
        result = ReinforcementParser.parse_main_bar("2K16")
        assert isinstance(result, MainSteel)

    @pytest.mark.parametrize("text", [
        "K8@150C/C",
        "B1(230X600)",
        "@225C/C",
        "BEAM",
        "",
        "K16",
    ])
    def test_non_matching_returns_none(self, text: str):
        assert ReinforcementParser.parse_main_bar(text) is None


class TestParseStirrupFull:
    """Tests for full stirrup notation parsing."""

    @pytest.mark.parametrize("text,expected_legs,expected_dia,expected_spacing", [
        ("K8@150C/C", 2, 8, 150),
        ("K10@125C/C", 2, 10, 125),
    ])
    def test_simple_stirrup(self, text: str, expected_legs: int, expected_dia: int, expected_spacing: int):
        result = ReinforcementParser.parse_stirrup(text)
        assert result is not None
        assert result.legs == expected_legs
        assert result.diameter == expected_dia
        assert result.spacing == expected_spacing

    def test_legged_stirrup(self):
        result = ReinforcementParser.parse_stirrup("6L-K8@125C/C")
        assert result is not None
        assert result.legs == 6
        assert result.diameter == 8
        assert result.spacing == 125

    def test_returns_correct_type(self):
        result = ReinforcementParser.parse_stirrup("K8@150C/C")
        assert isinstance(result, Stirrup)


class TestParseStirrupShorthand:
    """Tests for shorthand stirrup notation (diameter omitted)."""

    @pytest.mark.parametrize("text,expected_spacing", [
        ("@225C/C", 225),
        ("@175C/C", 175),
        ("@150C/C", 150),
    ])
    def test_shorthand_stirrup(self, text: str, expected_spacing: int):
        result = ReinforcementParser.parse_stirrup(text)
        assert result is not None
        assert result.diameter is None
        assert result.spacing == expected_spacing
        assert result.legs == 2

    def test_shorthand_returns_correct_type(self):
        result = ReinforcementParser.parse_stirrup("@225C/C")
        assert isinstance(result, Stirrup)


class TestParseBeamTag:
    """Tests for beam identification tag parsing."""

    @pytest.mark.parametrize("text,expected_id,expected_width,expected_depth", [
        ("B1(230X600)", "B1", 230, 600),
        ("B8(600X675)", "B8", 600, 675),
        ("B12a(230X450)", "B12a", 230, 450),
        ("B17(600X675)", "B17", 600, 675),
    ])
    def test_beam_tags(self, text: str, expected_id: str, expected_width: int, expected_depth: int):
        result = ReinforcementParser.parse_beam_tag(text)
        assert result is not None
        assert result.beam_id == expected_id
        assert result.width == expected_width
        assert result.depth == expected_depth

    def test_lowercase_separator(self):
        result = ReinforcementParser.parse_beam_tag("B5(230x600)")
        assert result is not None
        assert result.beam_id == "B5"
        assert result.width == 230
        assert result.depth == 600

    def test_returns_correct_type(self):
        result = ReinforcementParser.parse_beam_tag("B1(230X600)")
        assert isinstance(result, BeamTag)

    @pytest.mark.parametrize("text", [
        "2K16",
        "K8@150C/C",
        "@225C/C",
        "B1",
        "",
    ])
    def test_non_matching_returns_none(self, text: str):
        assert ReinforcementParser.parse_beam_tag(text) is None


class TestClassifyText:
    """Tests for text classification utility."""

    def test_classify_main_bar(self):
        assert ReinforcementParser.classify_text("2K16") == "main_bar"

    def test_classify_stirrup_full(self):
        assert ReinforcementParser.classify_text("K8@150C/C") == "stirrup"

    def test_classify_stirrup_shorthand(self):
        assert ReinforcementParser.classify_text("@225C/C") == "stirrup"

    def test_classify_stirrup_legged(self):
        assert ReinforcementParser.classify_text("6L-K8@125C/C") == "stirrup"

    def test_classify_beam_tag(self):
        assert ReinforcementParser.classify_text("B1(230X600)") == "beam_tag"

    def test_classify_unknown(self):
        assert ReinforcementParser.classify_text("some random text") is None
