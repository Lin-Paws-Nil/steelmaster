"""AI-assisted building description router."""

import json
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.models.schemas import StructuralElement, ElementType, ProjectEstimate
from backend.app.services.steel_estimator import estimate_project

router = APIRouter()

BUILDING_PROMPT = """You are an expert structural engineer. Given a description of a building, generate a COMPLETE list of ALL structural elements needed.

CRITICAL: Be THOROUGH. For every building include:
- ALL columns (at every grid intersection, every floor)
- ALL beams - peripheral beams along walls, internal beams between columns in BOTH X and Y directions, plinth beams
- ALL slab panels (between beams)
- ALL footings (one per column minimum)
- Lintels over all doors and windows
- Staircases if mentioned

For a typical G+1 (ground + 1 floor) building with ~6 columns:
- Columns: 6 x 2 floors = 12 column segments
- Beams per floor: typically 8-12 (peripheral + internal both directions)  
- Total beams: 16-24 for 2 floors
- Slab panels: 4-6 per floor
- Footings: 6 (one per column)
- Lintels: 6-10 (over doors and windows)

Output a JSON object with key "elements". Each element:
{
  "element_type": "column|beam|slab|footing|staircase|lintel",
  "label": "descriptive label",
  "width": mm,
  "depth": mm, 
  "length": mm,
  "main_bar_dia": mm,
  "main_bar_count": number,
  "stirrup_dia": mm,
  "stirrup_spacing": mm,
  "quantity": number
}"""


class BuildingDescription(BaseModel):
    description: str
    project_name: str = "AI Estimated Building"


@router.post("/describe-building", response_model=ProjectEstimate)
async def describe_building(request: BuildingDescription):
    """Generate steel estimate from a natural language building description."""

    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    api_base = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="LLM API key not configured. Set LLM_API_KEY in your .env file."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": BUILDING_PROMPT},
            {"role": "user", "content": request.description},
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
            elements_data = parsed if isinstance(parsed, list) else parsed.get("elements", [])

            elements = []
            for item in elements_data:
                try:
                    elements.append(StructuralElement(
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
                    ))
                except (ValueError, KeyError):
                    continue

            if not elements:
                raise HTTPException(
                    status_code=422,
                    detail="Could not generate elements from description. Try being more specific."
                )

            return estimate_project(request.project_name, elements)

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM API error: {e.response.status_code}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="LLM response was not valid JSON. Try again.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
