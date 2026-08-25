"""
Steel Estimator - FastAPI Backend

Main application entry point.
"""

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from backend.app.routers import upload, estimate, manual, describe
from backend.app.models.bbs import BBSRow

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

import os
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(
    title="Steel Estimator",
    description="Estimate reinforcement steel from structural drawings",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "frontend" / "static")), name="static")

app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(estimate.router, prefix="/api", tags=["estimate"])
app.include_router(manual.router, prefix="/api", tags=["manual"])
app.include_router(describe.router, prefix="/api", tags=["describe"])


@app.get("/")
async def root():
    from fastapi.responses import HTMLResponse
    index_path = BASE_DIR / "frontend" / "templates" / "index.html"
    return HTMLResponse(content=index_path.read_text())


@app.get("/health")
async def health():
    import os
    return {
        "status": "healthy",
        "version": "1.0.0",
        "llm_key_loaded": bool(os.getenv("LLM_API_KEY")),
        "ssl_verify": os.getenv("SSL_VERIFY", "NOT SET"),
        "llm_base": os.getenv("LLM_API_BASE", "NOT SET"),
    }


@app.get("/debug/test-llm")
async def test_llm():
    """Debug endpoint to test LLM connectivity from the server process."""
    import os
    import httpx

    api_key = os.getenv("LLM_API_KEY")
    api_base = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    model = os.getenv("LLM_MODEL", "gpt-4o")
    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() != "false"

    try:
        async with httpx.AsyncClient(timeout=15, verify=ssl_verify) as client:
            resp = await client.post(
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            )
            return {"status": resp.status_code, "body": resp.text[:300]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)}"}


@app.post("/generate-bbs/")
async def generate_bbs(bbs_rows: list[BBSRow]):
    """
    Generate a formatted BBS Excel report from calculated data.

    Accepts a JSON array of BBSRow objects and returns a downloadable
    .xlsx file with "BBS Details" and "Weight Summary" sheets.
    """
    from fastapi.responses import StreamingResponse
    import io
    from datetime import date

    from backend.app.services.export_service import BBSExporter

    if not bbs_rows:
        raise HTTPException(status_code=400, detail="No BBS data provided.")

    try:
        exporter = BBSExporter()
        excel_bytes = exporter.generate_excel_bytes(bbs_rows)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate Excel report: {str(e)}",
        )

    filename = f"BBS_Report_{date.today().isoformat()}.xlsx"

    return StreamingResponse(
        io.BytesIO(excel_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
