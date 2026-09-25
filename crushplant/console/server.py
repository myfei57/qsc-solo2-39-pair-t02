"""HTTP bridge that turns console requests into JSON or HTML responses."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..errors import InvalidRequest
from .app import ControlApp
from .response import JSON_CONTENT_TYPE, Response, failure
from .router import Request

SERVER_VERSION = "crushplant/1.0"
MAX_BODY_BYTES = 256 * 1024


class ControlHTTPServer(ThreadingHTTPServer):
    """Threaded server so a slow operator request cannot block the loop."""

    # Read by the standard library server classes, not from this module.
    daemon_threads = True
    allow_reuse_address = True
    server_version = SERVER_VERSION

    def __init__(self, address: tuple[str, int], app: ControlApp, logger: logging.Logger) -> None:
        self.app = app
        self.logger = logger
        super().__init__(address, build_handler(app, logger))


def build_handler(app: ControlApp, logger: logging.Logger) -> type[BaseHTTPRequestHandler]:
    """Create a request handler bound to one application instance."""

    class ControlRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = SERVER_VERSION

        def do_GET(self) -> None:  # noqa: N802 - mandated by the base class
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - mandated by the base class
            self._dispatch("POST")

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            try:
                request = Request(
                    method=method,
                    path=parsed.path,
                    query={key: values[0] for key, values in parse_qs(parsed.query).items()},
                    body=self._read_body(),
                )
                response = app.handle(request)
            except Exception as error:  # noqa: BLE001 - the console always answers
                response = failure(error)
            self._send(response)

        def _read_body(self) -> dict[str, Any]:
            length_header = self.headers.get("Content-Length")
            if not length_header:
                return {}
            try:
                length = int(length_header)
            except ValueError as exc:
                raise InvalidRequest("Content-Length is not a number") from exc
            if length <= 0:
                return {}
            if length > MAX_BODY_BYTES:
                raise InvalidRequest("request body is too large")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InvalidRequest("request body is not valid JSON") from exc
            if not isinstance(document, dict):
                raise InvalidRequest("request body must be a JSON object")
            return document

        def _send(self, response: Response) -> None:
            if isinstance(response.payload, str):
                payload = response.payload.encode("utf-8")
                content_type = response.content_type
            else:
                payload = json.dumps(response.payload, ensure_ascii=False, default=str).encode("utf-8")
                content_type = JSON_CONTENT_TYPE
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover - client vanished
                logger.warning("client disconnected before the response was written")

    return ControlRequestHandler


def build_server(
    app: ControlApp,
    host: str = "127.0.0.1",
    port: int = 8080,
    logger: logging.Logger | None = None,
) -> ControlHTTPServer:
    return ControlHTTPServer((host, port), app, logger or logging.getLogger("crushplant"))


def serve(app: ControlApp, host: str = "127.0.0.1", port: int = 8080) -> None:
    """Run the console until the process is interrupted."""

    server = build_server(app, host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
