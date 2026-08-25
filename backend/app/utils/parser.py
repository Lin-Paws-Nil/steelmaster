"""
Structural Notation Parser

A robust regex utility class to parse standard Indian structural engineering
notation from DWG/PDF text annotations.

Handles:
- Bar specifications: "2K16", "4K20", "6#25", "4-16mm"
- Stirrup notation: "K8@200C/C", "4L-K10@130C/C"
- Beam labels: "B1(230X600)", "B12a(450X675)"
- Column labels: "C1", "C12"
- Dimensions: "300x450", "230X600X5000"
- Spacing: "@150C/C", "c/c 200"
"""

import re
from typing import Optional


class StructuralNotationParser:
    """Parses structural engineering notation from text annotations."""

    # Beam label with dimensions: B1(230X600), B12a(450X675)
    BEAM_LABEL = re.compile(
        r"(B\d+[a-zA-Z]?)\s*\(\s*(\d+)\s*[xX×]\s*(\d+)\s*\)"
    )

    # Beam label without dimensions: B1, B12, B3A
    BEAM_ID = re.compile(r"\b(B\d+[a-zA-Z]?)\b")

    # Column label: C1, C12
    COLUMN_ID = re.compile(r"\b(C\d+[a-zA-Z]?)\b")

    # Indian K-notation: 2K16, 4K20, 6K25 (count + K + diameter)
    BAR_SPEC_K = re.compile(r"(\d+)\s*[KkNn#]\s*(\d+)")

    # International notation: 4-16mm, 6#20dia, 2nos 25mm
    BAR_SPEC_INTL = re.compile(
        r"(\d{1,2})\s*(?:[-#]|nos\.?\s*)\s*(\d{2,3})\s*(?:mm|dia|φ|Φ|ø)"
    )

    # Stirrup with legs: 4L-K10@130C/C, 6L-K8@125C/C
    STIRRUP_LEGGED = re.compile(
        r"(\d+)\s*[Ll]\s*-?\s*[Kk]\s*(\d+)\s*@\s*(\d+)\s*[Cc]\s*/\s*[Cc]"
    )

    # Simple stirrup: K10@150C/C, K8@200c/c
    STIRRUP_SIMPLE = re.compile(
        r"[Kk]\s*(\d+)\s*@\s*(\d+)\s*[Cc]\s*/\s*[Cc]"
    )

    # Spacing only: @150C/C, @200c/c
    SPACING = re.compile(r"@\s*(\d+)\s*[Cc]\s*/\s*[Cc]")

    # General spacing: @150, c/c 200, c.c.150
    SPACING_GENERAL = re.compile(r"(?:@|c/c|c\.c\.?)\s*(\d{2,3})")

    # Dimensions: 300x450, 230X600, 300x450x5000
    DIMENSION = re.compile(
        r"(\d{2,4})\s*[xX×]\s*(\d{2,4})(?:\s*[xX×]\s*(\d{2,5}))?"
    )

    @classmethod
    def parse_bar_spec(cls, text: str) -> Optional[dict]:
        """
        Parse a bar specification string.

        Examples:
            "2K16" -> {"count": 2, "diameter": 16, "notation": "2K16"}
            "4-20mm" -> {"count": 4, "diameter": 20, "notation": "4-20mm"}

        Returns None if no bar spec found.
        """
        match = cls.BAR_SPEC_K.search(text)
        if match:
            return {
                "count": int(match.group(1)),
                "diameter": int(match.group(2)),
                "notation": match.group(0),
            }

        match = cls.BAR_SPEC_INTL.search(text)
        if match:
            return {
                "count": int(match.group(1)),
                "diameter": int(match.group(2)),
                "notation": match.group(0),
            }

        return None

    @classmethod
    def parse_stirrup(cls, text: str) -> Optional[dict]:
        """
        Parse a stirrup specification string.

        Examples:
            "K8@200C/C" -> {"diameter": 8, "spacing": 200, "legs": 2, "notation": "K8@200C/C"}
            "4L-K10@130C/C" -> {"diameter": 10, "spacing": 130, "legs": 4, "notation": "4L-K10@130C/C"}

        Returns None if no stirrup spec found.
        """
        match = cls.STIRRUP_LEGGED.search(text)
        if match:
            return {
                "diameter": int(match.group(2)),
                "spacing": int(match.group(3)),
                "legs": int(match.group(1)),
                "notation": match.group(0),
            }

        match = cls.STIRRUP_SIMPLE.search(text)
        if match:
            return {
                "diameter": int(match.group(1)),
                "spacing": int(match.group(2)),
                "legs": 2,
                "notation": match.group(0),
            }

        return None

    @classmethod
    def parse_spacing(cls, text: str) -> Optional[float]:
        """
        Extract stirrup/bar spacing from text.

        Examples:
            "@150C/C" -> 150.0
            "c/c 200" -> 200.0

        Returns None if no spacing found.
        """
        match = cls.SPACING.search(text)
        if match:
            return float(match.group(1))

        match = cls.SPACING_GENERAL.search(text)
        if match:
            return float(match.group(1))

        return None

    @classmethod
    def parse_beam_label(cls, text: str) -> Optional[dict]:
        """
        Parse a beam label with dimensions.

        Examples:
            "B1(230X600)" -> {"beam_id": "B1", "width": 230, "depth": 600}
            "B12a(450X675)" -> {"beam_id": "B12a", "width": 450, "depth": 675}

        Returns None if no beam label found.
        """
        match = cls.BEAM_LABEL.search(text)
        if match:
            return {
                "beam_id": match.group(1),
                "width": int(match.group(2)),
                "depth": int(match.group(3)),
            }
        return None

    @classmethod
    def parse_column_label(cls, text: str) -> Optional[str]:
        """
        Extract column ID from text.

        Examples:
            "C1" -> "C1"
            "Column C12" -> "C12"

        Returns None if no column label found.
        """
        match = cls.COLUMN_ID.search(text)
        return match.group(1) if match else None

    @classmethod
    def parse_dimensions(cls, text: str) -> Optional[dict]:
        """
        Extract width x depth (x length) from text.

        Examples:
            "300x450" -> {"width": 300, "depth": 450, "length": None}
            "230X600X5000" -> {"width": 230, "depth": 600, "length": 5000}

        Returns None if no dimensions found.
        """
        match = cls.DIMENSION.search(text)
        if match:
            return {
                "width": int(match.group(1)),
                "depth": int(match.group(2)),
                "length": int(match.group(3)) if match.group(3) else None,
            }
        return None

    @classmethod
    def extract_all(cls, text: str) -> dict:
        """
        Parse all recognizable structural patterns from a text string.

        Returns a dict with all found patterns:
        {
            "beam_labels": [...],
            "bar_specs": [...],
            "stirrups": [...],
            "spacings": [...],
            "dimensions": [...],
            "column_labels": [...],
        }
        """
        result = {
            "beam_labels": [],
            "bar_specs": [],
            "stirrups": [],
            "spacings": [],
            "dimensions": [],
            "column_labels": [],
        }

        # Beam labels with dimensions
        for match in cls.BEAM_LABEL.finditer(text):
            result["beam_labels"].append({
                "beam_id": match.group(1),
                "width": int(match.group(2)),
                "depth": int(match.group(3)),
            })

        # Bar specs (K-notation)
        for match in cls.BAR_SPEC_K.finditer(text):
            result["bar_specs"].append({
                "count": int(match.group(1)),
                "diameter": int(match.group(2)),
                "notation": match.group(0),
            })

        # Stirrups (legged first, then simple)
        for match in cls.STIRRUP_LEGGED.finditer(text):
            result["stirrups"].append({
                "diameter": int(match.group(2)),
                "spacing": int(match.group(3)),
                "legs": int(match.group(1)),
                "notation": match.group(0),
            })
        for match in cls.STIRRUP_SIMPLE.finditer(text):
            already = any(s["notation"] in match.group(0) or match.group(0) in s["notation"]
                         for s in result["stirrups"])
            if not already:
                result["stirrups"].append({
                    "diameter": int(match.group(1)),
                    "spacing": int(match.group(2)),
                    "legs": 2,
                    "notation": match.group(0),
                })

        # Spacings
        for match in cls.SPACING.finditer(text):
            val = float(match.group(1))
            if val not in result["spacings"]:
                result["spacings"].append(val)

        # Dimensions
        for match in cls.DIMENSION.finditer(text):
            result["dimensions"].append({
                "width": int(match.group(1)),
                "depth": int(match.group(2)),
                "length": int(match.group(3)) if match.group(3) else None,
            })

        # Column labels
        for match in cls.COLUMN_ID.finditer(text):
            col_id = match.group(1)
            if col_id not in result["column_labels"]:
                result["column_labels"].append(col_id)

        return result

    @classmethod
    def find_all_bar_specs(cls, text: str) -> list[str]:
        """
        Find all bar specification strings in raw notation form.

        Example: "2K16 + 2K25 at bottom" -> ["2K16", "2K25"]
        """
        return [m.group(0) for m in cls.BAR_SPEC_K.finditer(text)]
