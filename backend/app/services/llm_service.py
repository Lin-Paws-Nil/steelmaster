"""
LLM Integration Service

Uses OpenAI-compatible API to interpret structural drawings when automated
parsing doesn't yield enough information. Can analyze text annotations,
layer names, and drawing metadata to infer structural elements.
"""

import json
import os
from typing import Optional

import httpx

from backend.app.models.schemas import (
    StructuralElement,
    ElementType,
    DWGParseResult,
)


SYSTEM_PROMPT = """You are an expert structural engineer specializing in steel reinforcement estimation for RCC structures.

Given information extracted from a structural drawing (AutoCAD DWG/DXF file), your job is to identify ALL structural elements and their specifications.

IMPORTANT RULES:
- You MUST identify EVERY beam, column, slab, footing, staircase, and lintel in the drawing
- A typical residential building has: 8-20 columns, 15-40 beams (including internal/secondary beams), 4-10 slab panels, 8-20 footings
- Internal beams (connecting internal columns), secondary beams, lintel beams, and plinth beams are often missed - include ALL of them
- If the drawing shows a grid of columns, there are beams connecting them in BOTH directions
- Count beams at each floor level separately
- For multi-storey buildings, multiply columns and beams by number of floors

For each element, determine:
1. Element type (column, beam, slab, footing, staircase, lintel, wall)
2. Dimensions (width x depth x length/span in mm)
3. Main bar specification (count and diameter)
4. Stirrup/tie specification (diameter and spacing)
5. Quantity of that element in the drawing

Use standard Indian construction practices (IS 456:2000) as defaults when info is missing:
- Columns: typical 230x230 to 600x600, 4-8 bars of 12-25mm, 8mm ties @150-200mm
- Beams: typical 230x300 to 300x600, 3-6 bars of 12-20mm, 8mm stirrups @150-200mm
- Plinth beams: typical 230x300, 4 bars of 12mm, 8mm stirrups @150mm
- Internal beams: same as main beams but may be smaller (230x300 to 230x450)
- Slabs: typical 125-150mm thick, 10-12mm bars @150mm, 8mm distribution @200mm
- Footings: typical 1000x1000 to 2000x2000, depth 300-600mm, 12-16mm bars @150mm
- Lintels: typical 230x200 to 230x300 over openings, 4 bars of 12mm

Output a JSON object with key "elements" containing an array. Each element must have:
{
  "element_type": "column|beam|slab|footing|staircase|lintel|wall",
  "label": "descriptive label (e.g. B1 - Main Beam, B2 - Internal Beam)",
  "width": number_in_mm,
  "depth": number_in_mm,
  "length": number_in_mm,
  "main_bar_dia": number_in_mm,
  "main_bar_count": number,
  "stirrup_dia": number_in_mm,
  "stirrup_spacing": number_in_mm,
  "quantity": number
}

Be THOROUGH - it is better to include more elements than to miss them. A typical G+1 residential building should have at minimum 40-60 total structural elements across all types."""


