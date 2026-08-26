"""
ACadSharp DWG Reader Integration

Uses the .NET ACadSharp library (via CLI tool) to properly read DWG/DXF files
and extract structural data including text annotations, dimensions, layers,
and geometric entities.
"""

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Optional

from backend.app.models.schemas import (
    DWGParseResult,
    StructuralElement,
    ElementType,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

def _resolve_dotnet_path() -> str:
    """Find dotnet installation path (local dev or Docker)."""
    candidates = [
        os.path.expanduser("~/.dotnet"),
        "/usr/share/dotnet",
        "/usr/bin",
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "dotnet")):
            return path
    return os.path.expanduser("~/.dotnet")

DOTNET_PATH = _resolve_dotnet_path()
DWG_READER_PROJECT = BASE_DIR / "tools" / "dwg-reader"

BEAM_PATTERN = re.compile(r"B\d+[a-z]?\((\d+)[xX](\d+)\)")
COLUMN_PATTERN = re.compile(r"C(\d+)")
BAR_SPEC_PATTERN = re.compile(r"(\d+)[KkNn#](\d+)")
STIRRUP_LEGGED_PATTERN = re.compile(r"(\d+)[Ll]-?[Kk](\d+)@(\d+)[Cc]/[Cc]")
STIRRUP_SIMPLE_PATTERN = re.compile(r"[Kk](\d+)@(\d+)[Cc]/[Cc]")
SPACING_PATTERN = re.compile(r"@(\d+)[Cc]/[Cc]")


def _get_dotnet_env():
    """Get environment with .NET path included."""
    env = os.environ.copy()
    env["PATH"] = f"{DOTNET_PATH}:{env.get('PATH', '')}"
    env["DOTNET_CLI_HOME"] = str(BASE_DIR / "tools" / ".dotnet-cli")
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    return env


def _ensure_built() -> str:
    """Ensure the dwg-reader is built and return the DLL path."""
    dll_path = DWG_READER_PROJECT / "bin" / "Release" / "net8.0" / "dwg-reader.dll"
    if not dll_path.exists():
        subprocess.run(
            [f"{DOTNET_PATH}/dotnet", "build", "-c", "Release"],
            capture_output=True,
            timeout=60,
            cwd=str(DWG_READER_PROJECT),
            env=_get_dotnet_env(),
        )
    return str(dll_path)


def read_dwg_with_acadsharp(filepath: str) -> Optional[dict]:
    """Run the ACadSharp DWG reader and return parsed JSON data."""
    try:
        dll_path = _ensure_built()
        # Run the pre-built DLL directly (faster than 'dotnet run' which re-checks build)
        result = subprocess.run(
            [f"{DOTNET_PATH}/dotnet", dll_path, filepath],
            capture_output=True,
            text=True,
            timeout=60,
            env=_get_dotnet_env(),
        )

        if result.returncode != 0:
            print(f"ACadSharp reader error: {result.stderr}")
            return None

        return json.loads(result.stdout)

    except subprocess.TimeoutExpired:
        print("ACadSharp reader timed out (60s)")
        return None
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"ACadSharp reader failed: {e}")
        return None


def parse_dwg_with_acadsharp(filepath: str) -> Optional[DWGParseResult]:
    """Parse a DWG file using ACadSharp and extract structural elements."""
    data = read_dwg_with_acadsharp(filepath)
    if data is None:
        return None

    # Extract layer names
    layers = [l["name"] for l in data.get("layers", [])]

    # Collect all text annotations
    raw_texts = [t["text"] for t in data.get("textEntities", [])]
    unique_texts = list(dict.fromkeys(raw_texts))

    # Parse structural elements
    elements = _extract_elements_from_data(data)

    # Build structural layer list
    structural_layers = [
        l["name"] for l in data.get("layers", [])
        if any(k in l["name"].lower() for k in [
            "beam", "column", "slab", "str", "struct", "reinf", "found", "foot"
        ])
    ]

    metadata = {
        "total_layers": len(layers),
        "structural_layers": structural_layers,
        "total_entities": data.get("totalEntities", 0),
        "file_format": data.get("version", "Unknown"),
        "parse_method": "acadsharp",
        "entity_type_counts": data.get("entityTypeCounts", {}),
        "dimension_count": len(data.get("dimensions", [])),
    }

    return DWGParseResult(
        filename=data.get("filename", os.path.basename(filepath)),
        layers=layers,
        element_count=len(elements),
        elements_detected=elements,
        raw_text_annotations=unique_texts[:200],
        metadata=metadata,
    )


