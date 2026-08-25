"""Manual input router - for manually specifying structural elements."""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from backend.app.models.schemas import ElementType, StructuralElement, ProjectEstimate
from backend.app.services.steel_estimator import estimate_project

router = APIRouter()


class ManualElementInput(BaseModel):
    element_type: ElementType
    label: str = ""
    width: float
    depth: float
    length: float
    clear_cover: float = 25.0
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
    quantity: int = 1


class ManualEstimateRequest(BaseModel):
    project_name: str = "Manual Estimate"
    elements: list[ManualElementInput]


@router.post("/manual-estimate", response_model=ProjectEstimate)
async def manual_estimate(request: ManualEstimateRequest):
    """Create steel estimate from manually entered element specifications."""

    structural_elements = []
    for i, elem in enumerate(request.elements):
        label = elem.label or f"{elem.element_type.value.title()} {i + 1}"
        structural_elements.append(StructuralElement(
            element_type=elem.element_type,
            label=label,
            width=elem.width,
            depth=elem.depth,
            length=elem.length,
            clear_cover=elem.clear_cover,
            concrete_grade=elem.concrete_grade,
            steel_grade=elem.steel_grade,
            main_bar_dia=elem.main_bar_dia,
            main_bar_count=elem.main_bar_count,
            top_bar_dia=elem.top_bar_dia,
            top_bar_count=elem.top_bar_count,
            bottom_bar_dia=elem.bottom_bar_dia,
            bottom_bar_count=elem.bottom_bar_count,
            stirrup_dia=elem.stirrup_dia,
            stirrup_spacing=elem.stirrup_spacing,
            quantity=elem.quantity,
        ))

    return estimate_project(request.project_name, structural_elements)
