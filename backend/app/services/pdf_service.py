"""
PDF Structural Drawing Service

Provides two levels of extraction:
1. Raw text block extraction with bounding box coordinates (extract_text_blocks)
2. Full structural element parsing with LLM vision (parse_pdf_file, analyze_pdf_with_vision)

Processes PDF files containing structural drawings by:
1. Rendering pages as high-resolution images
2. Extracting embedded text (dimensions, annotations)
3. Sending to vision-capable LLM for structural interpretation
4. Analyzing line weights/darkness to differentiate element types

Line weight conventions in structural drawings:
- Thick/dark lines: Structural members in section (columns, beams)
- Medium lines: Outlines, elevation views
- Thin/light lines: Dimension lines, center lines, grid lines
- Dashed lines: Hidden elements, below-slab beams
"""

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Optional

import pymupdf
from PIL import Image, ImageFilter, ImageEnhance

from backend.app.models.schemas import (
    DWGParseResult,
    StructuralElement,
    ElementType,
    TextEntity,
    BeamDetail,
    BeamDimensions,
    BeamReinforcement,
    ExtractionResult,
)
from backend.app.utils.parser import StructuralNotationParser


VISION_SYSTEM_PROMPT = """You are an expert structural engineer analyzing a structural drawing (plan/section/detail) from a PDF.

Your task is to identify EVERY structural element visible in this drawing and provide their specifications.

CRITICAL - INDIAN REINFORCEMENT NOTATION:
- "2K25" or "2-K25" or "2#25" = 2 bars of 25mm diameter (count=2, dia=25)
- "4K16" = 4 bars of 16mm diameter (count=4, dia=16)
- "K10@150C/C" or "K10@150c/c" = stirrups of 10mm diameter at 150mm spacing (stirrup_dia=10, stirrup_spacing=150)
- "K8@200C/C" = stirrups of 8mm diameter at 200mm spacing (stirrup_dia=8, stirrup_spacing=200)
- "@200C/C" or "@150C/C" = spacing between stirrups in mm
- "B1(230X600)" = Beam B1 with width=230mm, depth=600mm
- The number AFTER "K" is the bar diameter in mm
- The number BEFORE "K" is the count of bars

READING THE DRAWING:
- Thicker/darker lines represent structural members (columns shown as filled/hatched rectangles, beams as thick lines)
- Dimension lines show sizes in mm (e.g., 300x450 means 300mm wide x 450mm deep)
- Beam labels like "B1(230X600)" give the EXACT width and depth - use these values directly
- Grid lines (thin, with circle labels) show column positions
- TOP bars are shown above the beam centerline (usually at supports/ends)
- BOTTOM bars are shown below the beam centerline (usually at mid-span, these are the main tension bars)
- A beam section drawing will show top and bottom bars separately

WHAT TO IDENTIFY:
1. ALL columns (look for grid intersections, hatched rectangles)
2. ALL beams - read the EXACT dimensions from labels like B1(230X600)
3. For each beam: identify top bars AND bottom bars SEPARATELY with their count and diameter
4. ALL slab panels (rectangular areas bounded by beams)
5. Footings (if shown - usually in foundation plan)
6. Staircases (if shown)
7. Lintels (over openings)

Output a JSON object with key "elements". Each element:
{
  "element_type": "column|beam|slab|footing|staircase|lintel|wall",
  "label": "B1 (230x600)",
  "width": number_in_mm (e.g. 230),
  "depth": number_in_mm (e.g. 600),
  "length": number_in_mm (beam span from dimensions, use 4000-6000 if not clearly visible),
  "bottom_bar_dia": number_in_mm (e.g. 25 for 2K25),
  "bottom_bar_count": number (e.g. 2 for 2K25),
  "top_bar_dia": number_in_mm (e.g. 16 for 2K16),
  "top_bar_count": number (e.g. 2 for 2K16),
  "stirrup_dia": number_in_mm (e.g. 10 for K10@150C/C),
  "stirrup_spacing": number_in_mm (e.g. 150 for K10@150C/C),
  "quantity": number
}

For columns, use main_bar_dia and main_bar_count instead of top/bottom.

CRITICAL RULES:
- Read beam dimensions EXACTLY from the label, e.g. B1(230X600) means width=230, depth=600
- "2K25" means count=2, diameter=25. Do NOT confuse these.
- Report top and bottom bars SEPARATELY - they often have different diameters
- Stirrup diameter comes from the notation like K10 (dia=10) or K8 (dia=8)
- Beams with letter suffixes like B3A, B5B, B12C are SEPARATE beams - include them all with their exact label
- A drawing may have B1 through B20 or more - list EVERY SINGLE ONE you can see
- If a beam is partially visible at the edge of the drawing, still include it
- Be THOROUGH - list every element you can identify. Missing beams is the worst error you can make."""


# ===== New extraction API =====

def extract_text_blocks(filepath: str) -> list[dict]:
    """
    Open a vector PDF, extract all text blocks with precise bounding box
    coordinates (X0, Y0, X1, Y1) and page number.

    Returns a list of dicts:
        [{"text": "2K16", "x0": 100.5, "y0": 200.3, "x1": 150.2, "y1": 220.1, "page": 0}, ...]
    """
    doc = pymupdf.open(filepath)
    blocks = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:  # type 0 = text block
                continue

            for line in block.get("lines", []):
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")

                line_text = line_text.strip()
                if not line_text:
                    continue

                bbox = line.get("bbox", block.get("bbox", (0, 0, 0, 0)))
                blocks.append({
                    "text": line_text,
                    "x0": round(bbox[0], 2),
                    "y0": round(bbox[1], 2),
                    "x1": round(bbox[2], 2),
                    "y1": round(bbox[3], 2),
                    "page": page_num,
                })

    doc.close()
    return blocks


