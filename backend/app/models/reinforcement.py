"""
Reinforcement Data Models

Pydantic models for structured representation of extracted CAD text entities
and parsed structural reinforcement data.
"""

from typing import Optional

from pydantic import BaseModel, Field


class DWGTextEntity(BaseModel):
    """A text entity extracted from a DWG file with spatial and visual metadata."""
    text: str
    x: float
    y: float
    layer: str = ""
    color: int = 0  # AutoCAD Color Index (ACI): 1=red, 2=yellow, 3=green, 5=blue, 6=magenta


class MainSteel(BaseModel):
    """Parsed main reinforcement bar specification (e.g., '2K16' -> count=2, diameter=16)."""
    bar_count: int
    diameter: int  # mm


class Stirrup(BaseModel):
    """
    Parsed stirrup/link specification.

    Full notation: "K8@150C/C" -> legs=2, diameter=8, spacing=150
    Legged: "6L-K8@125C/C" -> legs=6, diameter=8, spacing=125
    Shorthand: "@225C/C" -> legs=2, diameter=None, spacing=225
    """
    legs: int = Field(default=2)
    diameter: Optional[int] = None  # None when shorthand like "@225C/C"
    spacing: int  # mm (center-to-center)


class BeamTag(BaseModel):
    """Parsed beam identification tag (e.g., 'B1(230X600)' -> beam_id='B1', width=230, depth=600)."""
    beam_id: str   # e.g., "B1", "B12a"
    width: int     # mm
    depth: int     # mm