def _extract_elements_from_data(data: dict) -> list[StructuralElement]:
    """Extract structural elements from ACadSharp parsed data."""
    elements = []
    texts = data.get("textEntities", [])
    dimensions = data.get("dimensions", [])

    print(f"[ACadSharp] {len(texts)} text entities, {len(dimensions)} dimensions")
    # Log sample text annotations for debugging
    sample_texts = [t.get("text", "") for t in texts[:30]]
    print(f"[ACadSharp] Sample texts: {sample_texts}")

    # Group texts by their content patterns
    beam_labels = {}  # beam_name -> (width, depth)
    column_labels = set()
    bar_specs = []  # (count, diameter)
    stirrup_specs = []  # (legs, dia, spacing)
    spacings = []

    for t in texts:
        text = t.get("text", "").strip()
        layer = t.get("layer", "")

        if not text:
            continue

        # Beam labels with dimensions: B1(230X600)
        beam_match = BEAM_PATTERN.search(text)
        if beam_match:
            beam_name = text.split("(")[0].strip()
            width = float(beam_match.group(1))
            depth = float(beam_match.group(2))
            beam_labels[beam_name] = (width, depth)
            continue

        # Column labels: C1, C2, etc.
        if "column" in layer.lower() and COLUMN_PATTERN.match(text):
            column_labels.add(text)
            continue

        # Stirrup specs (legged): 4L-K8@150C/C, 6L-K8@125C/C
        stirrup_match = STIRRUP_LEGGED_PATTERN.search(text)
        if stirrup_match:
            legs = int(stirrup_match.group(1))
            dia = float(stirrup_match.group(2))
            spacing = float(stirrup_match.group(3))
            stirrup_specs.append((legs, dia, spacing))
            continue

        # Stirrup specs (simple): K8@150C/C, K10@130C/C
        stirrup_simple_match = STIRRUP_SIMPLE_PATTERN.search(text)
        if stirrup_simple_match:
            dia = float(stirrup_simple_match.group(1))
            spacing = float(stirrup_simple_match.group(2))
            stirrup_specs.append((2, dia, spacing))
            continue

        # Bar specifications: 2K25, 4K16, 6K12 (use .search() not .match())
        bar_match = BAR_SPEC_PATTERN.search(text)
        if bar_match:
            count = int(bar_match.group(1))
            dia = float(bar_match.group(2))
            bar_specs.append((count, dia))
            continue

        # Spacing only: @150C/C
        spacing_match = SPACING_PATTERN.search(text)
        if spacing_match:
            spacings.append(float(spacing_match.group(1)))

    # Determine common stirrup spec from what's actually in the drawing
    print(f"[ACadSharp] Parsed: {len(beam_labels)} beams, {len(bar_specs)} bar specs, "
          f"{len(stirrup_specs)} stirrups, {len(spacings)} spacings, {len(column_labels)} columns")
    if bar_specs:
        print(f"[ACadSharp] Bar specs found: {bar_specs[:10]}")
    if stirrup_specs:
        print(f"[ACadSharp] Stirrup specs found: {stirrup_specs[:10]}")
    if dim_values := [d["measurement"] for d in dimensions if d.get("measurement", 0) > 500]:
        print(f"[ACadSharp] Dimension values > 500mm: {dim_values[:10]}")

    common_stirrup_dia = None
    common_stirrup_spacing = None
    if stirrup_specs:
        dia_counter = Counter(s[1] for s in stirrup_specs)
        spacing_counter = Counter(s[2] for s in stirrup_specs)
        common_stirrup_dia = dia_counter.most_common(1)[0][0]
        common_stirrup_spacing = spacing_counter.most_common(1)[0][0]

    common_main_dia = None
    common_main_count = None
    if bar_specs:
        dia_counter = Counter(s[1] for s in bar_specs)
        count_counter = Counter(s[0] for s in bar_specs)
        common_main_dia = dia_counter.most_common(1)[0][0]
        common_main_count = count_counter.most_common(1)[0][0]

    # Calculate average beam span from dimensions (only if dimensions exist)
    dim_values = [d["measurement"] for d in dimensions if d["measurement"] > 500]
    avg_span = sum(dim_values) / len(dim_values) if dim_values else None

    # Create beam elements — use only data actually found in the drawing
    for beam_name, (width, depth) in beam_labels.items():
        elements.append(StructuralElement(
            element_type=ElementType.BEAM,
            label=beam_name,
            width=width,
            depth=depth,
            length=avg_span,
            main_bar_dia=common_main_dia,
            main_bar_count=common_main_count,
            stirrup_dia=common_stirrup_dia,
            stirrup_spacing=common_stirrup_spacing,
            quantity=1,
        ))

    # Create column elements only if column labels actually exist in the drawing
    column_polys = [
        p for p in data.get("polylines", [])
        if "column" in p.get("layer", "").lower()
    ]

    col_width = None
    col_depth = None
    if column_polys:
        widths = [p["width"] for p in column_polys if 150 < p["width"] < 1500]
        heights = [p["height"] for p in column_polys if 150 < p["height"] < 1500]
        if widths:
            col_width = round(sum(widths) / len(widths))
        if heights:
            col_depth = round(sum(heights) / len(heights))

    if column_labels and col_width and col_depth:
        for col_name in sorted(column_labels):
            elements.append(StructuralElement(
                element_type=ElementType.COLUMN,
                label=col_name,
                width=col_width,
                depth=col_depth,
                length=None,
                main_bar_dia=common_main_dia,
                main_bar_count=None,
                stirrup_dia=common_stirrup_dia,
                stirrup_spacing=common_stirrup_spacing,
                quantity=1,
            ))

    return elements