def extract_and_parse_pdf(filepath: str) -> ExtractionResult:
    """
    High-level extraction: get raw text blocks + parsed BeamDetail list.
    Uses bounding box proximity to assign bar specs to their nearest beam.
    Used by the /upload/pdf endpoint.
    """
    import math

    text_blocks = extract_text_blocks(filepath)

    # First pass: identify beams and collect positioned bar/stirrup entities
    beam_positions: dict[str, tuple[float, float]] = {}  # beam_id -> (cx, cy)
    beams: dict[str, BeamDetail] = {}
    bar_entities: list[tuple[str, float, float]] = []  # (notation, cx, cy)
    stirrup_entities: list[tuple[str, float, float]] = []  # (notation, cx, cy)

    for block in text_blocks:
        text = block["text"]
        cx = (block["x0"] + block["x1"]) / 2
        cy = (block["y0"] + block["y1"]) / 2

        beam = StructuralNotationParser.parse_beam_label(text)
        if beam:
            bid = beam["beam_id"]
            if bid not in beams:
                beams[bid] = BeamDetail(
                    beam_id=bid,
                    dimensions=BeamDimensions(width=beam["width"], depth=beam["depth"]),
                    reinforcement=BeamReinforcement(),
                )
                beam_positions[bid] = (cx, cy)
            continue

        specs = StructuralNotationParser.find_all_bar_specs(text)
        for spec in specs:
            bar_entities.append((spec, cx, cy))

        stirrup = StructuralNotationParser.parse_stirrup(text)
        if stirrup:
            stirrup_entities.append((stirrup["notation"], cx, cy))

    # Second pass: assign each bar/stirrup to nearest beam
    def find_nearest_beam(x: float, y: float) -> Optional[str]:
        if not beam_positions:
            return None
        min_dist = float("inf")
        nearest = None
        for bid, (bx, by) in beam_positions.items():
            dist = math.hypot(x - bx, y - by)
            if dist < min_dist:
                min_dist = dist
                nearest = bid
        return nearest

    beam_bars: dict[str, list[str]] = {bid: [] for bid in beams}
    beam_stirrups: dict[str, str] = {bid: "" for bid in beams}

    for notation, x, y in bar_entities:
        nearest = find_nearest_beam(x, y)
        if nearest:
            beam_bars[nearest].append(notation)

    for notation, x, y in stirrup_entities:
        nearest = find_nearest_beam(x, y)
        if nearest:
            beam_stirrups[nearest] = notation

    # Assign: larger dia bars -> bottom, smaller -> top
    for bid, beam_detail in beams.items():
        bars = beam_bars.get(bid, [])
        stirrup = beam_stirrups.get(bid, "")

        if bars:
            parsed = [StructuralNotationParser.parse_bar_spec(b) for b in bars]
            parsed = [p for p in parsed if p]
            parsed.sort(key=lambda p: p["diameter"], reverse=True)

            mid = max(1, len(parsed) // 2)
            bottom = [p["notation"] for p in parsed[:mid]]
            top = [p["notation"] for p in parsed[mid:]]

            beam_detail.reinforcement = BeamReinforcement(
                top_bars=top or bottom,
                bottom_bars=bottom,
                stirrups=stirrup,
            )
        elif stirrup:
            beam_detail.reinforcement = BeamReinforcement(stirrups=stirrup)

    raw_annotations = [b["text"] for b in text_blocks]

    return ExtractionResult(
        filename=os.path.basename(filepath),
        text_entities=[
            TextEntity(
                text=b["text"],
                x=b["x0"], y=b["y0"],
                x0=b["x0"], y0=b["y0"], x1=b["x1"], y1=b["y1"],
                page=b["page"],
            )
            for b in text_blocks
        ],
        beams=list(beams.values()),
        raw_annotations=raw_annotations[:200],
        metadata={
            "total_text_blocks": len(text_blocks),
            "beams_found": len(beams),
            "bar_specs_found": len(bar_entities),
            "page_count": len(set(b["page"] for b in text_blocks)) if text_blocks else 0,
        },
    )


# ===== Hybrid OCR + Spatial Pipeline =====

class _EntityType:
    BEAM_LABEL = "beam_label"
    BAR_SPEC = "bar_spec"
    STIRRUP_SPEC = "stirrup_spec"
    SPACING = "spacing"
    DIMENSION = "dimension"
    OTHER = "other"


def classify_text_entities(text_blocks: list[dict]) -> list[dict]:
    """
    Parse all text blocks into typed structural entities with coordinates.
    Each entity gets: type, parsed data, and bounding box coordinates.
    """
    entities = []

    for block in text_blocks:
        text = block["text"].strip()
        if not text:
            continue

        entity = {
            "text": text,
            "x0": block["x0"],
            "y0": block["y0"],
            "x1": block["x1"],
            "y1": block["y1"],
            "cx": (block["x0"] + block["x1"]) / 2,
            "cy": (block["y0"] + block["y1"]) / 2,
            "page": block["page"],
        }

        beam = StructuralNotationParser.parse_beam_label(text)
        if beam:
            entity["type"] = _EntityType.BEAM_LABEL
            entity["data"] = beam
            entities.append(entity)
            continue

        stirrup = StructuralNotationParser.parse_stirrup(text)
        if stirrup:
            entity["type"] = _EntityType.STIRRUP_SPEC
            entity["data"] = stirrup
            entities.append(entity)
            continue

        bar = StructuralNotationParser.parse_bar_spec(text)
        if bar:
            entity["type"] = _EntityType.BAR_SPEC
            entity["data"] = bar
            entities.append(entity)
            continue

        spacing = StructuralNotationParser.parse_spacing(text)
        if spacing is not None:
            entity["type"] = _EntityType.SPACING
            entity["data"] = {"spacing": spacing}
            entities.append(entity)
            continue

        dim = StructuralNotationParser.parse_dimensions(text)
        if dim:
            entity["type"] = _EntityType.DIMENSION
            entity["data"] = dim
            entities.append(entity)
            continue

        if re.match(r"^\d{3,5}$", text):
            entity["type"] = _EntityType.DIMENSION
            entity["data"] = {"value_mm": int(text)}
            entities.append(entity)
            continue

        entity["type"] = _EntityType.OTHER
        entity["data"] = None
        entities.append(entity)

    return entities


def assign_entities_to_beams(entities: list[dict]) -> dict[str, dict]:
    """
    Group entities by nearest beam using spatial proximity on the same page.
    Returns: {beam_id: {"beam": beam_data, "bars": [...], "stirrups": [...],
              "spacings": [...], "dimensions": [...], "all_entities": [...]}}
    """
    import math

    beam_entities = [e for e in entities if e["type"] == _EntityType.BEAM_LABEL]
    non_beam_entities = [e for e in entities if e["type"] != _EntityType.BEAM_LABEL and e["type"] != _EntityType.OTHER]

    if not beam_entities:
        return {}

    beam_groups: dict[str, dict] = {}
    for be in beam_entities:
        bid = be["data"]["beam_id"]
        beam_groups[bid] = {
            "beam": be,
            "bars": [],
            "stirrups": [],
            "spacings": [],
            "dimensions": [],
            "all_entities": [],
        }

    for entity in non_beam_entities:
        best_beam = None
        best_dist = float("inf")

        for be in beam_entities:
            if be["page"] != entity["page"]:
                continue
            dist = math.hypot(entity["cx"] - be["cx"], entity["cy"] - be["cy"])
            if dist < best_dist:
                best_dist = dist
                best_beam = be["data"]["beam_id"]

        if best_beam is None:
            continue

        group = beam_groups[best_beam]
        group["all_entities"].append(entity)

        if entity["type"] == _EntityType.BAR_SPEC:
            group["bars"].append(entity)
        elif entity["type"] == _EntityType.STIRRUP_SPEC:
            group["stirrups"].append(entity)
        elif entity["type"] == _EntityType.SPACING:
            group["spacings"].append(entity)
        elif entity["type"] == _EntityType.DIMENSION:
            group["dimensions"].append(entity)

    return beam_groups


def _determine_bar_position(bar_entity: dict, beam_entity: dict, all_bars: list[dict]) -> str:
    """
    Determine if a bar is 'top' or 'bottom' based on Y-coordinate
    relative to the beam label position.

    In PDF coordinates, Y increases downward. Beam labels are typically
    placed below the beam drawing. Bars above (smaller Y) the beam label = top,
    bars below or at same level (larger Y) = bottom.

    Refined: use the midpoint of all bar Y-positions as the beam center.
    """
    if not all_bars:
        return "bottom"

    all_ys = [b["cy"] for b in all_bars]
    y_center = (min(all_ys) + max(all_ys)) / 2

    if bar_entity["cy"] < y_center:
        return "top"
    return "bottom"


def _determine_bar_zone(bar_entity: dict, beam_entity: dict, dimensions: list[dict], all_bars: list[dict]) -> str:
    """
    Determine if a bar is 'full' (straight, full span) or 'partial' (extra).

    Heuristics (in order of priority):
    1. If there's a dimension annotation nearby that looks like a bar cut length
       (value between 800-3000mm, not matching beam width/depth), mark as partial.
    2. Default to 'full' (straight bar).
    """
    import math

    nearby_dim_threshold = 100
    beam_width = beam_entity["data"]["width"]
    beam_depth = beam_entity["data"]["depth"]

    for dim in dimensions:
        data = dim.get("data", {})
        if "value_mm" in data:
            val = data["value_mm"]
            # Skip dimensions that match beam cross-section dimensions
            if val == beam_width or val == beam_depth:
                continue
            # Bar cut lengths are typically 800-4000mm
            if val < 800 or val > 5000:
                continue
            dist = math.hypot(bar_entity["cx"] - dim["cx"], bar_entity["cy"] - dim["cy"])
            if dist < nearby_dim_threshold:
                return "partial"

    return "full"


def _determine_bar_subzone(bar_entity: dict, all_bars: list[dict]) -> str:
    """
    For partial (extra) bars, determine if they're 'mid-span' or 'support' extra.
    Bars in the center half of the beam X-extent = mid-span extra.
    Bars in the outer quarters = support extra.
    """
    if not all_bars:
        return "mid-span"

    all_xs = [b["cx"] for b in all_bars]
    x_min, x_max = min(all_xs), max(all_xs)
    x_range = x_max - x_min

    if x_range == 0:
        return "mid-span"

    relative_x = (bar_entity["cx"] - x_min) / x_range
    if 0.3 < relative_x < 0.7:
        return "mid-span"
    return "both-supports"


def _determine_stirrup_zone(stirrup_entity: dict, beam_entity: dict, all_stirrups: list[dict]) -> str:
    """
    Determine stirrup zone: 'end', 'support', or 'mid' based on X-position.

    Strategy: sort all stirrup/spacing annotations by X-position.
    - Leftmost and rightmost = end zones (lighter stirrups at beam extremes)
    - Next inward = support zones (heavier, closer spacing near columns)
    - Center = mid zone (wider spacing)

    If only 2 annotations, the one with smaller spacing = support, larger = mid.
    If only 1, treat as uniform (mid).
    """
    if len(all_stirrups) <= 1:
        return "mid"

    sorted_stirrups = sorted(all_stirrups, key=lambda s: s["cx"])

    idx = sorted_stirrups.index(stirrup_entity) if stirrup_entity in sorted_stirrups else -1
    if idx == -1:
        dists = [abs(stirrup_entity["cx"] - s["cx"]) for s in sorted_stirrups]
        idx = dists.index(min(dists))

    n = len(sorted_stirrups)

    if n == 2:
        if idx == 0:
            return "support"
        return "mid"

    if n == 3:
        if idx == 0:
            return "end"
        elif idx == 1:
            return "support"
        else:
            return "mid"

    if n >= 4:
        if idx == 0 or idx == n - 1:
            return "end"
        elif idx == 1 or idx == n - 2:
            return "support"
        else:
            return "mid"

    return "mid"


def build_elements_from_spatial(beam_groups: dict[str, dict]) -> list[StructuralElement]:
    """
    Assemble StructuralElement objects with BeamReinforcementDetail
    from spatially-classified entities.
    """
    from backend.app.models.schemas import BeamReinforcementDetail, RebarLayer

    elements = []

    for bid, group in beam_groups.items():
        beam_data = group["beam"]["data"]
        beam_entity = group["beam"]
        bars = group["bars"]
        stirrups = group["stirrups"]
        spacings = group["spacings"]
        dimensions = group["dimensions"]

        width = float(beam_data["width"])
        depth = float(beam_data["depth"])

        # Classify bars into top/bottom
        top_bars = []
        bottom_bars = []
        for bar in bars:
            pos = _determine_bar_position(bar, beam_entity, bars)
            if pos == "top":
                top_bars.append(bar)
            else:
                bottom_bars.append(bar)

        # Within top/bottom, classify straight vs extra
        # Heuristic: if multiple bars at same position, larger dia = straight, smaller = extra
        bottom_straight_layers = []
        bottom_extra_layers = []
        top_straight_layers = []
        top_extra_layers = []

        for bar in bottom_bars:
            zone = _determine_bar_zone(bar, beam_entity, dimensions, bars)
            layer = RebarLayer(
                diameter=float(bar["data"]["diameter"]),
                count=int(bar["data"]["count"]),
                position="bottom",
                zone="full",
            )
            if zone == "partial":
                subzone = _determine_bar_subzone(bar, bars)
                layer.zone = subzone
                bottom_extra_layers.append(layer)
            else:
                bottom_straight_layers.append(layer)

        # If all bottom bars are "straight" but have different diameters, 
        # the smaller diameter is likely the extra bar
        if len(bottom_straight_layers) > 1:
            bottom_straight_layers.sort(key=lambda l: l.diameter, reverse=True)
            kept = [bottom_straight_layers[0]]
            for layer in bottom_straight_layers[1:]:
                if layer.diameter < kept[0].diameter:
                    layer.zone = "mid-span"
                    bottom_extra_layers.append(layer)
                else:
                    kept.append(layer)
            bottom_straight_layers = kept

        for bar in top_bars:
            zone = _determine_bar_zone(bar, beam_entity, dimensions, bars)
            layer = RebarLayer(
                diameter=float(bar["data"]["diameter"]),
                count=int(bar["data"]["count"]),
                position="top",
                zone="full",
            )
            if zone == "partial":
                subzone = _determine_bar_subzone(bar, bars)
                layer.zone = subzone
                top_extra_layers.append(layer)
            else:
                top_straight_layers.append(layer)

        # If all top bars are "straight" but have different diameters,
        # the larger diameter is likely the extra at support, smaller is straight
        if len(top_straight_layers) > 1:
            top_straight_layers.sort(key=lambda l: l.diameter)
            kept = [top_straight_layers[0]]
            for layer in top_straight_layers[1:]:
                if layer.diameter > kept[0].diameter:
                    layer.zone = "both-supports"
                    top_extra_layers.append(layer)
                else:
                    kept.append(layer)
            top_straight_layers = kept

        # Classify stirrups into zones
        # Combine stirrups and spacings into a unified list sorted by X-position
        stirrup_end_zone = None
        stirrup_support_zone = None
        stirrup_mid_zone = None

        all_stirrup_like = stirrups + spacings
        all_stirrup_like_sorted = sorted(all_stirrup_like, key=lambda s: s["cx"])

        for i, item in enumerate(all_stirrup_like_sorted):
            zone = _determine_stirrup_zone(item, beam_entity, all_stirrup_like_sorted)

            if item["type"] == _EntityType.STIRRUP_SPEC:
                sz_data = {
                    "dia": item["data"]["diameter"],
                    "spacing": item["data"]["spacing"],
                    "legs": item["data"].get("legs", 2),
                }
            elif item["type"] == _EntityType.SPACING:
                # For standalone spacing, inherit diameter from nearest full stirrup
                base_stirrup = None
                if stirrups:
                    import math
                    min_d = float("inf")
                    for st in stirrups:
                        d = math.hypot(item["cx"] - st["cx"], item["cy"] - st["cy"])
                        if d < min_d:
                            min_d = d
                            base_stirrup = st
                sz_data = {
                    "dia": base_stirrup["data"]["diameter"] if base_stirrup else 8,
                    "spacing": item["data"]["spacing"],
                    "legs": base_stirrup["data"].get("legs", 2) if base_stirrup else 2,
                }
            else:
                continue

            if zone == "end" and stirrup_end_zone is None:
                stirrup_end_zone = sz_data
            elif zone == "support" and stirrup_support_zone is None:
                stirrup_support_zone = sz_data
            elif zone == "mid" and stirrup_mid_zone is None:
                stirrup_mid_zone = sz_data
            elif stirrup_mid_zone is None:
                stirrup_mid_zone = sz_data

        # Estimate span from dimension annotations if available
        span = None
        for dim in dimensions:
            data = dim.get("data", {})
            if "value_mm" in data and data["value_mm"] > 1500:
                if span is None or data["value_mm"] > span:
                    span = data["value_mm"]
            elif data.get("length"):
                span = data["length"]

        # Calculate zone lengths based on span
        if span and stirrup_end_zone:
            stirrup_end_zone["zone_length_mm"] = span * 0.1
        if span and stirrup_support_zone:
            stirrup_support_zone["zone_length_mm"] = span * 0.25
        if span and stirrup_mid_zone:
            stirrup_mid_zone["zone_length_mm"] = span * 0.5

        # Build the detail object
        reinf_detail = BeamReinforcementDetail(
            bottom_straight=bottom_straight_layers or None,
            bottom_extra_midspan=bottom_extra_layers or None,
            top_straight=top_straight_layers or None,
            top_extra_support=top_extra_layers or None,
            side_face=None,
            stirrup_end_zone=stirrup_end_zone,
            stirrup_support_zone=stirrup_support_zone,
            stirrup_mid_zone=stirrup_mid_zone,
        )

        # Top-level fields for backward compatibility
        primary_bottom = bottom_straight_layers[0] if bottom_straight_layers else None
        primary_top = top_straight_layers[0] if top_straight_layers else (top_extra_layers[0] if top_extra_layers else None)
        primary_stirrup = stirrup_support_zone or stirrup_mid_zone or stirrup_end_zone

        element = StructuralElement(
            element_type=ElementType.BEAM,
            label=bid,
            width=width,
            depth=depth,
            length=float(span) if span else 6000.0,
            bottom_bar_dia=primary_bottom.diameter if primary_bottom else None,
            bottom_bar_count=primary_bottom.count if primary_bottom else None,
            top_bar_dia=primary_top.diameter if primary_top else None,
            top_bar_count=primary_top.count if primary_top else None,
            stirrup_dia=float(primary_stirrup["dia"]) if primary_stirrup else None,
            stirrup_spacing=float(primary_stirrup["spacing"]) if primary_stirrup else None,
            quantity=1,
            reinforcement_detail=reinf_detail,
        )
        elements.append(element)

    return elements


async def analyze_pdf_hybrid(
    filepath: str,
    filename: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None,
) -> list[StructuralElement]:
    """
    Hybrid extraction: OCR for text reading, spatial logic for assignment,
    LLM only for span length estimation when not available from text.

    Returns empty list if no beam labels are found (caller should fall back
    to full LLM vision for raster/scanned PDFs).
    """
    text_blocks = extract_text_blocks(filepath)

    entities = classify_text_entities(text_blocks)

    beam_groups = assign_entities_to_beams(entities)

    if not beam_groups:
        return []

    elements = build_elements_from_spatial(beam_groups)

    # Optional: use LLM to estimate span lengths for beams missing length data
    beams_missing_span = [e for e in elements if e.length == 6000.0]
    if beams_missing_span and api_key:
        import httpx
        from openai import AsyncOpenAI

        api_base_url = api_base or os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
        model_name = model or os.getenv("LLM_MODEL", "gpt-4o")
        ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

        try:
            http_client = httpx.AsyncClient(verify=ssl_verify)
            client = AsyncOpenAI(
                api_key=api_key,
                base_url=api_base_url,
                http_client=http_client,
            )

            pages = render_pdf_pages(filepath, dpi=150)
            if pages:
                img_bytes = pages[0][0]
                img_base64 = image_to_base64(img_bytes)
                beam_labels = [e.label for e in beams_missing_span]

                resp = await client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": "You read structural drawings and provide beam span lengths in mm."},
                        {"role": "user", "content": [
                            {"type": "text", "text": (
                                f"Read the span/length for these beams from the dimension lines in this drawing: {', '.join(beam_labels)}.\n"
                                f"Return JSON: {{\"spans\": {{\"B1\": 5000, \"B2\": 6000, ...}}}}\n"
                                f"Only include beams where you can clearly read the span. Values in mm."
                            )},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}", "detail": "low"}},
                        ]},
                    ],
                    temperature=0.1,
                    max_tokens=1024,
                )
                content = resp.choices[0].message.content
                spans_data = _parse_json_response(content)
                spans = spans_data.get("spans", {})

                for elem in elements:
                    if elem.label in spans:
                        elem.length = float(spans[elem.label])
        except Exception as e:
            print(f"LLM span estimation failed (non-critical): {e}")

    print(f"Hybrid OCR pipeline: found {len(elements)} beams with reinforcement")
    return elements


