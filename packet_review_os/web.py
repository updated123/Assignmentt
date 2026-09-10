"""The web app a non-developer actually uses.

Boundaries this module is responsible for:

* **Input limits.** Uploads are read in bounded chunks and refused past
  ``MAX_UPLOAD_BYTES``, so a large file cannot be buffered into memory first and
  rejected afterwards.
* **Not blocking the event loop.** ``run_review`` is synchronous and can spend
  tens of seconds inside the model call. Sync endpoints are already run in a
  worker thread by Starlette; the JSON endpoint is async, so it hands the work to
  a thread explicitly rather than stalling every other request.
* **Access.** The app binds to loopback by default and needs no auth there. If
  the operator binds it to a reachable interface, ``API_TOKEN`` becomes required
  -- otherwise stored candidate reviews would be readable by anyone on the
  network. See docs/RUNBOOK.md.
* **Readable failure.** A bad PDF, an invalid scorecard or an unexpected error
  renders a page that says what to do next, never a traceback.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .config import ConfigError, app_config, list_roles, settings, validate_all
from .pdf_extract import PdfExtractError, extract_pdf_text
from .pipeline import run_review, safe_log_packet
from .schemas import PacketInput, ReviewResult
from .storage import approve_review, get_review, init_db, list_reviews, save_review

logger = logging.getLogger("packet_review_os.web")
PACKAGE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE / "templates"))

UPLOAD_CHUNK = 64 * 1024
TOKEN_COOKIE = "prs_token"  # noqa: S105 - cookie name, not a credential


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Validate config and prepare the store before the first request lands."""
    logging.basicConfig(level=getattr(logging, settings().log_level, logging.INFO))
    problems = validate_all()
    if problems:
        # Fail loudly at startup rather than on the reviewer's first packet.
        raise ConfigError("Cannot start with invalid configuration:\n" + "\n".join(problems))
    init_db()
    cfg = settings()
    logger.info(
        "Packet Review OS ready. model=%s roles=%d public_bind=%s token_set=%s",
        cfg.openai_model if cfg.llm_enabled else "none",
        len(list_roles()),
        cfg.binds_publicly,
        bool(cfg.api_token),
    )
    if cfg.binds_publicly and not cfg.api_token:
        logger.warning(
            "Bound to %s with no API_TOKEN. Stored reviews are readable by anyone who can reach this port.",
            cfg.app_host,
        )
    yield


