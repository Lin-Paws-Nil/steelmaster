from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ElementType(str, Enum):
    COLUMN = "column"
    BEAM = "beam"
    SLAB = "slab"
    FOOTING = "footing"
    STAIRCASE = "staircase"
    WALL = "wall"
    LINTEL = "lintel"


class RebarSpec(BaseModel):
    diameter: float  # mm
    count: int
    length: float  # meters
    weight_per_meter: float  # kg/m
    total_weight: float  # kg
    bar_type: str = "main"  # main, stirrup, distribution, extra


class RebarLayer(BaseModel):
    """Represents a single layer/group of bars within a beam zone."""
    diameter: float  # mm
    count: int
    position: str = "bottom"  # bottom, top, side
    zone: str = "full"  # full, mid-span, left-support, right-support, both-supports


class BeamReinforcementDetail(BaseModel):
    """Detailed reinforcement breakdown for a beam, zone by zone."""
    bottom_straight: Optional[list[RebarLayer]] = None  # bars running full span at bottom
    bottom_extra_midspan: Optional[list[RebarLayer]] = None  # extra bars at mid-span (sagging)
    top_straight: Optional[list[RebarLayer]] = None  # bars running full span at top
    top_extra_support: Optional[list[RebarLayer]] = None  # extra/cranked bars at supports (hogging)
    side_face: Optional[list[RebarLayer]] = None  # side face reinforcement (for deep beams D>750)
    stirrup_end_zone: Optional[dict] = None  # {"dia": 8, "spacing": 200, "legs": 2, "zone_length_mm": 500}
    stirrup_support_zone: Optional[dict] = None  # {"dia": 10, "spacing": 130, "legs": 4, "zone_length_mm": 1500}
    stirrup_mid_zone: Optional[dict] = None  # {"dia": 10, "spacing": 150, "legs": 4, "zone_length_mm": 3000}


class StructuralElement(BaseModel):
    element_type: ElementType
    label: str
    width: float  # mm
    depth: float  # mm
    length: float  # mm (or span)
    clear_cover: float = 25.0  # mm
    concrete_grade: str = "M20"
    steel_grade: str = "Fe500"
    main_bar_dia: Optional[float] = None
    main_bar_count: Optional[int] = None
    top_bar_dia: Optional[float] = None
    top_bar_count: Optional[int] = None
    bottom_bar_dia: Optional[float] = None
    bottom_bar_count: Optional[int] = None
    stirrup_dia: Optional[float] = None
    stirrup_spacing: Optional[float] = None
    extra_bars: Optional[list] = None
    quantity: int = 1
    reinforcement_detail: Optional[BeamReinforcementDetail] = None


class SteelEstimate(BaseModel):
    element: StructuralElement
    rebars: list[RebarSpec]
    total_weight_kg: float
    total_weight_tons: float


class ProjectEstimate(BaseModel):
    project_name: str
    elements: list[SteelEstimate]
    total_steel_kg: float
    total_steel_tons: float
    summary_by_type: dict[str, float]
    summary_by_diameter: dict[str, float]


class DWGParseResult(BaseModel):
    filename: str
    layers: list[str]
    element_count: int
    elements_detected: list[StructuralElement]
    raw_text_annotations: list[str]
    metadata: dict


# --- Target JSON Schema for Beam Extraction ---

class BeamDimensions(BaseModel):
    """Beam cross-section dimensions."""
    width: int   # mm
    depth: int   # mm

class BeamReinforcement(BaseModel):
    """Reinforcement details for a beam in notation form."""
    top_bars: list[str] = []       # ["2K16", "2K25"]
    bottom_bars: list[str] = []    # ["2K16", "4K20"]
    stirrups: str = ""             # "K10@150C/C"

class BeamDetail(BaseModel):
    """Target schema: one beam's complete identification."""
    beam_id: str                             # "B1"
    dimensions: BeamDimensions               # {"width": 230, "depth": 600}
    reinforcement: BeamReinforcement

class TextEntity(BaseModel):
    """A text entity extracted from a DWG/PDF with coordinates."""
    text: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    x0: Optional[float] = None  # bounding box (PDF)
    y0: Optional[float] = None
    x1: Optional[float] = None
    y1: Optional[float] = None
    layer: str = ""
    page: int = 0

class ExtractionResult(BaseModel):
    """Result from /upload/pdf or /upload/dwg endpoints."""
    filename: str
    text_entities: list[TextEntity]
    beams: list[BeamDetail]
    raw_annotations: list[str]
    metadata: dict = {}