def render_pdf_pages(filepath: str, dpi: int = 200) -> list[tuple[bytes, str]]:
    """
    Render each page of a PDF as a high-resolution image.
    Returns list of (image_bytes_png, extracted_text) tuples.
    """
    doc = pymupdf.open(filepath)
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")

        # Render at high DPI for detail visibility
        mat = pymupdf.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        pages.append((img_bytes, text))

    doc.close()
    return pages


def enhance_drawing_image(img_bytes: bytes) -> bytes:
    """
    Enhance the drawing image to make line weights more distinguishable.
    Increases contrast so structural elements (darker lines) stand out.
    """
    img = Image.open(io.BytesIO(img_bytes))

    enhancer = ImageEnhance.Contrast(img)
    enhanced = enhancer.enhance(1.3)

    enhanced = enhanced.filter(ImageFilter.SHARPEN)

    output = io.BytesIO()
    enhanced.save(output, format="PNG", optimize=True)
    return output.getvalue()


def extract_text_annotations(text: str) -> dict:
    """Extract structural information from PDF text content."""
    annotations = {
        "dimensions": [],
        "bar_specs": [],
        "spacings": [],
        "labels": [],
        "all_text": [],
    }

    lines = text.strip().split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue

        annotations["all_text"].append(line)

        # Dimension patterns (e.g., 300x450, 230X600)
        dim_matches = re.findall(r"(\d{2,4})\s*[xX×]\s*(\d{2,4})", line)
        for match in dim_matches:
            annotations["dimensions"].append(f"{match[0]}x{match[1]}")

        # Bar specifications (e.g., 4-16mm, 6#20dia, 4nos 16mm)
        bar_matches = re.findall(
            r"(\d{1,2})\s*(?:[-#]|nos\.?\s*)\s*(\d{2,3})\s*(?:mm|dia|φ|Φ|ø)", line
        )
        for match in bar_matches:
            annotations["bar_specs"].append(f"{match[0]}-{match[1]}mm")

        # Spacing patterns
        spacing_matches = re.findall(r"(?:@|c/c|c\.c\.?)\s*(\d{2,3})", line)
        for match in spacing_matches:
            annotations["spacings"].append(f"@{match}")

        # Element labels (B1, C1, F1, S1, etc.)
        label_matches = re.findall(r"\b([BCFSL]\d{1,2})\b", line)
        annotations["labels"].extend(label_matches)

    return annotations


