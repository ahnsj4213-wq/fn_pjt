"""Request id, security headers (07-security) and an access log without query strings or bodies."""
import logging
import secrets
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.logging import log_event

log = logging.getLogger("app.access")

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


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id")
        request.state.request_id = rid if rid and len(rid) <= 64 and rid.isalnum() else secrets.token_hex(8)
        t0 = time.perf_counter()
        response = await call_next(request)
        for k, v in API_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers["X-Request-ID"] = request.state.request_id
        principal = getattr(request.state, "principal", None)
        log_event(log, "http", request_id=request.state.request_id, method=request.method, path=request.url.path,
                  status_code=response.status_code, latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                  user_id=str(principal.user_id) if principal else None)
        return response
