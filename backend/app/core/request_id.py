from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

from fastapi import Request

request_id_context: ContextVar[str] = ContextVar("request_id", default="")
_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")


def new_request_id() -> str:
    return uuid4().hex


def request_id_from_header(value: str | None) -> str:
    """Keep a bounded, printable upstream request ID or create a local one.

    Nginx's ``$request_id`` and common tracing identifiers fit this deliberately
    small alphabet. Rejecting control characters and oversized values prevents
    untrusted headers from corrupting structured logs.
    """

    if value and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return new_request_id()


def bind_request_id(request_id: str) -> Token[str]:
    return request_id_context.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    request_id_context.reset(token)


def current_request_id() -> str:
    return request_id_context.get()


def request_id_for(request: Request) -> str:
    """Return the request ID even after the ContextVar has been reset.

    Starlette's catch-all exception handler can run outside user middleware. The
    request state therefore acts as the request-lifetime source of truth while
    the ContextVar remains available to application code and log formatting.
    """

    request_id = getattr(request.state, "request_id", "") or current_request_id()
    if not request_id:
        request_id = new_request_id()
        request.state.request_id = request_id
    return request_id
