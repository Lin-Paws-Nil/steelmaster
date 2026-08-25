"""
Reinforcement Parser Service

Static methods using regex to parse standard Indian structural notation
from extracted CAD text strings into typed Pydantic models.

Supports:
- Main bar specs: "2K16", "6K20"
- Full stirrup notation: "K10@125C/C", "6L-K8@125C/C"
- Shorthand stirrup notation: "@225C/C", "@175C/C"
- Beam tags: "B1(230X600)", "B12a(230X450)"
"""

import re
from typing import Optional

from backend.app.models.reinforcement import MainSteel, Stirrup, BeamTag


class ReinforcementParser:
    """Parses structural reinforcement notation from DWG text entities."""

    # Main bar pattern: count + K/N/# + diameter
    # Matches: "2K16", "6K20", "2K25", "4N12", "6#20"
    _MAIN_BAR = re.compile(
        r"^(\d{1,2})\s*[KkNn#]\s*(\d{1,2})$"
    )

    # Full stirrup with legs: "6L-K8@125C/C", "4L-K10@130C/C"
    _STIRRUP_LEGGED = re.compile(
        r"^(\d+)\s*[Ll]\s*-?\s*[Kk]\s*(\d+)\s*@\s*(\d+)\s*[Cc]\s*/\s*[Cc]$"
    )

    # Full stirrup without explicit legs: "K8@150C/C", "K10@125C/C"
    _STIRRUP_FULL = re.compile(
        r"^[Kk]\s*(\d+)\s*@\s*(\d+)\s*[Cc]\s*/\s*[Cc]$"
    )

    # Shorthand stirrup (diameter omitted): "@225C/C", "@150C/C"
    _STIRRUP_SHORTHAND = re.compile(
        r"^@\s*(\d+)\s*[Cc]\s*/\s*[Cc]$"
    )

    # Beam tag: "B1(230X600)", "B12a(230X450)", "B8(600X675)"
    _BEAM_TAG = re.compile(
        r"^(B\d+[a-zA-Z]?)\s*\(\s*(\d+)\s*[xX×]\s*(\d+)\s*\)$"
    )

    @staticmethod
    def parse_main_bar(text: str) -> Optional[MainSteel]:
        """
        Parse a main bar specification string.

        Args:
            text: Raw text string, e.g. "2K16", "6K20"

        Returns:
            MainSteel object or None if text doesn't match.

        Examples:
            >>> ReinforcementParser.parse_main_bar("2K16")
            MainSteel(bar_count=2, diameter=16)
            >>> ReinforcementParser.parse_main_bar("6K20")
            MainSteel(bar_count=6, diameter=20)
        """
        text = text.strip()
        match = ReinforcementParser._MAIN_BAR.match(text)
        if match:
            return MainSteel(
                bar_count=int(match.group(1)),
                diameter=int(match.group(2)),
            )
        return None

    @staticmethod
    def parse_stirrup(text: str) -> Optional[Stirrup]:
        """
        Parse a stirrup specification string (full or shorthand notation).

        Args:
            text: Raw text string, e.g. "K8@150C/C", "6L-K8@125C/C", "@225C/C"

        Returns:
            Stirrup object or None if text doesn't match.

        Examples:
            >>> ReinforcementParser.parse_stirrup("K8@150C/C")
            Stirrup(legs=2, diameter=8, spacing=150)
            >>> ReinforcementParser.parse_stirrup("6L-K8@125C/C")
            Stirrup(legs=6, diameter=8, spacing=125)
            >>> ReinforcementParser.parse_stirrup("@225C/C")
            Stirrup(legs=2, diameter=None, spacing=225)
        """
        text = text.strip()

        # Try legged notation first: "6L-K8@125C/C"
        match = ReinforcementParser._STIRRUP_LEGGED.match(text)
        if match:
            return Stirrup(
                legs=int(match.group(1)),
                diameter=int(match.group(2)),
                spacing=int(match.group(3)),
            )

        # Try full notation: "K8@150C/C"
        match = ReinforcementParser._STIRRUP_FULL.match(text)
        if match:
            return Stirrup(
                legs=2,
                diameter=int(match.group(1)),
                spacing=int(match.group(2)),
            )

        # Try shorthand: "@225C/C"
        match = ReinforcementParser._STIRRUP_SHORTHAND.match(text)
        if match:
            return Stirrup(
                legs=2,
                diameter=None,
                spacing=int(match.group(1)),
            )

        return None

    @staticmethod
    def parse_beam_tag(text: str) -> Optional[BeamTag]:
        """
        Parse a beam identification tag with dimensions.

        Args:
            text: Raw text string, e.g. "B1(230X600)", "B12a(230X450)"

        Returns:
            BeamTag object or None if text doesn't match.

        Examples:
            >>> ReinforcementParser.parse_beam_tag("B1(230X600)")
            BeamTag(beam_id='B1', width=230, depth=600)
            >>> ReinforcementParser.parse_beam_tag("B12a(230X450)")
            BeamTag(beam_id='B12a', width=230, depth=450)
        """
        text = text.strip()
        match = ReinforcementParser._BEAM_TAG.match(text)
        if match:
            return BeamTag(
                beam_id=match.group(1),
                width=int(match.group(2)),
                depth=int(match.group(3)),
            )
        return None

    @staticmethod
    def classify_text(text: str) -> Optional[str]:
        """
        Classify a text string into its structural notation type.

        Returns:
            "main_bar", "stirrup", "beam_tag", or None if unrecognized.
        """
        text = text.strip()
        if ReinforcementParser._BEAM_TAG.match(text):
            return "beam_tag"
        if ReinforcementParser._MAIN_BAR.match(text):
            return "main_bar"
        if (ReinforcementParser._STIRRUP_LEGGED.match(text) or
                ReinforcementParser._STIRRUP_FULL.match(text) or
                ReinforcementParser._STIRRUP_SHORTHAND.match(text)):
            return "stirrup"
        return None
