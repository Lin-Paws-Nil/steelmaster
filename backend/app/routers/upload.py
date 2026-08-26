"""Upload router - handles DWG/DXF/PDF file uploads and parsing."""

import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.app.services.dwg_service import parse_file, extract_and_parse
from backend.app.services.pdf_service import (
    parse_pdf_file, render_pdf_pages, analyze_pdf_with_vision,
    analyze_pdf_hybrid, extract_and_parse_pdf,
)
from backend.app.models.schemas import DWGParseResult, ExtractionResult

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

SUPPORTED_EXTENSIONS = (".dwg", ".dxf", ".pdf")


@router.post("/upload", response_model=DWGParseResult)
async def upload_file(file: UploadFile = File(...)):
    """Upload a DWG/DXF/PDF file and parse structural elements."""

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    file_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        if ext == ".pdf":
            result = parse_pdf_file(str(save_path))
        else:
            result = parse_file(str(save_path))
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to parse file: {str(e)}"
        )

    if result is None:
        raise HTTPException(
            status_code=422,
            detail="Could not parse this file."
        )

    return result


@router.post("/upload-and-estimate")
async def upload_and_estimate(file: UploadFile = File(...), use_llm: bool = False):
    """Upload a file, parse it, and immediately estimate steel."""

    from backend.app.services.steel_estimator import estimate_project
    from backend.app.services.llm_service import interpret_drawing_with_llm, enhance_elements_with_llm

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    file_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # Parse based on file type
    try:
        if ext == ".pdf":
            parse_result = parse_pdf_file(str(save_path))
        else:
            parse_result = parse_file(str(save_path))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to parse: {str(e)}")

    if parse_result is None:
        raise HTTPException(status_code=422, detail="Unsupported file format.")

    elements = parse_result.elements_detected
    parse_method = parse_result.metadata.get("parse_method", "")

    # For PDF files: try hybrid OCR pipeline first, fall back to LLM vision
    if ext == ".pdf":
        if use_llm or not elements:
            import os
            api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")

            # Step 1: Try hybrid OCR + spatial logic (fast, deterministic)
            try:
                hybrid_elements = await analyze_pdf_hybrid(
                    str(save_path), file.filename or "drawing.pdf",
                    api_key=api_key,
                )
                if hybrid_elements:
                    elements = hybrid_elements
            except Exception as e:
                print(f"Hybrid OCR pipeline failed (will try LLM vision): {e}")
                hybrid_elements = []

            # Step 2: If hybrid found nothing (raster PDF or no beam labels), use LLM vision
            if not elements:
                try:
                    pages = render_pdf_pages(str(save_path), dpi=250)
                    vision_elements = await analyze_pdf_with_vision(
                        pages, file.filename or "drawing.pdf"
                    )
                    if vision_elements:
                        elements = vision_elements
                    else:
                        raise HTTPException(
                            status_code=422,
                            detail="No structural elements detected. Neither OCR nor Vision API could extract beam data from this PDF."
                        )
                except HTTPException:
                    raise
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=f"PDF analysis failed: {str(e)}")
                except ConnectionError as e:
                    raise HTTPException(status_code=503, detail=f"Cannot reach LLM service: {str(e)}")
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Unexpected error during PDF analysis: {type(e).__name__}: {str(e)}")

    # For DWG/DXF files: use LLM only when fallback method was used or explicitly requested
    elif parse_method == "binary_fallback":
        if not elements:
            try:
                elements = await interpret_drawing_with_llm(parse_result)
            except (ValueError, ConnectionError, RuntimeError) as e:
                raise HTTPException(status_code=422, detail=f"LLM interpretation failed for DWG binary fallback: {str(e)}")
        else:
            try:
                elements = await enhance_elements_with_llm(
                    elements, parse_result=parse_result
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                raise HTTPException(status_code=422, detail=f"LLM enhancement failed: {str(e)}")

    # For ACadSharp/ezdxf parsed files, optionally enhance with LLM
    elif use_llm:
        if not elements:
            try:
                elements = await interpret_drawing_with_llm(parse_result)
            except (ValueError, ConnectionError, RuntimeError) as e:
                raise HTTPException(status_code=422, detail=f"LLM interpretation failed: {str(e)}")
        else:
            try:
                elements = await enhance_elements_with_llm(
                    elements, parse_result=parse_result
                )
            except (ValueError, ConnectionError, RuntimeError) as e:
                raise HTTPException(status_code=422, detail=f"LLM enhancement failed: {str(e)}")

    if not elements:
        if parse_method == "binary_fallback":
            raise HTTPException(
                status_code=422,
                detail="DWG file was read but its internal data could not be decoded. This is a limitation of the proprietary DWG format."
            )

        raise HTTPException(
            status_code=422,
            detail="No structural elements could be detected from this file. Ensure the drawing contains readable structural annotations."
        )

    try:
        estimate = estimate_project(
            project_name=file.filename or "Unnamed Project",
            elements=elements,
        )
    except ValueError as e:
        # Estimation failed — return the parsed elements with error detail
        # so the frontend can show what was found and what's missing
        missing_fields = []
        for elem in elements:
            issues = []
            if not elem.length:
                issues.append("span/length")
            if not elem.main_bar_dia and not elem.bottom_bar_dia and not elem.reinforcement_detail:
                issues.append("reinforcement")
            if not elem.stirrup_dia and not elem.stirrup_spacing and not (elem.reinforcement_detail and (elem.reinforcement_detail.stirrup_end_zone or elem.reinforcement_detail.stirrup_mid_zone or elem.reinforcement_detail.stirrup_support_zone)):
                issues.append("stirrups")
            if issues:
                missing_fields.append(f"{elem.label}: missing {', '.join(issues)}")

        return {
            "parse_result": parse_result,
            "estimate": None,
            "elements_found": [
                {
                    "label": e.label,
                    "type": e.element_type.value,
                    "width": e.width,
                    "depth": e.depth,
                    "length": e.length,
                    "has_reinforcement": bool(e.main_bar_dia or e.bottom_bar_dia or e.reinforcement_detail),
                    "has_stirrups": bool(e.stirrup_dia or (e.reinforcement_detail and (e.reinforcement_detail.stirrup_end_zone or e.reinforcement_detail.stirrup_mid_zone))),
                }
                for e in elements
            ],
            "message": f"Found {len(elements)} elements but cannot estimate steel — missing required data.",
            "missing_data": missing_fields,
            "error": str(e),
        }

    return {
        "parse_result": parse_result,
        "estimate": estimate,
        "message": f"Successfully estimated steel for {len(elements)} elements.",
    }


@router.post("/upload/dwg", response_model=ExtractionResult)
async def upload_dwg(file: UploadFile = File(...)):
    """
    Upload a DWG/DXF file and extract text entities with coordinates
    plus parsed beam details in the target JSON schema.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".dwg", ".dxf"):
        raise HTTPException(
            status_code=400,
            detail="This endpoint accepts .dwg and .dxf files only."
        )

    file_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        result = extract_and_parse(str(save_path))
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to extract from DWG/DXF: {str(e)}"
        )

    return result


@router.post("/upload/pdf", response_model=ExtractionResult)
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a PDF file and extract text blocks with bounding box coordinates
    plus parsed beam details in the target JSON schema.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(
            status_code=400,
            detail="This endpoint accepts .pdf files only."
        )

    file_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{file_id}{ext}"

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        result = extract_and_parse_pdf(str(save_path))
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Failed to extract from PDF: {str(e)}"
        )

    return result