def image_to_base64(img_bytes: bytes) -> str:
    """Convert image bytes to base64 string for LLM API."""
    return base64.b64encode(img_bytes).decode()


async def analyze_pdf_with_vision(
    pages: list[tuple[bytes, str]],
    filename: str,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None,
) -> list[StructuralElement]:
    """
    Send PDF page images to a vision-capable LLM for structural analysis.
    Uses a two-pass approach:
      Pass 1: Identify ALL beam/element labels and dimensions
      Pass 2: Get full reinforcement details for each element
    """
    import httpx
    from openai import AsyncOpenAI

    api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    api_base = api_base or os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    if not api_key:
        raise ValueError("No LLM API key configured. Set LLM_API_KEY in your .env file.")

    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

    http_client = httpx.AsyncClient(verify=ssl_verify)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=api_base,
        http_client=http_client,
    )

    all_elements = []

    for page_idx, (img_bytes, page_text) in enumerate(pages):
        enhanced_img = enhance_drawing_image(img_bytes)
        img_base64 = image_to_base64(enhanced_img)
        text_info = extract_text_annotations(page_text)

        # --- PASS 1: Find all beam labels and basic info ---
        pass1_content = [
            {
                "type": "text",
                "text": (
                    f"Look at this structural drawing (file: {filename}, section {page_idx + 1} of {len(pages)}).\n\n"
                    f"OCR text (may be incomplete):\n"
                    f"{chr(10).join(text_info['all_text'][:60]) or 'No OCR text'}\n\n"
                    f"YOUR TASK: List EVERY beam label with its dimensions from the drawing.\n"
                    f"Beam labels are written as: B1(230X600), B11(450X600), B3A(230X450) etc.\n"
                    f"The format is: BeamName(WidthXDepth)\n\n"
                    f"RULES:\n"
                    f"- B11(450X600) means label='B11', width=450, depth=600\n"
                    f"- B3A is a separate beam (letter suffix = added later)\n"
                    f"- Scan the ENTIRE drawing. There may be 20+ beams.\n"
                    f"- Include EVERY beam: B1, B2, B3, B3A, B4... B11, B12... B20 etc.\n\n"
                    f"Return JSON:\n"
                    f'{{"beams": [{{"label": "B1", "width": 230, "depth": 600}}, '
                    f'{{"label": "B11", "width": 450, "depth": 600}}, ...]}}'
                ),
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}",
                    "detail": "high",
                },
            },
        ]

        pass1_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are an expert at reading structural engineering drawings. Find ALL element labels."},
                {"role": "user", "content": pass1_content},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }

        try:
            # --- PASS 1 using OpenAI SDK ---
            resp1 = await client.chat.completions.create(
                model=model,
                messages=pass1_payload["messages"],
                temperature=0.1,
                max_tokens=4096,
            )
            content1 = resp1.choices[0].message.content
            labels_data = _parse_json_response(content1)

            beam_labels = labels_data.get("beams", [])

            beam_count = len(beam_labels)
            print(f"Pass 1 found: {beam_count} beams")

            if beam_count == 0:
                raise ValueError(
                    f"LLM Pass 1 failed to identify any beam labels on page {page_idx + 1}. "
                    f"The drawing may not contain readable beam annotations or the LLM could not interpret them."
                )

            # --- PASS 2: Get reinforcement details for each beam ---
            # Build a dimension lookup from Pass 1 (these are authoritative)
            beam_dims = {}
            for b in beam_labels:
                bl = b.get("label", "")
                if bl:
                    beam_dims[bl] = (float(b.get("width", 230)), float(b.get("depth", 450)))

            beam_list_str = ", ".join(
                f"{b.get('label', '?')}({b.get('width', '?')}x{b.get('depth', '?')})"
                for b in beam_labels
            )

            pass2_content = [
                {
                    "type": "text",
                    "text": (
                        f"This drawing has {beam_count} beams: {beam_list_str}\n\n"
                        f"Now read the COMPLETE REINFORCEMENT SCHEDULE for EACH beam from the drawing.\n\n"
                        f"CRITICAL NOTATION RULES:\n"
                        f"- '4K20' = 4 bars of 20mm diameter. Count is the number BEFORE 'K', dia is AFTER 'K'\n"
                        f"- '2K25' = 2 bars of 25mm. '6K16' = 6 bars of 16mm.\n"
                        f"- 'K10@130C/C' = stirrup dia=10mm, spacing=130mm\n"
                        f"- 'K8@200C/C' = stirrup dia=8mm, spacing=200mm\n"
                        f"- '4L-K10@130C/C' = 4-legged stirrup, dia=10mm, spacing=130mm\n"
                        f"- Bars shown ABOVE beam centerline = top bars (compression/hogging)\n"
                        f"- Bars shown BELOW beam centerline = bottom bars (tension/sagging)\n"
                        f"- Extra bars at supports are shown for partial length (cranked/curtailed)\n"
                        f"- Stirrup spacing often varies: closer spacing at ends (support zone), wider at mid-span\n\n"
                        f"FOR EACH BEAM, identify these SEPARATE bar groups:\n"
                        f"1. BOTTOM STRAIGHT BARS: Full-span tension bars at bottom (e.g., 4K20 straight)\n"
                        f"2. BOTTOM EXTRA AT MIDSPAN: Additional bars at mid-span only (e.g., 4K16 extra)\n"
                        f"3. TOP STRAIGHT BARS: Full-span bars at top (e.g., 4K16 holding bars)\n"
                        f"4. TOP EXTRA AT SUPPORTS: Cranked/curtailed bars at supports for hogging (e.g., 4K20 at L/4)\n"
                        f"5. STIRRUPS END ZONE: Lighter stirrups at the very ends of beam (e.g., K8@200C/C)\n"
                        f"6. STIRRUPS SUPPORT ZONE: Heavier/closer stirrups near supports (e.g., 4L-K10@130C/C)\n"
                        f"7. STIRRUPS MID ZONE: Wider spacing at center (e.g., K10@150C/C for middle portion)\n"
                        f"8. SIDE FACE BARS: If beam depth >= 750mm (e.g., 2K12 on each side face)\n\n"
                        f"CRITICAL: The 'count' field MUST be the EXACT number from the drawing notation.\n"
                        f"If drawing shows 4K20, then count=4. If 6K16, then count=6. Do NOT split or halve the count across zones.\n"
                        f"Each bar group gets the FULL count as written in the notation.\n\n"
                        f"Return JSON:\n"
                        f'{{"elements": [\n'
                        f'  {{\n'
                        f'    "label": "B11",\n'
                        f'    "length": 6000,\n'
                        f'    "bottom_bar_dia": 20, "bottom_bar_count": 4,\n'
                        f'    "top_bar_dia": 16, "top_bar_count": 4,\n'
                        f'    "stirrup_dia": 10, "stirrup_spacing": 130,\n'
                        f'    "reinforcement_detail": {{\n'
                        f'      "bottom_straight": [{{"diameter": 20, "count": 4, "position": "bottom", "zone": "full"}}],\n'
                        f'      "bottom_extra_midspan": [{{"diameter": 16, "count": 4, "position": "bottom", "zone": "mid-span"}}],\n'
                        f'      "top_straight": [{{"diameter": 16, "count": 4, "position": "top", "zone": "full"}}],\n'
                        f'      "top_extra_support": [{{"diameter": 20, "count": 4, "position": "top", "zone": "both-supports"}}],\n'
                        f'      "stirrup_end_zone": {{"dia": 8, "spacing": 200, "legs": 2, "zone_length_mm": 500}},\n'
                        f'      "stirrup_support_zone": {{"dia": 10, "spacing": 130, "legs": 4, "zone_length_mm": 1500}},\n'
                        f'      "stirrup_mid_zone": {{"dia": 10, "spacing": 150, "legs": 4, "zone_length_mm": 3000}},\n'
                        f'      "side_face": null\n'
                        f'    }}\n'
                        f'  }},\n'
                        f'  ...(one entry per beam)...\n]}}\n\n'
                        f"RULES:\n"
                        f"- You MUST output ALL {beam_count} beams. Do NOT skip any.\n"
                        f"- The label must EXACTLY match: {', '.join(b.get('label','') for b in beam_labels)}\n"
                        f"- Read the span/length from dimension lines if visible (in mm)\n"
                        f"- 'count' in reinforcement_detail = EXACT count from the notation (4K20 -> count=4, NOT 2)\n"
                        f"- Stirrup zones: end_zone is the very ends, support_zone is near columns (heaviest), mid_zone is center\n"
                        f"- If only two stirrup zones visible, set stirrup_end_zone to null and use support_zone + mid_zone\n"
                        f"- If only one stirrup zone visible, set end_zone and support_zone to null, use mid_zone only\n"
                        f"- stirrup_support_zone.zone_length_mm = approx L/4 from each support face\n"
                        f"- stirrup_mid_zone.zone_length_mm = middle portion (span - 2*support_zone_length)\n"
                        f"- If you see 'EF' or 'EXTRA' near bar annotations, those are extra/curtailed bars\n"
                        f"- Include side_face bars only if depth >= 750mm\n"
                        f"- If reinforcement is not clearly visible for a beam, return null for those fields. Do NOT guess or assume values.\n"
                        f"- If multiple bar sizes at same position (e.g. 2K25+2K20 at bottom), list them as separate entries in the array"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_base64}",
                        "detail": "high",
                    },
                },
            ]

            # --- PASS 2 using OpenAI SDK ---
            resp2 = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {"role": "user", "content": pass2_content},
                ],
                temperature=0.1,
                max_tokens=16384,
            )
            content2 = resp2.choices[0].message.content
            parsed2 = _parse_json_response(content2)

            elements_data = parsed2 if isinstance(parsed2, list) else parsed2.get("elements", [])

            # Build elements, FORCING dimensions from Pass 1
            page_elements = []
            for item in elements_data:
                try:
                    label = item.get("label", f"Beam {len(page_elements)+1}")
                    # Use dimensions from Pass 1 (authoritative from the beam label)
                    if label in beam_dims:
                        width, depth = beam_dims[label]
                    else:
                        width = float(item.get("width", 230))
                        depth = float(item.get("depth", 450))

                    # Build reinforcement detail if provided
                    reinf_detail = None
                    if item.get("reinforcement_detail"):
                        from backend.app.models.schemas import BeamReinforcementDetail, RebarLayer
                        rd = item["reinforcement_detail"]
                        reinf_detail = BeamReinforcementDetail(
                            bottom_straight=[
                                RebarLayer(**r) for r in (rd.get("bottom_straight") or [])
                            ] or None,
                            bottom_extra_midspan=[
                                RebarLayer(**r) for r in (rd.get("bottom_extra_midspan") or [])
                            ] or None,
                            top_straight=[
                                RebarLayer(**r) for r in (rd.get("top_straight") or [])
                            ] or None,
                            top_extra_support=[
                                RebarLayer(**r) for r in (rd.get("top_extra_support") or [])
                            ] or None,
                            side_face=[
                                RebarLayer(**r) for r in (rd.get("side_face") or [])
                            ] or None,
                            stirrup_end_zone=rd.get("stirrup_end_zone"),
                            stirrup_support_zone=rd.get("stirrup_support_zone"),
                            stirrup_mid_zone=rd.get("stirrup_mid_zone"),
                        )

                    element = StructuralElement(
                        element_type=ElementType.BEAM,
                        label=label,
                        width=width,
                        depth=depth,
                        length=float(item.get("length", 4000)),
                        bottom_bar_dia=float(item["bottom_bar_dia"]) if item.get("bottom_bar_dia") else None,
                        bottom_bar_count=int(item["bottom_bar_count"]) if item.get("bottom_bar_count") else None,
                        top_bar_dia=float(item["top_bar_dia"]) if item.get("top_bar_dia") else None,
                        top_bar_count=int(item["top_bar_count"]) if item.get("top_bar_count") else None,
                        main_bar_dia=float(item["main_bar_dia"]) if item.get("main_bar_dia") else None,
                        main_bar_count=int(item["main_bar_count"]) if item.get("main_bar_count") else None,
                        stirrup_dia=float(item["stirrup_dia"]) if item.get("stirrup_dia") else None,
                        stirrup_spacing=float(item["stirrup_spacing"]) if item.get("stirrup_spacing") else None,
                        quantity=int(item.get("quantity", 1)),
                        reinforcement_detail=reinf_detail,
                    )
                    page_elements.append(element)
                except (ValueError, KeyError) as e:
                    print(f"Skipping element: {e}")
                    continue

            all_elements.extend(page_elements)
            print(f"Pass 2 returned: {len(page_elements)} elements")

            # Log beams from Pass 1 that Pass 2 missed (do NOT inject defaults)
            found_labels = {e.label for e in page_elements}
            missed = [beam.get("label", "") for beam in beam_labels if beam.get("label", "") and beam.get("label", "") not in found_labels]
            if missed:
                print(f"Warning: Pass 2 missed beams identified in Pass 1: {missed}. No fallback data injected.")

        except httpx.TimeoutException:
            raise ValueError(f"LLM API timed out on page {page_idx + 1}. Try again.")
        except httpx.ConnectError as e:
            raise ValueError(f"Cannot connect to LLM API at {api_base}: {e}")
        except httpx.ProxyError as e:
            raise ValueError(f"Proxy error on page {page_idx + 1}: {e}. Try setting HTTP_PROXY/HTTPS_PROXY or disabling proxy.")
        except httpx.HTTPStatusError as e:
            raise ValueError(f"LLM API HTTP error on page {page_idx + 1}: {e.response.status_code} - {e.response.text[:300]}")
        except ValueError:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise ValueError(f"Vision analysis error on page {page_idx + 1}: {type(e).__name__}: {str(e)}")

    return _deduplicate_elements(all_elements)


