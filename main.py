from __future__ import annotations

import base64
import json
import io
import mimetypes
import os
import re
import zlib
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import qrcode
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient

# Keep Playwright browser binaries inside the deployment artifact.
from playwright.async_api import async_playwright


app = FastAPI(title="Performance Report PDF API")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_ENV = Environment(
    loader=FileSystemLoader(str(BASE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)
REPORT_TEMPLATE = TEMPLATE_ENV.get_template("app.html")
CERTIFICATE_TEMPLATE = TEMPLATE_ENV.get_template("certificate.html")
INSIGHT_PROMPT_PATH = BASE_DIR / "Prompt_AI Insights_PER.md"


def build_mongo_uri() -> str:
    direct_uri = os.getenv("MONGODB_URI")
    if direct_uri:
        return direct_uri

    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_cluster = os.getenv("DB_CLUSTER")
    db_name = os.getenv("DB_NAME")

    if not all([db_user, db_password, db_cluster, db_name]):
        raise RuntimeError("MongoDB environment variables are not configured")

    return (
        f"mongodb+srv://{quote_plus(db_user)}:{quote_plus(db_password)}"
        f"@{db_cluster}/{db_name}?retryWrites=true&w=majority&appName=Cluster0"
    )


def get_mongo_client() -> MongoClient:
    uri = build_mongo_uri()
    return MongoClient(uri, serverSelectionTimeoutMS=30000)

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


class CertificateRequest(BaseModel):
    certificate_id: str = Field(..., min_length=1)
    issued_date: str = Field(..., min_length=1)
    performer_name: str = Field(..., min_length=1)
    performance_title: str = Field(..., min_length=1)
    achievement_text: str = Field(..., min_length=1)
    honors_text: str = Field(default="Presented with Highest Honors", min_length=1)
    day_text: str = Field(default="On this Day", min_length=1)
    award_date_text: str = Field(..., min_length=1)
    organization_text: str = Field(..., min_length=1)
    background_image: str = Field(default="IMG_9174.PNG", min_length=1)


class InsightItem(BaseModel):
    title: str = Field(..., min_length=1)
    rating: float = Field(..., ge=0, le=5)
    comment: str = Field(..., min_length=1)

    @field_validator("rating")
    @classmethod
    def validate_rating(cls, value: float) -> float:
        if round(value * 2) != value * 2:
            raise ValueError("rating must be in 0.5 increments")
        return value


class InsightRequest(BaseModel):
    items: list[InsightItem] = Field(..., min_length=5, max_length=5)


class InsightResponse(BaseModel):
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


def render_certificate_html(payload: CertificateRequest, certificate_link: str) -> str:
    return CERTIFICATE_TEMPLATE.render(
        certificate_id=payload.certificate_id,
        issued_date=payload.issued_date,
        performer_name=payload.performer_name,
        performance_title=payload.performance_title,
        achievement_text=payload.achievement_text,
        honors_text=payload.honors_text,
        day_text=payload.day_text,
        award_date_text=payload.award_date_text,
        organization_text=payload.organization_text,
        background_image_data=resolve_profile_pic(payload.background_image),
        qr_code_data=build_qr_code_data(certificate_link),
        certificate_link=certificate_link,
    )


def payload_to_dict(payload: ReportRequest) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def payload_from_dict(data: dict[str, Any]) -> ReportRequest:
    if hasattr(ReportRequest, "model_validate"):
        return ReportRequest.model_validate(data)
    return ReportRequest.parse_obj(data)


def certificate_payload_to_dict(payload: CertificateRequest) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


def certificate_payload_from_dict(data: dict[str, Any]) -> CertificateRequest:
    if hasattr(CertificateRequest, "model_validate"):
        return CertificateRequest.model_validate(data)
    return CertificateRequest.parse_obj(data)


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


def encode_certificate_token(payload: CertificateRequest) -> str:
    payload_json = json.dumps(certificate_payload_to_dict(payload), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = zlib.compress(payload_json, level=9)
    token = base64.urlsafe_b64encode(compressed).decode("ascii")
    return token.rstrip("=")


def decode_certificate_token(token: str) -> CertificateRequest:
    padding = "=" * (-len(token) % 4)
    compressed = base64.urlsafe_b64decode(token + padding)
    payload_json = zlib.decompress(compressed).decode("utf-8")
    return certificate_payload_from_dict(json.loads(payload_json))


def _extract_three_bullets(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    bullets: list[str] = []

    for line in lines:
        cleaned = re.sub(r"^[-*\d.)\s]+", "", line).strip()
        if cleaned:
            bullets.append(cleaned)

    deduped: list[str] = []
    for bullet in bullets:
        if bullet not in deduped:
            deduped.append(bullet)

    return deduped[:3]


def load_insight_prompt_template() -> str:
    return INSIGHT_PROMPT_PATH.read_text(encoding="utf-8")


def render_insight_prompt(items: list[InsightItem]) -> str:
    prompt = load_insight_prompt_template()
    for index, item in enumerate(items, start=1):
        prompt = prompt.replace(f"{{item{index}_title}}", item.title.strip())
        prompt = prompt.replace(f"{{item{index}_rating}}", f"{item.rating:g}")
        prompt = prompt.replace(f"{{item{index}_comment}}", item.comment.strip())
    return prompt


def generate_three_insight_bullets(items: list[InsightItem]) -> list[str]:
    fireworks_api_key = os.getenv("FIREWORKS_API_KEY")
    if not fireworks_api_key:
        raise HTTPException(status_code=500, detail="Set FIREWORKS_API_KEY")

    prompt = render_insight_prompt(items)

    def parse_bullets(candidate_text: str, provider_name: str) -> list[str]:
        try:
            parsed = json.loads(candidate_text)
            bullets = [str(item).strip() for item in parsed.get("bullets", []) if str(item).strip()]
        except Exception:
            bullets = _extract_three_bullets(candidate_text)

        if len(bullets) != 3:
            bullets = _extract_three_bullets(candidate_text)

        if len(bullets) != 3:
            raise HTTPException(status_code=502, detail=f"{provider_name} did not return exactly 3 bullet points")

        return bullets

    fireworks_model = os.getenv("FIREWORKS_MODEL", "accounts/fireworks/models/kimi-k2p6")
    fireworks_payload = {
        "model": fireworks_model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": "Return valid JSON only with this exact shape: {\"bullets\":[\"...\",\"...\",\"...\"]}.",
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
    }

    fireworks_request = urllib.request.Request(
        "https://api.fireworks.ai/inference/v1/chat/completions",
        data=json.dumps(fireworks_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {fireworks_api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(fireworks_request, timeout=45) as response:
            fireworks_response = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise HTTPException(status_code=502, detail="Fireworks API rejected the request or key") from exc
        if exc.code == 429:
            raise HTTPException(status_code=429, detail="Fireworks API rate limit or quota exceeded") from exc
        raise HTTPException(status_code=503, detail="Fireworks API request failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Fireworks API request failed") from exc

    try:
        fireworks_text = fireworks_response["choices"][0]["message"]["content"]
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Unexpected Fireworks response format") from exc

    return parse_bullets(fireworks_text, "Fireworks")


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


@app.post("/certificates/pdf", response_class=Response)
async def generate_certificate_pdf(request: Request, payload: CertificateRequest) -> Response:
    token = encode_certificate_token(payload)
    certificate_link = str(request.url_for("view_certificate", token=token))
    html = render_certificate_html(payload, certificate_link)

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            page = await browser.new_page(viewport={"width": 1240, "height": 1754}, device_scale_factor=2)
            await page.set_content(html, wait_until="networkidle")
            pdf_bytes = await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            await browser.close()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="PDF engine is unavailable on this deployment. Ensure Chromium is installed in the build step.",
        ) from exc

    filename = f"certificate-{payload.certificate_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/certificates/{token}", response_class=HTMLResponse, name="view_certificate")
async def view_certificate(request: Request, token: str) -> HTMLResponse:
    try:
        payload = decode_certificate_token(token)
    except Exception:
        raise HTTPException(status_code=404, detail="Certificate not found")

    certificate_link = str(request.url_for("view_certificate", token=token))
    html = render_certificate_html(payload, certificate_link)
    return HTMLResponse(content=html)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/db-health")
async def db_health() -> dict[str, str]:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to connect to MongoDB") from exc

    return {"status": "ok", "database": "connected"}


@app.post("/insights", response_model=InsightResponse)
async def generate_insights(payload: InsightRequest) -> InsightResponse:
    cleaned_items = [
        InsightItem(title=item.title.strip(), rating=item.rating, comment=item.comment.strip())
        for item in payload.items
    ]

    if any(not item.title or not item.comment for item in cleaned_items):
        raise HTTPException(status_code=422, detail="All 5 items must include a title and comment")

    bullets = generate_three_insight_bullets(cleaned_items)
    return InsightResponse(bullets=bullets)