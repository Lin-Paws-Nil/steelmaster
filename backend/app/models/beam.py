"""
Assembled Beam Model

Represents a fully assembled beam with spatially-grouped reinforcement:
- Beam identification tag (anchor point)
- Top main bars (above beam centerline)
- Bottom main bars (below beam centerline)
- Stirrups (assigned by proximity)
"""

from typing import List

from pydantic import BaseModel

from backend.app.models.reinforcement import BeamTag, MainSteel, Stirrup


class AssembledBeam(BaseModel):
    """
    A beam with all its reinforcement grouped by spatial proximity
    and sorted into top/bottom positions based on Y-coordinate relationship
    to the beam tag anchor.
    """
    beam_tag: BeamTag
    top_main_bars: List[MainSteel] = []
    bottom_main_bars: List[MainSteel] = []
    stirrups: List[Stirrup] = []
