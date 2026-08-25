"""
Bar Bending Schedule (BBS) Data Model

Represents a single row in a standard BBS table, containing all information
needed for procurement and fabrication of reinforcement steel.
"""

from pydantic import BaseModel, Field


class BBSRow(BaseModel):
    """A single row in a Bar Bending Schedule."""
    beam_id: str                     # e.g., "B1"
    bar_type: str                    # e.g., "Top Main", "Bottom Main", "Stirrup"
    diameter: int                    # mm
    count: int                       # number of bars
    shape_code: str                  # e.g., "Straight with L", "Closed Rectangular Link"
    cutting_length_m: float          # meters (per bar)
    total_weight_kg: float = Field(  # kg (all bars combined)
        description="Total weight = count * cutting_length_m * (diameter^2 / 162)"
    )
