"""Uniform error body: {"error": {"code", "message", "request_id", ...}}."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(code)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def not_found() -> AppError:
    # Same body for "does not exist" and "belongs to another company/user" (SEC-03).
    return AppError(404, "NOT_FOUND", "요청한 정보를 찾을 수 없습니다.")


def conflict(code: str, message: str, **extra: Any) -> AppError:
    return AppError(409, code, message, **extra)


def _body(request: Request, code: str, message: str, **extra: Any) -> dict:
    return {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(request, exc.code, exc.message, **exc.extra), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Field locations and messages only; never echo submitted values (they may hold PII).
        fields = [{"loc": [str(p) for p in e["loc"]], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(_body(request, "VALIDATION", "입력값을 확인해 주세요.", fields=fields), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(request, code, "요청을 처리할 수 없습니다."), status_code=exc.status_code)
