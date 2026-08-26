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
- Internal beams (connecting internal columns), secondary beams, lintel beams, and plinth beams are often missed - include ALL of them
- If the drawing shows a grid of columns, there are beams connecting them in BOTH directions
- Count beams at each floor level separately
- For multi-storey buildings, multiply columns and beams by number of floors

For each element, determine FROM THE DRAWING DATA:
1. Element type (column, beam, slab, footing, staircase, lintel, wall)
2. Dimensions (width x depth x length/span in mm)
3. Main bar specification (count and diameter)
4. Stirrup/tie specification (diameter and spacing)
5. Quantity of that element in the drawing

CRITICAL: Only report values you can actually read from the drawing data provided.
- If dimensions are not visible, set width/depth/length to null
- If reinforcement is not visible, set main_bar_dia/main_bar_count/stirrup_dia/stirrup_spacing to null
- Do NOT invent elements that don't exist in the drawing (no imaginary slabs/footings/columns)
- However, DO read span/length from dimension annotations in the text (feet-inches like 15'-2" = 4623mm, or mm values)
- If you see dimension text like "5000", "4500", "6000" near beams, those are span lengths — report them

Output a JSON object with key "elements" containing an array. Each element must have:
{
  "element_type": "column|beam|slab|footing|staircase|lintel|wall",
  "label": "exact label from drawing (e.g. B1, B2, C1)",
  "width": number_in_mm_or_null,
  "depth": number_in_mm_or_null,
  "length": number_in_mm_or_null,
  "main_bar_dia": number_in_mm_or_null,
  "main_bar_count": number_or_null,
  "stirrup_dia": number_in_mm_or_null,
  "stirrup_spacing": number_in_mm_or_null,
  "quantity": number
}

Only include elements you can clearly identify from the provided drawing data."""


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
        raise ValueError("No LLM API key configured. Set LLM_API_KEY or OPENAI_API_KEY environment variable.")

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

IMPORTANT: ONLY report elements you can identify from the data above.
- Do NOT invent or infer elements that are not clearly indicated in the text annotations, layer names, or block names.
- If the drawing only shows beams, report ONLY beams. Do NOT add columns, slabs, or footings unless they are explicitly mentioned in the annotations.
- If reinforcement details (bar count, diameter, stirrup spacing) are not readable from the annotations, set those fields to null.
- The label should be the exact label from the drawing (e.g., "B1", "B9", "B19") — do NOT add descriptions like "Main Beam" or "Internal Beam"."""

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
                    if not item.get("width") or not item.get("depth"):
                        print(f"Skipping element '{item.get('label', '?')}': missing dimensions")
                        continue

                    element = StructuralElement(
                        element_type=ElementType(item["element_type"]),
                        label=item.get("label", "Unknown"),
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
                    print(f"Skipping element due to parse error: {e}")
                    continue

            return elements

    except httpx.TimeoutException:
        raise ValueError("LLM API request timed out after 90 seconds. The service may be overloaded.")
    except httpx.ConnectError as e:
        raise ConnectionError(f"Cannot connect to LLM API at {api_base}: {e}")
    except httpx.HTTPStatusError as e:
        raise ValueError(f"LLM API returned HTTP {e.response.status_code}: {e.response.text[:300]}")
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON response: {e}")
    except Exception as e:
        raise RuntimeError(f"LLM interpretation failed: {type(e).__name__}: {e}")


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
        raise ValueError("No LLM API key configured. Set LLM_API_KEY or OPENAI_API_KEY environment variable.")

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
1. Verify the detected elements against the drawing context - remove any that don't appear in the actual data.
2. If reinforcement details are clearly readable from the text annotations, fill them in.
3. Do NOT add elements that are not in the drawing. Do NOT invent columns, slabs, or footings.
4. Do NOT fill in reinforcement with "typical" or "standard" values — only use values from the actual annotations.
5. Return ONLY the elements that are actually present in the drawing data.

Return a JSON object with key "elements" containing the verified array."""

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
                    if not item.get("width") or not item.get("depth"):
                        continue

                    element = StructuralElement(
                        element_type=ElementType(item["element_type"]),
                        label=item.get("label", "Unknown"),
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
                    result.append(element)
                except (ValueError, KeyError):
                    continue

            # Only use LLM result if it found more elements than we started with
            if len(result) >= len(elements):
                return result
            raise ValueError(
                f"LLM enhancement returned fewer elements ({len(result)}) than input ({len(elements)}). "
                f"The LLM may have dropped elements during processing."
            )

    except httpx.TimeoutException:
        raise ValueError("LLM enhancement request timed out after 90 seconds.")
    except httpx.ConnectError as e:
        raise ConnectionError(f"Cannot connect to LLM API at {api_base}: {e}")
    except httpx.HTTPStatusError as e:
        raise ValueError(f"LLM API returned HTTP {e.response.status_code}: {e.response.text[:300]}")
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON during enhancement: {e}")
    except (ValueError, ConnectionError):
        raise
    except Exception as e:
        raise RuntimeError(f"LLM enhancement failed: {type(e).__name__}: {e}")
