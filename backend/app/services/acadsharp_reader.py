"""
ACadSharp DWG Reader Integration

Uses the .NET ACadSharp library (via CLI tool) to properly read DWG/DXF files
and extract structural data including text annotations, dimensions, layers,
and geometric entities.
"""

import json
import math
import os
import re
import subprocess
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

BEAM_PATTERN = re.compile(r"B(\d+)[a-zA-Z]?\((\d+\"?)\s*[xX]\s*(\d+\"?)\)")
COLUMN_PATTERN = re.compile(r"C(\d+)")
BAR_SPEC_PATTERN = re.compile(r"(\d+)[KkNn#](\d+)")
STIRRUP_LEGGED_PATTERN = re.compile(r"(\d+)[Ll]-?[Kk](\d+)@(\d+)\"?[Cc]/[Cc]")
STIRRUP_SIMPLE_PATTERN = re.compile(r"[Kk](\d+)@(\d+)\"?[Cc]/[Cc]")
SPACING_PATTERN = re.compile(r"@(\d+)\"?[Cc]/[Cc]")

INCH_TO_MM = 25.4


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

    layers = [l["name"] for l in data.get("layers", [])]
    raw_texts = [t["text"] for t in data.get("textEntities", [])]
    unique_texts = list(dict.fromkeys(raw_texts))

    elements = _extract_elements_from_data(data)

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
    """Extract structural elements using spatial assignment of bars to nearest beam."""
    elements = []
    texts = data.get("textEntities", [])
    dimensions = data.get("dimensions", [])

    print(f"[ACadSharp] {len(texts)} text entities, {len(dimensions)} dimensions")
    sample_texts = [t.get("text", "") for t in texts[:30]]
    print(f"[ACadSharp] Sample texts: {sample_texts}")

    beam_positions: dict[str, tuple[float, float, float, float]] = {}
    bar_entities: list[tuple[int, float, float, float]] = []
    stirrup_entities: list[tuple[float, float, float, float]] = []

    for t in texts:
        text = t.get("text", "").strip()
        x = float(t.get("x", 0))
        y = float(t.get("y", 0))

        if not text:
            continue

        beam_match = BEAM_PATTERN.search(text)
        if beam_match:
            beam_num = beam_match.group(1)
            w_str = beam_match.group(2)
            d_str = beam_match.group(3)

            if '"' in w_str:
                width = float(w_str.replace('"', '')) * INCH_TO_MM
            else:
                width = float(w_str)
            if '"' in d_str:
                depth = float(d_str.replace('"', '')) * INCH_TO_MM
            else:
                depth = float(d_str)

            beam_name = f"B{beam_num}"
            beam_positions[beam_name] = (x, y, width, depth)
            continue

        stirrup_match = STIRRUP_LEGGED_PATTERN.search(text)
        if stirrup_match:
            dia = float(stirrup_match.group(2))
            spacing_val = float(stirrup_match.group(3))
            if spacing_val < 20:
                spacing_val = spacing_val * INCH_TO_MM
            stirrup_entities.append((dia, spacing_val, x, y))
            continue

        stirrup_simple_match = STIRRUP_SIMPLE_PATTERN.search(text)
        if stirrup_simple_match:
            dia = float(stirrup_simple_match.group(1))
            spacing_val = float(stirrup_simple_match.group(2))
            if spacing_val < 20:
                spacing_val = spacing_val * INCH_TO_MM
            stirrup_entities.append((dia, spacing_val, x, y))
            continue

        bar_match = BAR_SPEC_PATTERN.search(text)
        if bar_match:
            count = int(bar_match.group(1))
            dia = float(bar_match.group(2))
            bar_entities.append((count, dia, x, y))
            continue

    print(f"[ACadSharp] Spatial: {len(beam_positions)} beams, "
          f"{len(bar_entities)} bars, {len(stirrup_entities)} stirrups")

    if not beam_positions:
        return []

    # Get span from dimension entities — use the LARGEST (most likely beam span)
    dim_values = []
    for d in dimensions:
        val = d.get("measurement", 0)
        if val > 500:
            dim_values.append(val)
        elif 10 < val < 500:
            dim_values.append(val * INCH_TO_MM)

    span = max(dim_values) if dim_values else None
    if span:
        print(f"[ACadSharp] Span (max dimension): {round(span)}mm")

    # Spatial assignment: assign bars and stirrups to nearest beam
    def find_nearest_beam(x: float, y: float) -> str:
        min_dist = float("inf")
        nearest = None
        for bname, (bx, by, _, _) in beam_positions.items():
            dist = math.hypot(x - bx, y - by)
            if dist < min_dist:
                min_dist = dist
                nearest = bname
        return nearest

    beam_bars: dict[str, list[tuple[int, float]]] = {b: [] for b in beam_positions}
    beam_stirrups: dict[str, list[tuple[float, float]]] = {b: [] for b in beam_positions}

    for count, dia, x, y in bar_entities:
        nearest = find_nearest_beam(x, y)
        if nearest:
            beam_bars[nearest].append((count, dia))

    for dia, spacing, x, y in stirrup_entities:
        nearest = find_nearest_beam(x, y)
        if nearest:
            beam_stirrups[nearest].append((dia, spacing))

    # Build elements with per-beam reinforcement
    for beam_name, (bx, by, width, depth) in beam_positions.items():
        bars = beam_bars.get(beam_name, [])
        stirrups = beam_stirrups.get(beam_name, [])

        bottom_dia = None
        bottom_count = None
        top_dia = None
        top_count = None

        if bars:
            bars_sorted = sorted(bars, key=lambda b: b[1], reverse=True)
            bottom_count, bottom_dia = bars_sorted[0]
            if len(bars_sorted) > 1 and bars_sorted[-1][1] < bottom_dia:
                top_count, top_dia = bars_sorted[-1]

        stirrup_dia = stirrups[0][0] if stirrups else None
        stirrup_spacing = stirrups[0][1] if stirrups else None

        print(f"[ACadSharp] {beam_name}: bars={bars}, stirrup={stirrup_dia}@{stirrup_spacing}")

        elements.append(StructuralElement(
            element_type=ElementType.BEAM,
            label=beam_name,
            width=width,
            depth=depth,
            length=span,
            bottom_bar_dia=bottom_dia,
            bottom_bar_count=bottom_count,
            top_bar_dia=top_dia,
            top_bar_count=top_count,
            stirrup_dia=stirrup_dia,
            stirrup_spacing=stirrup_spacing,
            quantity=1,
        ))

    return elements
