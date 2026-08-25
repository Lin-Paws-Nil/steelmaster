"""Upload router - handles DWG/DXF/PDF file uploads and parsing."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException

from backend.app.services.dwg_service import parse_file, extract_and_parse
from backend.app.services.pdf_service import (
    parse_pdf_file, render_pdf_pages, analyze_pdf_with_vision, extract_and_parse_pdf,
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
    llm_error = None

    # For PDF files, always use vision LLM to analyze the drawing images
    if ext == ".pdf":
        if use_llm or not elements:
            try:
                pages = render_pdf_pages(str(save_path), dpi=250)
                vision_elements = await analyze_pdf_with_vision(
                    pages, file.filename or "drawing.pdf"
                )
                if vision_elements:
                    elements = vision_elements
                else:
                    llm_error = "Vision API returned no elements. Check your LLM_API_KEY in .env"
            except Exception as e:
                llm_error = str(e)
                print(f"PDF vision analysis failed: {e}")
                if not elements:
                    elements = await interpret_drawing_with_llm(parse_result)

    # For DWG/DXF files: use LLM only when fallback method was used or explicitly requested
    elif parse_method == "binary_fallback":
        if not elements:
            elements = await interpret_drawing_with_llm(parse_result)
        else:
            elements = await enhance_elements_with_llm(
                elements, parse_result=parse_result
            )

    # For ACadSharp/ezdxf parsed files, optionally enhance with LLM
    elif use_llm:
        if not elements:
            elements = await interpret_drawing_with_llm(parse_result)
        else:
            elements = await enhance_elements_with_llm(
                elements, parse_result=parse_result
            )

    if not elements:
        if parse_method == "binary_fallback":
            parse_result.layers = []
            parse_result.raw_text_annotations = []
            parse_result.element_count = 0
            return {
                "parse_result": parse_result,
                "estimate": None,
                "message": (
                    "DWG file was read but its internal data could not be decoded. "
                    "This is a limitation of the proprietary DWG format."
                ),
            }

        error_detail = f" Error: {llm_error}" if llm_error else ""
        return {
            "parse_result": parse_result,
            "estimate": None,
            "message": (
                f"No structural elements could be detected.{error_detail} "
                "Ensure your LLM_API_KEY is configured in .env for AI-powered analysis."
            ),
        }

    estimate = estimate_project(
        project_name=file.filename or "Unnamed Project",
        elements=elements,
    )

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
