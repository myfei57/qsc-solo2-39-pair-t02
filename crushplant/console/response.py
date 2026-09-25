"""Response objects and the exception to HTTP mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import payload_for, status_for

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
HTML_CONTENT_TYPE = "text/html; charset=utf-8"


@dataclass(frozen=True)
class Response:
    """A payload together with its status code and content type."""

    status: int
    payload: Any = field(default_factory=dict)
    content_type: str = JSON_CONTENT_TYPE


def ok(payload: Any = None) -> Response:
    return Response(status=200, payload={} if payload is None else payload)


def created(payload: Any = None) -> Response:
    return Response(status=201, payload={} if payload is None else payload)


def html(document: str) -> Response:
    return Response(status=200, payload=document, content_type=HTML_CONTENT_TYPE)


def failure(error: BaseException) -> Response:
    return Response(status=status_for(error), payload=payload_for(error))
