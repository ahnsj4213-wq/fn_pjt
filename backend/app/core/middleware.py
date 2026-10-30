"""Request id, security headers (07-security) and an access log without query strings or bodies."""
import asyncio
import logging
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse

from app.core.constants import RESUME_MAX_BYTES
from app.core.errors import _body
from app.core.logging import log_event

log = logging.getLogger("app.access")

# Uploads refused from Content-Length alone: the multipart form is parsed before the endpoint's own size check runs,
# and a body the frontend proxy cut short would leave that parse waiting until the request timed out.
UPLOAD_LIMITS = {"/api/candidate/resume-files": RESUME_MAX_BYTES}
MULTIPART_SLACK = 64 * 1024  # boundaries and part headers around the file
DRAIN_SEC = 5.0

API_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "microphone=(self), camera=(self), display-capture=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


def _declared_too_large(request: Request) -> bool:
    limit = UPLOAD_LIMITS.get(request.url.path)
    if limit is None or request.method != "POST":
        return False
    try:
        return int(request.headers.get("content-length", "0")) > limit + MULTIPART_SLACK
    except ValueError:
        return False


async def _drain(request: Request) -> None:
    """Read and drop what the client sends before answering: replying mid-upload resets the connection and the
    browser sees a network error instead of the message. A body cut short never ends, hence the time limit."""
    try:
        async with asyncio.timeout(DRAIN_SEC):
            async for _ in request.stream():
                pass
    except (TimeoutError, ClientDisconnect):
        pass


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id")
        request.state.request_id = rid if rid and len(rid) <= 64 and rid.isalnum() else secrets.token_hex(8)
        t0 = time.perf_counter()
        if _declared_too_large(request):
            await _drain(request)
            response = JSONResponse(_body(request, "FILE_TOO_LARGE", "파일 크기는 10MB 이하여야 합니다."), status_code=413)
        else:
            response = await call_next(request)
        for k, v in API_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["X-Request-ID"] = request.state.request_id
        principal = getattr(request.state, "principal", None)
        log_event(log, "http", request_id=request.state.request_id, method=request.method, path=request.url.path,
                  status_code=response.status_code, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                  user_id=str(principal.user_id) if principal else None)
        return response
