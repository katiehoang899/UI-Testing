"""Web UI + JSON API.

Run:  uvicorn uicheck.server:app --reload   (then open http://localhost:8000)
"""

from __future__ import annotations

from pathlib import Path

import pytesseract
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import pipeline
from .figma import FigmaError
from .models import Options

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_UPLOAD = 25 * 1024 * 1024
ALL_CHECKS = ("spacing", "padding", "font", "color", "size")

app = FastAPI(title="UI Spec Checker")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def _scale(value: str | None) -> float | None:
    if not value or value == "auto":
        return None
    try:
        v = float(value)
    except ValueError as e:
        raise HTTPException(400, f"Scale không hợp lệ: {value}") from e
    if not 0.5 <= v <= 5:
        raise HTTPException(400, "Scale phải trong khoảng 0.5 - 5")
    return v


async def _read(upload: UploadFile | None) -> bytes | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Ảnh quá lớn (tối đa 25MB).")
    return data or None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/compare")
async def compare(
    dev: UploadFile = File(..., description="Screenshot bản đã code"),
    design: UploadFile | None = File(None, description="Ảnh design (nếu không dùng Figma)"),
    figma_url: str = Form(""),
    figma_token: str = Form(""),
    design_scale: str = Form("auto"),
    dev_scale: str = Form("auto"),
    tol_spacing: float = Form(2.0),
    tol_font_size: float = Form(1.0),
    tol_color: float = Form(3.0),
    checks: str = Form(",".join(ALL_CHECKS)),
    ignore_top: float = Form(0.0),
    ignore_bottom: float = Form(0.0),
    lang: str = Form("vie+eng"),
) -> dict:
    dev_bytes = await _read(dev)
    design_bytes = await _read(design)
    if dev_bytes is None:
        raise HTTPException(400, "Thiếu screenshot bản dev.")
    if design_bytes is None and not figma_url.strip():
        raise HTTPException(400, "Cần upload ảnh design hoặc nhập link Figma.")
    if lang not in ("vie+eng", "eng", "vie", "eng+vie"):
        raise HTTPException(400, "Ngôn ngữ OCR không hỗ trợ.")

    opts = Options(
        tol_spacing=max(0.0, tol_spacing),
        tol_font_size=max(0.0, tol_font_size),
        tol_color=max(0.0, tol_color),
        checks=tuple(c for c in checks.split(",") if c in ALL_CHECKS),
        lang=lang,
        design_scale=_scale(design_scale),
        dev_scale=_scale(dev_scale),
        ignore_top=max(0.0, ignore_top),
        ignore_bottom=max(0.0, ignore_bottom),
    )
    try:
        # OCR + image analysis is CPU bound: keep it off the event loop.
        return await run_in_threadpool(
            pipeline.run,
            dev_bytes,
            None if figma_url.strip() else design_bytes,
            opts,
            figma_url.strip() or None,
            figma_token.strip() or None,
        )
    except pytesseract.TesseractNotFoundError as e:
        raise HTTPException(
            500, "Chưa cài Tesseract OCR trên máy chạy server (xem README) hoặc đặt biến môi trường TESSERACT_CMD."
        ) from e
    except FigmaError as e:
        raise HTTPException(400, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