def _parse_json_response(content: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    json_str = content
    if "```json" in content:
        json_str = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 3:
            json_str = parts[1]
            if json_str.startswith("\n"):
                json_str = json_str[1:]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            return json.loads(json_match.group())
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")


def _parse_elements_from_data(elements_data: list, page_idx: int) -> list[StructuralElement]:
    """Parse a list of element dicts into StructuralElement objects.
    
    Raises ValueError if any element is missing required fields (element_type, width, depth).
    """
    elements = []
    for item in elements_data:
        try:
            if "element_type" not in item:
                raise ValueError(f"Missing required field 'element_type' in element: {item.get('label', 'unknown')}")
            if "width" not in item or "depth" not in item:
                raise ValueError(
                    f"Missing required dimensions (width/depth) for element '{item.get('label', 'unknown')}'. "
                    f"Got width={item.get('width')}, depth={item.get('depth')}"
                )

            element = StructuralElement(
                element_type=ElementType(item["element_type"]),
                label=item.get("label", f"Page {page_idx+1} element"),
                width=float(item["width"]),
                depth=float(item["depth"]),
                length=float(item["length"]) if item.get("length") else None,
                main_bar_dia=float(item["main_bar_dia"]) if item.get("main_bar_dia") else None,
                main_bar_count=int(item["main_bar_count"]) if item.get("main_bar_count") else None,
                top_bar_dia=float(item["top_bar_dia"]) if item.get("top_bar_dia") else None,
                top_bar_count=int(item["top_bar_count"]) if item.get("top_bar_count") else None,
                bottom_bar_dia=float(item["bottom_bar_dia"]) if item.get("bottom_bar_dia") else None,
                bottom_bar_count=int(item["bottom_bar_count"]) if item.get("bottom_bar_count") else None,
                stirrup_dia=float(item["stirrup_dia"]) if item.get("stirrup_dia") else None,
                stirrup_spacing=float(item["stirrup_spacing"]) if item.get("stirrup_spacing") else None,
                quantity=int(item.get("quantity", 1)),
            )
            elements.append(element)
        except (ValueError, KeyError) as e:
            raise ValueError(f"Failed to parse element on page {page_idx + 1}: {e}")
    return elements


def _deduplicate_elements(elements: list[StructuralElement]) -> list[StructuralElement]:
    """Remove duplicate elements that may have been detected across multiple pages."""
    seen = {}
    unique = []

    for elem in elements:
        # Create a key based on type + dimensions + label
        key = (
            elem.element_type,
            elem.label,
            elem.width,
            elem.depth,
        )
        if key not in seen:
            seen[key] = elem
            unique.append(elem)
        else:
            # If same element found again, keep the one with more detail
            existing = seen[key]
            if elem.main_bar_dia and not existing.main_bar_dia:
                seen[key] = elem
                unique[unique.index(existing)] = elem

    return unique


def parse_pdf_file(filepath: str) -> DWGParseResult:
    """
    Parse a PDF structural drawing file.
    Extracts text and prepares images for LLM vision analysis.
    """
    pages = render_pdf_pages(filepath, dpi=200)

    all_text = []
    all_annotations = {
        "dimensions": [],
        "bar_specs": [],
        "spacings": [],
        "labels": [],
    }

    for img_bytes, page_text in pages:
        text_info = extract_text_annotations(page_text)
        all_text.extend(text_info["all_text"])
        all_annotations["dimensions"].extend(text_info["dimensions"])
        all_annotations["bar_specs"].extend(text_info["bar_specs"])
        all_annotations["spacings"].extend(text_info["spacings"])
        all_annotations["labels"].extend(text_info["labels"])

    # Try to detect elements from text alone first
    elements_detected = _detect_elements_from_text(all_text)

    metadata = {
        "total_layers": 0,
        "structural_layers": [],
        "total_entities": 0,
        "file_format": "PDF",
        "parse_method": "pdf_vision",
        "page_count": len(pages),
        "dimensions_found": list(set(all_annotations["dimensions"]))[:30],
        "bar_specs_found": list(set(all_annotations["bar_specs"]))[:30],
        "labels_found": list(set(all_annotations["labels"]))[:30],
    }

    # Deduplicate text
    unique_text = list(dict.fromkeys(all_text))

    return DWGParseResult(
        filename=os.path.basename(filepath),
        layers=list(set(all_annotations["labels"])),
        element_count=len(elements_detected),
        elements_detected=elements_detected,
        raw_text_annotations=unique_text[:200],
        metadata=metadata,
    )


def _detect_elements_from_text(text_lines: list[str]) -> list[StructuralElement]:
    """Try to detect structural elements from extracted PDF text."""
    elements = []

    dim_pattern = re.compile(r"(\d{2,4})\s*[xX×]\s*(\d{2,4})(?:\s*[xX×]\s*(\d{2,5}))?")
    bar_pattern = re.compile(r"(\d{1,2})\s*[-#]?\s*(\d{2,3})\s*(?:mm|dia|φ|Φ|ø)")
    spacing_pattern = re.compile(r"(?:@|c/c|c\.c)\s*(\d{2,3})")

    for line in text_lines:
        dims = dim_pattern.search(line)
        bars = bar_pattern.search(line)
        spacing = spacing_pattern.search(line)

        if dims:
            w = float(dims.group(1))
            d = float(dims.group(2))
            l = float(dims.group(3)) if dims.group(3) else None

            if w < 50 or d < 50:  # Too small to be structural
                continue

            line_lower = line.lower()
            if any(k in line_lower for k in ["col", "column"]):
                elem_type = ElementType.COLUMN
            elif any(k in line_lower for k in ["beam", "bm"]):
                elem_type = ElementType.BEAM
            elif any(k in line_lower for k in ["slab"]):
                elem_type = ElementType.SLAB
            elif any(k in line_lower for k in ["foot", "found"]):
                elem_type = ElementType.FOOTING
            elif any(k in line_lower for k in ["stair"]):
                elem_type = ElementType.STAIRCASE
            elif any(k in line_lower for k in ["lintel"]):
                elem_type = ElementType.LINTEL
            else:
                continue  # Skip ambiguous without context

            element = StructuralElement(
                element_type=elem_type,
                label=line[:50],
                width=w,
                depth=d,
                length=l * 1000 if l else 3000,
                main_bar_dia=float(bars.group(2)) if bars else None,
                main_bar_count=int(bars.group(1)) if bars else None,
                stirrup_spacing=float(spacing.group(1)) if spacing else None,
            )
            elements.append(element)

    return elements
