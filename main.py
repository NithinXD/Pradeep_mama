from __future__ import annotations

import base64
import json
import io
import mimetypes
import os
import zlib
import urllib.request
from pathlib import Path
from typing import Any

import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

# Keep Playwright browser binaries inside the deployment artifact.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

from playwright.async_api import async_playwright


app = FastAPI(title="Performance Report PDF API")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(BASE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)
REPORT_TEMPLATE = TEMPLATE_ENV.get_template("app.html")

class ReportRequest(BaseModel):
    report_id: str = Field(..., min_length=1)
    report_date: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    profile_pic: str | None = None
    event_name: str = Field(..., min_length=1)
    confidence: int = Field(..., ge=0, le=5)
    creativity: int = Field(..., ge=0, le=5)
    technique: int = Field(..., ge=0, le=5)
    expression: int = Field(..., ge=0, le=5)
    overall_impact: int = Field(..., ge=0, le=5)
    score_value: float = Field(..., ge=0, le=5)
    bullets: list[str] = Field(..., min_length=3, max_length=3)


def stars(score: int) -> str:
    filled = "★" * int(score)
    empty = "☆" * max(0, 5 - int(score))
    return filled + empty


def _to_data_uri(image_bytes: bytes, content_type: str | None) -> str:
    resolved_content_type = content_type or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{resolved_content_type};base64,{encoded}"


def resolve_profile_pic(profile_pic: str | None) -> str | None:
    if not profile_pic:
        return None

    if profile_pic.startswith("data:"):
        return profile_pic

    if profile_pic.startswith(("http://", "https://")):
        with urllib.request.urlopen(profile_pic) as response:
            image_bytes = response.read()
            content_type = response.headers.get_content_type()
            return _to_data_uri(image_bytes, content_type)

    image_path = (BASE_DIR / profile_pic).resolve()
    if image_path.exists() and BASE_DIR in image_path.parents:
        content_type = mimetypes.guess_type(image_path.name)[0]
        return _to_data_uri(image_path.read_bytes(), content_type)

    return None


def build_qr_code_data(link: str) -> str:
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(link)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#3f51b5", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return _to_data_uri(buffer.getvalue(), "image/png")


def render_report_html(payload: ReportRequest, report_link: str) -> str:
    return REPORT_TEMPLATE.render(
        report_id=payload.report_id,
        report_date=payload.report_date,
        name=payload.name,
        profile_pic_data=resolve_profile_pic(payload.profile_pic),
        event_name=payload.event_name,
        confidence_stars=stars(payload.confidence),
        creativity_stars=stars(payload.creativity),
        technique_stars=stars(payload.technique),
        expression_stars=stars(payload.expression),
        overall_impact_stars=stars(payload.overall_impact),
        score_value=f"{payload.score_value:g}",
        bullets=payload.bullets,
        qr_code_data=build_qr_code_data(report_link),
        report_link=report_link,
    )


def payload_to_dict(payload: ReportRequest) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def payload_from_dict(data: dict[str, Any]) -> ReportRequest:
    if hasattr(ReportRequest, "model_validate"):
        return ReportRequest.model_validate(data)
    return ReportRequest.parse_obj(data)


def encode_report_token(payload: ReportRequest) -> str:
    payload_json = json.dumps(payload_to_dict(payload), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(payload_json, level=9)
    token = base64.urlsafe_b64encode(compressed).decode("ascii")
    return token.rstrip("=")


def decode_report_token(token: str) -> ReportRequest:
    padding = "=" * (-len(token) % 4)
    compressed = base64.urlsafe_b64decode(token + padding)
    payload_json = zlib.decompress(compressed).decode("utf-8")
    return payload_from_dict(json.loads(payload_json))


@app.post("/reports/pdf", response_class=Response)
async def generate_report_pdf(request: Request, payload: ReportRequest) -> Response:
    token = encode_report_token(payload)
    report_link = str(request.url_for("view_report", token=token))
    html = render_report_html(payload, report_link)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 900, "height": 1400}, device_scale_factor=2)
            await page.set_content(html, wait_until="networkidle")
            pdf_bytes = await page.pdf(format="A4", print_background=True, margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="PDF engine is unavailable on this deployment. Ensure Chromium is installed in the build step.",
        ) from exc

    filename = f"performance-report-{payload.report_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/reports/{token}", response_class=HTMLResponse, name="view_report")
async def view_report(request: Request, token: str) -> HTMLResponse:
    try:
        payload = decode_report_token(token)
    except Exception:
        raise HTTPException(status_code=404, detail="Report not found")

    report_link = str(request.url_for("view_report", token=token))
    html = render_report_html(payload, report_link)
    return HTMLResponse(content=html)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}