app = FastAPI(title="Packet Review OS", version=app_config().version, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")


async def require_access(request: Request) -> None:
    """No-op on a loopback deployment; enforces API_TOKEN when one is configured.

    Accepts the token as an ``X-API-Token`` header, a ``?token=`` query parameter
    (which then sets a cookie so the reviewer only pastes it once), or the cookie.
    """
    expected = settings().api_token.strip()
    if not expected:
        return
    supplied = (
        request.headers.get("x-api-token")
        or request.query_params.get("token")
        or request.cookies.get(TOKEN_COOKIE)
        or ""
    )
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid access token.")


def _context(request: Request, **extra) -> dict:
    ctx = {
        "request": request,
        "roles": list_roles(),
        "llm_on": settings().llm_enabled,
        "version": app_config().version,
    }
    ctx.update(extra)
    return ctx


def _error_page(request: Request, message: str, status_code: int, hint: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "error.html",
        _context(request, message=message, hint=hint, status_code=status_code),
        status_code=status_code,
    )


def _maybe_set_token_cookie(response, request: Request):
    """Remember a token supplied in the URL so the reviewer pastes it once."""
    token = request.query_params.get("token")
    if token and settings().api_token.strip() and secrets.compare_digest(token, settings().api_token.strip()):
        response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict", max_age=86400)
    return response


async def _read_upload(pdf: UploadFile) -> bytes:
    """Read an upload in chunks, refusing anything past the configured cap."""
    limit = settings().max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await pdf.read(UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise PdfExtractError(
                f"{pdf.filename} is larger than {limit // 1_000_000} MB. "
                "Paste the relevant pages as text instead."
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.exception_handler(ConfigError)
async def _config_error_handler(request: Request, exc: ConfigError):
    logger.error("Configuration error on %s: %s", request.url.path, exc)
    return _error_page(
        request,
        str(exc),
        status_code=500,
        hint="Fix the file named above, then run `python -m packet_review_os check`.",
    )


@app.exception_handler(HTTPException)
async def _http_error_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    hints = {
        401: "Open the app using the link that includes ?token=... , or set API_TOKEN in .env.",
        404: "Check the run id, or open History to see stored reviews.",
    }
    return _error_page(request, str(exc.detail), status_code=exc.status_code, hint=hints.get(exc.status_code, ""))


@app.get("/", response_class=HTMLResponse)
def home(request: Request, _: None = Depends(require_access)):
    page = templates.TemplateResponse(request, "index.html", _context(request, error=None))
    return _maybe_set_token_cookie(page, request)


@app.post("/review", response_class=HTMLResponse)
async def review(
    request: Request,
    role_id: str = Form(...),
    packet_text: str = Form(""),
    recruiter_notes: str = Form(""),
    candidate_name: str = Form(""),
    job_url: str = Form(""),
    pdf: UploadFile | None = File(None),
    _: None = Depends(require_access),
):
    pdf_text = ""
    source = "paste"
    if pdf and pdf.filename:
        try:
            raw = await _read_upload(pdf)
            if raw:
                pdf_text = extract_pdf_text(raw, pdf.filename)
                source = "pdf"
        except PdfExtractError as exc:
            return templates.TemplateResponse(
                request, "index.html", _context(request, error=str(exc)), status_code=400
            )

    if not (packet_text.strip() or pdf_text):
        return templates.TemplateResponse(
            request,
            "index.html",
            _context(request, error="Paste a packet or upload a PDF resume before running a review."),
            status_code=400,
        )

    payload = PacketInput(
        role_id=role_id,
        packet_text=packet_text,
        recruiter_notes=recruiter_notes,
        candidate_name=candidate_name,
        job_url=job_url,
        source=source,
    )
    # run_review is synchronous; this endpoint is async, so keep the loop free.
    result = await run_in_threadpool(run_review, payload, pdf_text=pdf_text)
    await run_in_threadpool(save_review, result, safe_log_packet(packet_text + "\n" + pdf_text))
    logger.info(
        "Review %s role=%s action=%s engine=%s latency=%dms",
        result.run_id,
        result.role_id,
        result.next_action.value,
        result.engine_mode.value,
        result.latency_ms,
    )
    return templates.TemplateResponse(request, "result.html", _context(request, result=result))


@app.post("/review/{run_id}/decision")
async def decision(
    run_id: str,
    approved: str = Form(...),
    note: str = Form(""),
    _: None = Depends(require_access),
):
    if approved not in {"approve", "send_back"}:
        raise HTTPException(status_code=400, detail="Decision must be approve or send_back.")
    record = await run_in_threadpool(approve_review, run_id, approved == "approve", note)
    if not record:
        raise HTTPException(status_code=404, detail="Review not found. It may have been cleared.")
    return RedirectResponse(url=f"/review/{run_id}", status_code=303)


@app.get("/review/{run_id}", response_class=HTMLResponse)
def review_detail(request: Request, run_id: str, _: None = Depends(require_access)):
    data = get_review(run_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"No stored review with id {run_id}.")
    result = ReviewResult.model_validate(data)
    return templates.TemplateResponse(
        request,
        "result.html",
        _context(
            request,
            result=result,
            approved=data.get("approved"),
            approver_note=data.get("approver_note") or "",
        ),
    )


@app.get("/history", response_class=HTMLResponse)
def history(request: Request, _: None = Depends(require_access)):
    return templates.TemplateResponse(request, "history.html", _context(request, records=list_reviews()))


@app.get("/health")
def health():
    """Unauthenticated liveness probe. Deliberately reveals nothing about candidates."""
    cfg = settings()
    return {
        "ok": True,
        "version": app_config().version,
        "model_configured": cfg.llm_enabled,
        "config_problems": len(validate_all()),
    }


@app.post("/api/review")
async def api_review(payload: PacketInput, _: None = Depends(require_access)):
    try:
        result = await run_in_threadpool(run_review, payload)
    except ConfigError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    await run_in_threadpool(save_review, result, safe_log_packet(payload.packet_text))
    return JSONResponse(result.to_public_dict())