async def interpret_drawing_with_llm(
    parse_result: DWGParseResult,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None,
) -> list[StructuralElement]:
    """Use LLM to interpret drawing data and extract structural elements."""

    api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    api_base = api_base or os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    if not api_key:
        return []

    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

    # Build a comprehensive context message
    layers_info = ", ".join(parse_result.layers[:50]) if parse_result.layers else "No layers detected"
    structural_layers = ", ".join(parse_result.metadata.get("structural_layers", []))
    blocks_found = ", ".join(parse_result.metadata.get("blocks_found", []))
    all_layers = ", ".join(parse_result.metadata.get("all_layers", []))

    user_message = f"""Analyze this structural drawing data and identify ALL structural elements (especially don't miss any beams - including internal, secondary, plinth, and lintel beams):

**Filename:** {parse_result.filename}
**File format:** {parse_result.metadata.get('file_format', 'Unknown')}
**Parse method:** {parse_result.metadata.get('parse_method', 'direct')}

**Layers found:** {layers_info}
**Structural layers:** {structural_layers or 'None specifically identified'}
**All layers:** {all_layers or layers_info}
**Block names found:** {blocks_found or 'None'}

**Text annotations found in drawing ({len(parse_result.raw_text_annotations)} total):**
{chr(10).join(parse_result.raw_text_annotations[:100])}

**Elements already detected by automated parser ({len(parse_result.elements_detected)}):**
{json.dumps([e.model_dump() for e in parse_result.elements_detected[:30]], indent=2) if parse_result.elements_detected else "None - automated parsing could not detect elements from this DWG binary"}

IMPORTANT: The automated parser may have missed many elements, especially internal beams. 
Based on the drawing information above, provide a COMPLETE list of ALL structural elements.
If the automated parser found some elements, use those as a starting point but ADD any missing ones.
If no elements were detected, infer from the layer names, block names, and text annotations what the building contains.

For a typical building plan, ensure you include:
- ALL peripheral beams (along the outer walls)
- ALL internal beams (connecting internal columns, in BOTH X and Y directions)
- Plinth beams (at plinth level)
- Lintel beams (over doors and windows)
- All columns
- All slab panels
- All footings (one per column typically)"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }

    try:
        async with httpx.AsyncClient(timeout=90, verify=ssl_verify) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            # Try to parse JSON from the response
            # Handle cases where LLM wraps in markdown code block
            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0]

            parsed = json.loads(json_str)
            elements_data = parsed if isinstance(parsed, list) else parsed.get("elements", [])

            elements = []
            for item in elements_data:
                try:
                    element = StructuralElement(
                        element_type=ElementType(item["element_type"]),
                        label=item.get("label", "Unknown"),
                        width=float(item.get("width", 300)),
                        depth=float(item.get("depth", 300)),
                        length=float(item.get("length", 3000)),
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
                    print(f"Skipping element due to parse error: {e}")
                    continue

            return elements

    except Exception as e:
        print(f"LLM interpretation failed: {e}")
        return []


async def enhance_elements_with_llm(
    elements: list[StructuralElement],
    parse_result: Optional[DWGParseResult] = None,
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    model: Optional[str] = None,
) -> list[StructuralElement]:
    """Use LLM to fill in missing details and find missing elements."""

    api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    api_base = api_base or os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    model = model or os.getenv("LLM_MODEL", "gpt-4o")

    if not api_key:
        return elements

    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

    existing_json = json.dumps([e.model_dump() for e in elements], indent=2)

    # Count what we have
    type_counts = {}
    for e in elements:
        t = e.element_type.value
        type_counts[t] = type_counts.get(t, 0) + e.quantity

    context_info = ""
    if parse_result:
        context_info = f"""
Additional drawing context:
- Layers: {', '.join(parse_result.layers[:30])}
- Text annotations: {chr(10).join(parse_result.raw_text_annotations[:50])}
- Blocks: {', '.join(parse_result.metadata.get('blocks_found', []))}
"""

    user_message = f"""These structural elements were detected from a building drawing, but the detection may be INCOMPLETE (especially internal/secondary beams are often missed):

Current element counts: {json.dumps(type_counts)}

Detected elements:
{existing_json}

{context_info}

Please:
1. Fill in any missing reinforcement details (main_bar_dia, main_bar_count, stirrup_dia, stirrup_spacing) using engineering judgment
2. CRITICALLY - identify any MISSING elements that a building of this type would typically have:
   - Are there internal beams connecting internal columns? (usually missed)
   - Are there plinth beams at foundation level?
   - Are there lintel beams over openings?
   - Does the footing count match the column count?
   - Are all slab panels accounted for?
3. Return the COMPLETE list including both existing (corrected) and newly identified elements

Return a JSON object with key "elements" containing the full array."""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }

    try:
        async with httpx.AsyncClient(timeout=90, verify=ssl_verify) as client:
            response = await client.post(
                f"{api_base}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

            data = response.json()
            content = data["choices"][0]["message"]["content"]

            json_str = content
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0]

            parsed = json.loads(json_str)
            enhanced_data = parsed if isinstance(parsed, list) else parsed.get("elements", [])

            result = []
            for item in enhanced_data:
                try:
                    element = StructuralElement(
                        element_type=ElementType(item["element_type"]),
                        label=item.get("label", "Unknown"),
                        width=float(item.get("width", 300)),
                        depth=float(item.get("depth", 300)),
                        length=float(item.get("length", 3000)),
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
                    result.append(element)
                except (ValueError, KeyError):
                    continue

            # Only use LLM result if it found more elements than we started with
            if len(result) >= len(elements):
                return result
            return elements

    except Exception as e:
        print(f"LLM enhancement failed: {e}")
        return elements
