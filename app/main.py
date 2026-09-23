"""supload - a metadata-stripping, self-expiring file drop.

Request flow for an upload:

    stream to a temp file under a hard size cap
    -> detect the real type from magic bytes (the client's Content-Type and
       filename are never trusted)
    -> sanitize in a sandboxed child process
    -> encrypt with a key derived from a fresh 256-bit token
    -> store, and hand back a link that exists nowhere on the server

Two origins are served from this one app. supload.org carries the UI; raw
bytes come only from files.supload.org, so a stored payload can never execute
against the origin that hosts the interface.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import config, crypto, db, detect, pages, ratelimit, sanitize, sweeper

log = logging.getLogger("supload")

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
RAW_RE = re.compile(r"^/d/[A-Za-z0-9_-]{20,64}/raw$")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    db.init()
    task = asyncio.create_task(sweeper.run_forever())
    log.info("supload up: %d kinds accepted", len(sanitize.supported_kinds()))
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --------------------------------------------------------------------------
# helpers


def _host(request: Request) -> str:
    return (request.headers.get("host") or "").split(":")[0].lower()


def safe_display_name(original: str | None, kind: str) -> str:
    """Rebuild a filename for display and download.

    The uploader's name is never used to address anything on disk, so this is
    purely cosmetic - but it still gets stripped of path separators and control
    characters, and the extension is forced to match what the bytes actually
    are rather than what the upload claimed.
    """
    base = Path(original or "file").name
    stem = Path(base).stem[:64]
    stem = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "_", stem).strip(" .")
    return f"{stem or 'file'}.{detect.extension_for(kind)}"


def content_disposition(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_")
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{urllib.parse.quote(name, safe='')}"
    )


def human_remaining(seconds: int) -> str:
    seconds = max(0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _json_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _gone() -> HTMLResponse:
    """One response for missing, expired and blocked alike, so the service
    never confirms that a given link ever existed."""
    return HTMLResponse(
        pages.message_page(
            "Link not found",
            "This link has expired, been deleted, or never existed.",
        ),
        status_code=404,
    )


# --------------------------------------------------------------------------
# middleware


@app.middleware("http")
async def host_and_headers(request: Request, call_next):
    host = _host(request)
    path = request.url.path
    on_file_host = host == config.FILE_HOST
    # Raw bytes get the sandbox CSP because of what they are, not because of
    # which name they arrived under, so a host misconfiguration cannot quietly
    # downgrade them.
    serving_bytes = on_file_host or RAW_RE.match(path) is not None

    if config.ENFORCE_HOSTS:
        if host == f"www.{config.APP_HOST}":
            target = f"{config.PUBLIC_SCHEME}://{config.APP_HOST}{path}"
            return RedirectResponse(target, status_code=301)
        if host not in (config.APP_HOST, config.FILE_HOST):
            return PlainTextResponse("Not found", status_code=404)
        # The file host serves bytes and nothing else; the app host never
        # serves bytes. Neither will do the other's job.
        if on_file_host and not (RAW_RE.match(path) or path == "/healthz"):
            return PlainTextResponse("Not found", status_code=404)
        if not on_file_host and RAW_RE.match(path):
            return PlainTextResponse("Not found", status_code=404)

    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # The token lives in the URL, so it must never ride along in a Referer.
    response.headers["Referrer-Policy"] = "no-referrer"
    if config.PUBLIC_SCHEME == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if serving_bytes:
        response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    elif path == "/watermark":
        # This page handles passports and bank letters, and does all its work
        # in the browser. connect-src 'none' turns "we do not upload your
        # image" from a claim about the code into a rule the browser enforces:
        # no fetch, no XHR, no WebSocket, no beacon, to anywhere, ever.
        #
        # blob: must be listed explicitly for img-src. Blob URLs are how the
        # page loads the file you picked, and 'self' does NOT cover them - so
        # without this the page cannot open an image at all.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' blob: data:; connect-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
    if not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------------
# routes


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(
        pages.upload_page(
            config.MAX_FILE_SIZE,
            sorted(detect.extension_for(k) for k in sanitize.supported_kinds()),
            config.RETENTION_SECONDS // 3600,
        )
    )


@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")


@app.get("/robots.txt")
async def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/watermark", response_class=HTMLResponse)
async def watermark():
    """Runs entirely client-side. No upload endpoint backs this page."""
    return HTMLResponse(pages.watermark_page())


@app.get("/abuse", response_class=HTMLResponse)
async def abuse():
    return HTMLResponse(pages.abuse_page())


@app.post("/api/upload")
async def api_upload(request: Request, file: UploadFile = File(...)):
    ip_hash = ratelimit.hash_ip(ratelimit.client_ip(request))
    limit_mb = config.MAX_FILE_SIZE // (1024 * 1024)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > config.MAX_FILE_SIZE + (1 << 20):
        return _json_error(413, f"That file is over the {limit_mb} MB limit.")

    try:
        ratelimit.check(ip_hash, int(declared) if declared and declared.isdigit() else 0)
    except ratelimit.RateLimited as exc:
        return _json_error(429, str(exc))

    if db.total_stored_bytes() >= config.TOTAL_QUOTA:
        return _json_error(507, "The server is at capacity. Please try again later.")

    config.ensure_dirs()
    job = uuid.uuid4().hex
    raw_path = config.TMP_DIR / f"{job}.in"
    clean_path = config.TMP_DIR / f"{job}.out"

    try:
        size = 0
        with open(raw_path, "wb") as out:
            while chunk := await file.read(config.CHUNK_SIZE):
                size += len(chunk)
                if size > config.MAX_FILE_SIZE:
                    return _json_error(413, f"That file is over the {limit_mb} MB limit.")
                out.write(chunk)

        if size == 0:
            return _json_error(400, "That file is empty.")

        try:
            kind = detect.detect(raw_path)
        except detect.Rejected as exc:
            return _json_error(415, str(exc))

        if kind not in sanitize.supported_kinds():
            return _json_error(415, "That file type is not accepted on this server.")

        try:
            await sanitize.run_async(kind, raw_path, clean_path)
        except sanitize.SanitizeRejected as exc:
            return _json_error(415, str(exc))
        except sanitize.SanitizeError:
            log.exception("sanitizer failed for kind=%s", kind)
            return _json_error(500, "That file could not be processed.")

        clean_size = clean_path.stat().st_size
        token = crypto.new_token()
        delete_token = crypto.new_token()
        salt = secrets.token_bytes(16)
        nonce_prefix = secrets.token_bytes(4)
        blob_id = secrets.token_hex(16)
        blob_path = config.BLOB_DIR / blob_id

        crypto.encrypt_file(
            clean_path, blob_path, crypto.derive_key(token, salt), nonce_prefix
        )
        blob_path.chmod(0o600)

        now = int(time.time())
        expires_at = now + config.RETENTION_SECONDS
        display = safe_display_name(file.filename, kind)
        db.insert_file(
            lookup_key=crypto.lookup_key(token),
            delete_key=crypto.lookup_key(delete_token),
            blob_id=blob_id,
            salt=salt,
            nonce_prefix=nonce_prefix,
            kind=kind,
            mime=detect.mime_for(kind),
            orig_name=display,
            size=clean_size,
            created_at=now,
            expires_at=expires_at,
        )
        ratelimit.record(ip_hash, size)
        log.info("stored kind=%s size=%d", kind, clean_size)

        base = f"{config.PUBLIC_SCHEME}://{config.APP_HOST}"
        files_base = f"{config.PUBLIC_SCHEME}://{config.FILE_HOST}"
        return JSONResponse(
            {
                "url": f"{base}/d/{token}",
                "file_url": f"{files_base}/d/{token}/raw",
                "delete_url": f"{base}/x/{delete_token}",
                "name": display,
                "size": clean_size,
                "expires_at": expires_at,
            }
        )
    finally:
        raw_path.unlink(missing_ok=True)
        clean_path.unlink(missing_ok=True)


@app.get("/d/{token}", response_class=HTMLResponse)
async def download_landing(token: str):
    """A landing page rather than the bytes, so link previewers in chat apps
    do not fetch the file the moment the URL is pasted."""
    if not TOKEN_RE.match(token):
        return _gone()
    row = db.get_file(crypto.lookup_key(token))
    if row is None:
        return _gone()
    return HTMLResponse(
        pages.download_page(
            name=row["orig_name"],
            size=row["size"],
            expires_in=human_remaining(row["expires_at"] - int(time.time())),
            url=f"{config.PUBLIC_SCHEME}://{config.FILE_HOST}/d/{token}/raw",
            token=token,
        )
    )


@app.get("/d/{token}/raw")
async def download_raw(token: str):
    if not TOKEN_RE.match(token):
        return PlainTextResponse("Not found", status_code=404)
    row = db.get_file(crypto.lookup_key(token))
    if row is None:
        return PlainTextResponse("Not found", status_code=404)

    blob = config.BLOB_DIR / row["blob_id"]
    if not blob.is_file():
        log.error("row present but blob missing")
        return PlainTextResponse("Not found", status_code=404)

    key = crypto.derive_key(token, row["salt"])
    db.bump_download(row["lookup_key"])

    # Everything is served as an attachment. Nothing this service hosts is
    # ever rendered in a browser tab, which removes the whole class of
    # "upload a page, phish from a trusted domain" abuse.
    media_type = row["mime"] if detect.is_inline_safe(row["kind"]) else "application/octet-stream"
    return StreamingResponse(
        crypto.decrypt_iter(blob, key, row["nonce_prefix"]),
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(row["orig_name"]),
            "Content-Length": str(row["size"]),
        },
    )


@app.head("/d/{token}/raw")
async def download_raw_head(token: str):
    """Clients and download managers check size and type before fetching.
    Answer from metadata alone - nothing is decrypted, nothing is counted."""
    if not TOKEN_RE.match(token):
        return Response(status_code=404)
    row = db.get_file(crypto.lookup_key(token))
    if row is None:
        return Response(status_code=404)
    media_type = row["mime"] if detect.is_inline_safe(row["kind"]) else "application/octet-stream"
    return Response(
        status_code=200,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(row["orig_name"]),
            "Content-Length": str(row["size"]),
        },
    )


@app.head("/d/{token}")
async def download_landing_head(token: str):
    live = bool(TOKEN_RE.match(token)) and db.get_file(crypto.lookup_key(token)) is not None
    return Response(status_code=200 if live else 404)


@app.get("/r/{token}", response_class=HTMLResponse)
async def report_form(token: str):
    if not TOKEN_RE.match(token):
        return _gone()
    row = db.get_file(crypto.lookup_key(token))
    if row is None:
        return _gone()
    return HTMLResponse(pages.report_page(row["orig_name"], f"/r/{token}"))


@app.post("/r/{token}", response_class=HTMLResponse)
async def report_submit(token: str):
    if TOKEN_RE.match(token):
        row = db.get_file(crypto.lookup_key(token))
        if row is not None:
            db.mark_reported(row["lookup_key"])
            log.warning("file reported blob=%s", row["blob_id"])
    return HTMLResponse(
        pages.message_page("Reported", "Thanks. This upload has been flagged for review.")
    )


@app.get("/x/{delete_token}", response_class=HTMLResponse)
async def delete_form(delete_token: str):
    if not TOKEN_RE.match(delete_token):
        return _gone()
    row = db.get_by_delete_key(crypto.lookup_key(delete_token))
    if row is None:
        return _gone()
    return HTMLResponse(pages.confirm_delete_page(row["orig_name"], f"/x/{delete_token}"))


@app.post("/x/{delete_token}", response_class=HTMLResponse)
async def delete_submit(delete_token: str):
    if not TOKEN_RE.match(delete_token):
        return _gone()
    blob_id = db.delete_by_delete_key(crypto.lookup_key(delete_token))
    if blob_id is None:
        return _gone()
    (config.BLOB_DIR / blob_id).unlink(missing_ok=True)
    return HTMLResponse(
        pages.message_page("Deleted", "That file is gone. The link no longer works.")
    )
