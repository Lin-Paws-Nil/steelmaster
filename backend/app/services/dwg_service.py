"""
DWG/DXF Service Module

Provides two levels of extraction:
1. Raw text entity extraction with coordinates (extract_text_entities)
2. Full structural element parsing (parse_file) - backward compatible

Uses ezdxf for DXF parsing. For DWG files, attempts conversion via ODA File Converter,
LibreDWG, or cloud conversion, then falls back to LLM-based interpretation of
extracted binary data.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import ezdxf
from ezdxf.entities import MText, Text, Line, LWPolyline, Circle, Insert

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


STRUCTURAL_LAYER_PATTERNS = [
    r"(?i)column",
    r"(?i)beam",
    r"(?i)slab",
    r"(?i)foot",
    r"(?i)found",
    r"(?i)stair",
    r"(?i)lintel",
    r"(?i)rebar",
    r"(?i)reinf",
    r"(?i)struct",
    r"(?i)rcc",
    r"(?i)steel",
    r"(?i)bar",
]

DIMENSION_PATTERN = re.compile(
    r"(\d{2,4})\s*[xX×]\s*(\d{2,4})(?:\s*[xX×]\s*(\d{2,5}))?"
)
BAR_SPEC_PATTERN = re.compile(
    r"(\d{1,2})\s*[-#]?\s*(\d{2,3})\s*(?:mm|dia|φ|Φ|ø)"
)
SPACING_PATTERN = re.compile(
    r"(?:@|c/c|c\.c)\s*(\d{2,3})"
)
# NOTE: The above patterns are used by legacy parse_dxf_file/parse_dwg_file code paths.
# New code should use StructuralNotationParser from utils/parser.py instead.


# ===== Phase 2: Typed Extraction API =====

def extract_text_from_dwg(file_path: str) -> list:
    """
    Extract all TEXT and MTEXT entities from a DWG/DXF file as DWGTextEntity objects.

    Includes spatial coordinates, layer name, and AutoCAD Color Index (ACI) for each entity.
    Color conventions in structural drawings:
        - Magenta (6): Beam tags like B1(230X600)
        - Green (3): Reinforcement annotations like 2K16, K8@150C/C
        - White/default (7): General text and dimensions

    Returns:
        list[DWGTextEntity]: Typed Pydantic objects with text, x, y, layer, color fields.
    """
    from backend.app.models.reinforcement import DWGTextEntity

    ext = Path(file_path).suffix.lower()

    if ext == ".dxf":
        return _extract_typed_text_from_dxf(file_path)
    elif ext == ".dwg":
        # Try ACadSharp first (provides richer metadata)
        from backend.app.services.acadsharp_reader import read_dwg_with_acadsharp
        data = read_dwg_with_acadsharp(file_path)
        if data and data.get("textEntities"):
            return [
                DWGTextEntity(
                    text=t.get("text", "").strip(),
                    x=float(t.get("x", 0.0)),
                    y=float(t.get("y", 0.0)),
                    layer=t.get("layer", ""),
                    color=int(t.get("color", 0)),
                )
                for t in data["textEntities"]
                if t.get("text", "").strip()
            ]

        # Fallback: convert to DXF then read
        dxf_path = convert_dwg_to_dxf(file_path)
        if dxf_path:
            entities = _extract_typed_text_from_dxf(dxf_path)
            if dxf_path != file_path.rsplit(".", 1)[0] + ".dxf":
                os.unlink(dxf_path)
            return entities

    return []


def _extract_typed_text_from_dxf(filepath: str) -> list:
    """Extract TEXT/MTEXT entities from DXF as DWGTextEntity objects with color."""
    from backend.app.models.reinforcement import DWGTextEntity

    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    entities = []

    for entity in msp:
        if entity.dxftype() not in ("TEXT", "MTEXT"):
            continue

        if hasattr(entity, "plain_text"):
            text = entity.plain_text()
        elif hasattr(entity.dxf, "text"):
            text = entity.dxf.text
        else:
            continue

        text = text.strip()
        if not text:
            continue

        insert = entity.dxf.insert if hasattr(entity.dxf, "insert") else (0, 0, 0)
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""
        color = entity.dxf.color if hasattr(entity.dxf, "color") else 0

        entities.append(DWGTextEntity(
            text=text,
            x=round(float(insert[0]), 2),
            y=round(float(insert[1]), 2),
            layer=layer,
            color=int(color) if color else 0,
        ))

    return entities


# ===== Legacy extraction API (dict-based) =====

def extract_text_entities(filepath: str) -> list[dict]:
    """
    Load a DWG/DXF file and extract all TEXT and MTEXT entities
    with their insertion point coordinates (X, Y, Z) and layer.

    Returns a list of dicts:
        [{"text": "B1(230X600)", "x": 1234.5, "y": 678.9, "z": 0.0, "layer": "STR-Text"}, ...]
    """
    ext = Path(filepath).suffix.lower()

    if ext == ".dxf":
        return _extract_text_from_dxf(filepath)
    elif ext == ".dwg":
        # Try ACadSharp first for proper DWG reading
        from backend.app.services.acadsharp_reader import read_dwg_with_acadsharp
        data = read_dwg_with_acadsharp(filepath)
        if data and data.get("textEntities"):
            return [
                {
                    "text": t.get("text", ""),
                    "x": t.get("x", 0.0),
                    "y": t.get("y", 0.0),
                    "z": t.get("z", 0.0),
                    "layer": t.get("layer", ""),
                }
                for t in data["textEntities"]
                if t.get("text", "").strip()
            ]

        # Fallback: try converting to DXF
        dxf_path = convert_dwg_to_dxf(filepath)
        if dxf_path:
            entities = _extract_text_from_dxf(dxf_path)
            if dxf_path != filepath.rsplit(".", 1)[0] + ".dxf":
                os.unlink(dxf_path)
            return entities

        # Last resort: binary extraction
        return _extract_text_from_binary(filepath)

    return []


def _extract_text_from_dxf(filepath: str) -> list[dict]:
    """Extract TEXT/MTEXT entities from a DXF file via ezdxf."""
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()
    entities = []

    for entity in msp:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            if hasattr(entity, "plain_text"):
                text = entity.plain_text()
            elif hasattr(entity.dxf, "text"):
                text = entity.dxf.text
            else:
                continue

            if not text.strip():
                continue

            insert = entity.dxf.insert if hasattr(entity.dxf, "insert") else (0, 0, 0)
            layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""

            entities.append({
                "text": text.strip(),
                "x": round(float(insert[0]), 2),
                "y": round(float(insert[1]), 2),
                "z": round(float(insert[2]) if len(insert) > 2 else 0.0, 2),
                "layer": layer,
            })

    return entities


def _extract_text_from_binary(filepath: str) -> list[dict]:
    """Fallback: extract text strings from DWG binary data."""
    with open(filepath, "rb") as f:
        raw_data = f.read()

    obj_data = _extract_dwg_object_data(raw_data)
    return [
        {"text": t, "x": 0.0, "y": 0.0, "z": 0.0, "layer": ""}
        for t in obj_data.get("texts", [])
    ]


def extract_and_parse(filepath: str) -> ExtractionResult:
    """
    High-level extraction: get raw text entities + parsed BeamDetail list.
    Uses spatial proximity to assign bar specs to their nearest beam.
    Used by the /upload/dwg endpoint.
    """
    import math

    text_entities = extract_text_entities(filepath)

    # First pass: identify beam labels and their positions
    beam_positions: dict[str, tuple[float, float]] = {}  # beam_id -> (x, y)
    beams: dict[str, BeamDetail] = {}

    # Collect bar specs and stirrups with their positions
    bar_entities: list[tuple[str, float, float]] = []  # (notation, x, y)
    stirrup_entities: list[tuple[str, float, float]] = []  # (notation, x, y)

    for entity in text_entities:
        text = entity["text"]
        x, y = entity.get("x", 0.0), entity.get("y", 0.0)

        beam = StructuralNotationParser.parse_beam_label(text)
        if beam:
            bid = beam["beam_id"]
            if bid not in beams:
                beams[bid] = BeamDetail(
                    beam_id=bid,
                    dimensions=BeamDimensions(width=beam["width"], depth=beam["depth"]),
                    reinforcement=BeamReinforcement(),
                )
                beam_positions[bid] = (x, y)
            continue

        bar = StructuralNotationParser.parse_bar_spec(text)
        if bar:
            bar_entities.append((bar["notation"], x, y))

        stirrup = StructuralNotationParser.parse_stirrup(text)
        if stirrup:
            stirrup_entities.append((stirrup["notation"], x, y))

    # Second pass: assign each bar/stirrup to its nearest beam by Euclidean distance
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

    # Group bars by nearest beam
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

    # Assign reinforcement per beam
    # Heuristic: larger diameter bars are typically bottom (tension), smaller are top
    for bid, beam_detail in beams.items():
        bars = beam_bars.get(bid, [])
        stirrup = beam_stirrups.get(bid, "")

        if bars:
            parsed = [StructuralNotationParser.parse_bar_spec(b) for b in bars]
            parsed = [p for p in parsed if p]
            parsed.sort(key=lambda p: p["diameter"], reverse=True)

            # Larger dia bars -> bottom (tension), smaller -> top (compression)
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

    raw_annotations = [e["text"] for e in text_entities]

    return ExtractionResult(
        filename=os.path.basename(filepath),
        text_entities=[TextEntity(**e) for e in text_entities],
        beams=list(beams.values()),
        raw_annotations=raw_annotations[:200],
        metadata={
            "total_text_entities": len(text_entities),
            "beams_found": len(beams),
            "bar_specs_found": len(bar_entities),
        },
    )


def convert_dwg_to_dxf(dwg_path: str) -> Optional[str]:
    """Attempt to convert DWG to DXF using available tools."""
    dxf_path = dwg_path.rsplit(".", 1)[0] + ".dxf"

    oda_paths = [
        "/usr/bin/ODAFileConverter",
        "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
        "ODAFileConverter",
    ]

    for oda_path in oda_paths:
        try:
            input_dir = os.path.dirname(os.path.abspath(dwg_path))
            output_dir = tempfile.mkdtemp()
            filename = os.path.basename(dwg_path)

            subprocess.run(
                [oda_path, input_dir, output_dir, "ACAD2018", "DXF", "0", "1", filename],
                capture_output=True,
                timeout=30,
            )

            converted = os.path.join(output_dir, filename.rsplit(".", 1)[0] + ".dxf")
            if os.path.exists(converted):
                return converted
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    try:
        subprocess.run(
            ["dwg2dxf", "-o", dxf_path, dwg_path],
            capture_output=True,
            timeout=30,
        )
        if os.path.exists(dxf_path):
            return dxf_path
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


def _classify_layer(layer_name: str) -> Optional[ElementType]:
    """Classify a layer name into a structural element type."""
    name_lower = layer_name.lower()
    if any(k in name_lower for k in ["column", "col"]):
        return ElementType.COLUMN
    if any(k in name_lower for k in ["beam", "bm"]):
        return ElementType.BEAM
    if any(k in name_lower for k in ["slab", "floor"]):
        return ElementType.SLAB
    if any(k in name_lower for k in ["foot", "found", "ftg"]):
        return ElementType.FOOTING
    if "stair" in name_lower:
        return ElementType.STAIRCASE
    if "lintel" in name_lower:
        return ElementType.LINTEL
    return None


def _is_structural_layer(layer_name: str) -> bool:
    """Check if a layer likely contains structural information."""
    return any(re.search(p, layer_name) for p in STRUCTURAL_LAYER_PATTERNS)


def _extract_dimensions_from_text(text: str) -> Optional[tuple]:
    """Extract width x depth (x length) from text annotation."""
    match = DIMENSION_PATTERN.search(text)
    if match:
        w = float(match.group(1))
        d = float(match.group(2))
        l = float(match.group(3)) if match.group(3) else None
        return (w, d, l)
    return None


def _extract_bar_spec(text: str) -> Optional[tuple]:
    """Extract bar count and diameter from text like '4-16mm' or '6#20dia'."""
    match = BAR_SPEC_PATTERN.search(text)
    if match:
        count = int(match.group(1))
        dia = float(match.group(2))
        return (count, dia)
    return None


def _extract_spacing(text: str) -> Optional[float]:
    """Extract stirrup spacing from text like '@150' or 'c/c 200'."""
    match = SPACING_PATTERN.search(text)
    if match:
        return float(match.group(1))
    return None


def _get_polyline_dimensions(entity: LWPolyline) -> Optional[tuple]:
    """Calculate width and height from a closed polyline (rectangle)."""
    points = list(entity.get_points(format="xy"))
    if len(points) < 4:
        return None

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)
    return (width, height)


def parse_dxf_file(filepath: str) -> DWGParseResult:
    """Parse a DXF file and extract structural elements."""
    doc = ezdxf.readfile(filepath)
    msp = doc.modelspace()

    layers = [layer.dxf.name for layer in doc.layers]
    structural_layers = [l for l in layers if _is_structural_layer(l)]

    text_annotations = []
    elements_detected = []
    element_geometries = {}

    for entity in msp:
        if entity.dxftype() in ("TEXT", "MTEXT"):
            if hasattr(entity, "plain_text"):
                text = entity.plain_text()
            elif hasattr(entity.dxf, "text"):
                text = entity.dxf.text
            else:
                continue
            text_annotations.append(text)

    for entity in msp:
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else ""
        elem_type = _classify_layer(layer)

        if entity.dxftype() == "LWPOLYLINE" and elem_type:
            dims = _get_polyline_dimensions(entity)
            if dims:
                if elem_type not in element_geometries:
                    element_geometries[elem_type] = []
                element_geometries[elem_type].append(dims)

        elif entity.dxftype() == "INSERT" and elem_type:
            if elem_type not in element_geometries:
                element_geometries[elem_type] = []
            element_geometries[elem_type].append(None)

    for text in text_annotations:
        dims = _extract_dimensions_from_text(text)
        bar_spec = _extract_bar_spec(text)
        spacing = _extract_spacing(text)

        if dims:
            text_lower = text.lower()
            if any(k in text_lower for k in ["col", "column"]):
                elem_type = ElementType.COLUMN
            elif any(k in text_lower for k in ["beam", "bm"]):
                elem_type = ElementType.BEAM
            elif any(k in text_lower for k in ["slab"]):
                elem_type = ElementType.SLAB
            elif any(k in text_lower for k in ["foot", "found"]):
                elem_type = ElementType.FOOTING
            else:
                elem_type = ElementType.BEAM

            element = StructuralElement(
                element_type=elem_type,
                label=text[:50],
                width=dims[0],
                depth=dims[1],
                length=dims[2] * 1000 if dims[2] else 3000,
                main_bar_dia=bar_spec[1] if bar_spec else None,
                main_bar_count=bar_spec[0] if bar_spec else None,
                stirrup_spacing=spacing,
            )
            elements_detected.append(element)

    if not elements_detected:
        for elem_type, geometries in element_geometries.items():
            for geom in geometries:
                if geom:
                    element = StructuralElement(
                        element_type=elem_type,
                        label=f"{elem_type.value.title()} (from geometry)",
                        width=geom[0],
                        depth=geom[1],
                        length=3000,
                    )
                    elements_detected.append(element)

    metadata = {
        "total_layers": len(layers),
        "structural_layers": structural_layers,
        "total_entities": len(list(msp)),
        "file_format": doc.dxfversion,
    }

    return DWGParseResult(
        filename=os.path.basename(filepath),
        layers=layers,
        element_count=len(elements_detected),
        elements_detected=elements_detected,
        raw_text_annotations=text_annotations[:100],
        metadata=metadata,
    )


def _extract_dwg_object_data(data: bytes) -> dict:
    """
    Extract structured data from DWG binary using format knowledge.
    AC1024 (AutoCAD 2010) stores objects in specific sections.
    This extracts layer names, text content, and block names.
    """
    results = {
        "layers": [],
        "texts": [],
        "blocks": [],
        "dimensions": [],
    }

    # Method 1: Look for layer/text object patterns
    # DWG stores strings with a "bit-short" length prefix in object sections
    # For readable text, we search for common structural patterns in context

    # Extract properly encoded strings by finding readable sequences
    # between non-printable boundaries (more reliable than raw scan)
    text_regions = []
    current_text = []
    min_length = 4

    for i, byte in enumerate(data):
        if 32 <= byte <= 126:  # printable ASCII
            current_text.append(chr(byte))
        else:
            if len(current_text) >= min_length:
                text = "".join(current_text)
                text_regions.append((i - len(current_text), text))
            current_text = []

    if len(current_text) >= min_length:
        text_regions.append((len(data) - len(current_text), "".join(current_text)))

    # Filter and classify extracted strings
    noise_prefixes = (
        "AC10", "Teigha", "ODA", "Open Design", "Alliance",
        "build_version", "registry", "install_id", "ProductInformation",
        ".NET", "GSTARCAD", "GSTARSOFT", "localeID",
    )

    for offset, text in text_regions:
        text = text.strip()
        if not text or len(text) < 3:
            continue
        if any(text.startswith(n) or n in text for n in noise_prefixes):
            continue
        # Skip if mostly non-alphanumeric
        alnum_ratio = sum(c.isalnum() or c in " ._-" for c in text) / len(text)
        if alnum_ratio < 0.5:
            continue

        text_lower = text.lower()

        # Layer names are typically short, alphanumeric with underscores
        if (len(text) < 40 and re.match(r"^[A-Za-z][A-Za-z0-9_ -]*$", text)
                and not text.startswith("Files")):
            results["layers"].append(text)

        # Structural annotations
        if (DIMENSION_PATTERN.search(text) or
                BAR_SPEC_PATTERN.search(text) or
                SPACING_PATTERN.search(text)):
            results["texts"].append(text)
            results["dimensions"].append(text)
        elif any(kw in text_lower for kw in [
            "beam", "column", "col", "slab", "footing", "foundation",
            "stair", "lintel", "plinth", "rcc", "grade", "floor",
            "level", "plan", "section", "detail", "schedule",
            "reinforcement", "bar", "stirrup", "ring",
        ]):
            results["texts"].append(text)

        # Block names (often element labels like B1, C1, F1, etc.)
        if re.match(r"^[A-Z]{1,3}\d{1,3}$", text):
            results["blocks"].append(text)

    # Deduplicate
    results["layers"] = list(dict.fromkeys(results["layers"]))
    results["texts"] = list(dict.fromkeys(results["texts"]))
    results["blocks"] = list(dict.fromkeys(results["blocks"]))
    results["dimensions"] = list(dict.fromkeys(results["dimensions"]))

    return results


def parse_dwg_file(filepath: str) -> Optional[DWGParseResult]:
    """Parse a DWG file by converting to DXF first, or fallback to binary extraction."""
    dxf_path = convert_dwg_to_dxf(filepath)
    if dxf_path:
        result = parse_dxf_file(dxf_path)
        if dxf_path != filepath.rsplit(".", 1)[0] + ".dxf":
            os.unlink(dxf_path)
        return result

    return _parse_dwg_binary_fallback(filepath)


def _parse_dwg_binary_fallback(filepath: str) -> DWGParseResult:
    """
    Extract all possible structural data from DWG binary.
    Uses multiple extraction strategies and sends everything to LLM for interpretation.
    """
    with open(filepath, "rb") as f:
        raw_data = f.read()

    # Get version info
    version_str = raw_data[:6].decode("ascii", errors="ignore")

    # Extract structured data
    obj_data = _extract_dwg_object_data(raw_data)

    # Also try to find dimension-like patterns with context
    # Look for common structural annotation patterns
    all_text = obj_data["texts"]
    layers = obj_data["layers"]
    structural_layers = [l for l in layers if _is_structural_layer(l)]

    # Try to detect elements from extracted text
    elements_detected = []
    for text in all_text:
        dims = _extract_dimensions_from_text(text)
        bar_spec = _extract_bar_spec(text)
        spacing = _extract_spacing(text)

        if dims and dims[0] >= 100 and dims[1] >= 100:  # Minimum realistic dimensions
            text_lower = text.lower()
            if any(k in text_lower for k in ["col", "column"]):
                elem_type = ElementType.COLUMN
            elif any(k in text_lower for k in ["beam", "bm"]):
                elem_type = ElementType.BEAM
            elif any(k in text_lower for k in ["slab"]):
                elem_type = ElementType.SLAB
            elif any(k in text_lower for k in ["foot", "found"]):
                elem_type = ElementType.FOOTING
            elif any(k in text_lower for k in ["stair"]):
                elem_type = ElementType.STAIRCASE
            elif any(k in text_lower for k in ["lintel"]):
                elem_type = ElementType.LINTEL
            else:
                elem_type = ElementType.BEAM

            element = StructuralElement(
                element_type=elem_type,
                label=text[:50],
                width=dims[0],
                depth=dims[1],
                length=dims[2] * 1000 if dims[2] else 3000,
                main_bar_dia=bar_spec[1] if bar_spec else None,
                main_bar_count=bar_spec[0] if bar_spec else None,
                stirrup_spacing=spacing,
            )
            elements_detected.append(element)

    metadata = {
        "total_layers": len(layers),
        "structural_layers": structural_layers,
        "total_entities": 0,
        "file_format": f"DWG ({version_str})",
        "strings_extracted": len(all_text),
        "blocks_found": obj_data["blocks"],
        "parse_method": "binary_fallback",
        "all_layers": layers[:50],
    }

    return DWGParseResult(
        filename=os.path.basename(filepath),
        layers=layers,
        element_count=len(elements_detected),
        elements_detected=elements_detected,
        raw_text_annotations=all_text[:200],
        metadata=metadata,
    )


def _natural_sort_key(element: "StructuralElement") -> tuple:
    """Sort key for natural ordering of element labels (B2 before B10)."""
    import re as _re
    parts = _re.split(r'(\d+)', element.label or "")
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def parse_file(filepath: str) -> Optional[DWGParseResult]:
    """Parse either a DWG or DXF file. Tries ACadSharp first, then fallbacks."""
    from backend.app.services.acadsharp_reader import parse_dwg_with_acadsharp

    ext = Path(filepath).suffix.lower()

    # Try ACadSharp first (works for both DWG and DXF)
    result = parse_dwg_with_acadsharp(filepath)
    if result and result.element_count > 0:
        result.elements_detected.sort(key=_natural_sort_key)
        return result

    # For DXF, use ezdxf as fallback
    if ext == ".dxf":
        result = parse_dxf_file(filepath)
    elif ext == ".dwg":
        # If ACadSharp returned data but no elements, still use it (better than binary)
        if result:
            pass
        else:
            result = parse_dwg_file(filepath)
    else:
        return None

    if result and result.elements_detected:
        result.elements_detected.sort(key=_natural_sort_key)
    return result
