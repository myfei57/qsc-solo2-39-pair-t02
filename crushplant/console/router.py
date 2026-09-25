"""Minimal router: method plus ``{placeholder}`` path segments."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from .response import Response

Handler = Callable[["Request"], Response]


@dataclass(frozen=True)
class Request:
    """A parsed console request."""

    method: str
    path: str
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Route:
    """One registered endpoint."""

    method: str
    pattern: str
    handler: Handler
    summary: str

    def as_dict(self) -> dict[str, str]:
        return {"method": self.method, "path": self.pattern, "summary": self.summary}


class Router:
    """Dispatches requests and reports the endpoint inventory."""

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, pattern: str, handler: Handler, summary: str) -> Route:
        for route in self._routes:
            if route.method == method.upper() and route.pattern == pattern:
                raise ValueError(f"duplicate route: {method} {pattern}")
        route = Route(method.upper(), pattern, handler, summary)
        self._routes.append(route)
        return route

    def get(self, pattern: str, handler: Handler, summary: str) -> Route:
        return self.add("GET", pattern, handler, summary)

    def post(self, pattern: str, handler: Handler, summary: str) -> Route:
        return self.add("POST", pattern, handler, summary)

    def dispatch(self, request: Request) -> Response:
        """Run the handler for this request; handler errors travel as they are."""

        matched_path = False
        for route in self._routes:
            captured = match(route.pattern, request.path)
            if captured is None:
                continue
            matched_path = True
            if route.method != request.method.upper():
                continue
            return route.handler(replace(request, params=captured))
        if matched_path:
            return Response(
                status=405,
                payload={"error": "method-not-allowed", "message": "that endpoint uses a different method"},
            )
        return Response(status=404, payload={"error": "not-found", "message": "unknown endpoint"})

    def routes(self) -> list[Route]:
        return list(self._routes)

    def inventory(self) -> list[dict[str, str]]:
        return [route.as_dict() for route in sorted(self._routes, key=lambda item: (item.pattern, item.method))]

    def paths(self) -> list[str]:
        return sorted({route.pattern for route in self._routes})


def match(pattern: str, path: str) -> dict[str, str] | None:
    """Match one pattern against a path, returning the captured segments."""

    pattern_parts = [part for part in pattern.strip("/").split("/") if part]
    path_parts = [part for part in path.strip("/").split("/") if part]
    if len(pattern_parts) != len(path_parts):
        return None
    captured: dict[str, str] = {}
    for expected, actual in zip(pattern_parts, path_parts):
        if expected.startswith("{") and expected.endswith("}"):
            captured[expected[1:-1]] = actual
            continue
        if expected != actual:
            return None
    return captured
