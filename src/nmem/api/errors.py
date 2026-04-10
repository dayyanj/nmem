"""
Consistent error envelope and exception handlers for the nmem REST API.

All errors return a JSON body of the form:
    {"error": {"code": "...", "message": "...", "details": {...}},
     "meta": {"request_id": "...", "duration_ms": 0}}
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def _envelope(
    code: str, message: str, status_code: int,
    request_id: str | None = None, details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a consistent error response envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
            "meta": {
                "request_id": request_id or str(uuid.uuid4()),
            },
        },
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


async def _permission_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return _envelope("permission_denied", str(exc), 403, _request_id(request))


async def _value_handler(request: Request, exc: ValueError) -> JSONResponse:
    return _envelope("invalid_argument", str(exc), 400, _request_id(request))


async def _key_handler(request: Request, exc: KeyError) -> JSONResponse:
    return _envelope("not_found", f"Key not found: {exc}", 404, _request_id(request))


async def _validation_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    return _envelope(
        "validation_error",
        "Request validation failed",
        422,
        _request_id(request),
        details={"errors": exc.errors()},
    )


async def _http_handler(
    request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    code = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "unprocessable_entity",
        429: "rate_limited",
    }.get(exc.status_code, "http_error")
    return _envelope(code, str(exc.detail), exc.status_code, _request_id(request))


async def _generic_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    logger.error(
        "Unhandled exception for request %s: %s\n%s",
        rid, exc, "".join(traceback.format_exception(exc)),
    )
    return _envelope(
        "internal_error",
        "An unexpected error occurred. See server logs.",
        500, rid,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI app."""
    app.add_exception_handler(PermissionError, _permission_handler)
    app.add_exception_handler(ValueError, _value_handler)
    app.add_exception_handler(KeyError, _key_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _generic_handler)
