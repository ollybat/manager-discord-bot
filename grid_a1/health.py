"""Optional read-only status endpoint for hosted deployments.

A Discord bot normally only makes outbound gateway connections, so a host that attaches a
public domain has nothing to answer with and its edge returns 502. This module serves a
tiny stdlib HTTP document on the port the host provides (Railway injects PORT), which lets
uptime monitors and platform health checks see a 200. It reports only coarse runtime facts:
no tokens, IDs, guild names, ticket contents, or member data ever leave the process, and a
failure to bind is logged and ignored so the bot itself keeps running.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

log = logging.getLogger("grid-a1-health")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
STATUS_PATHS = ("/", "/health", "/healthz", "/status")
DISABLED_VALUES = {"1", "true", "yes", "on"}


def resolve_port(env: Mapping[str, str] | None = None) -> int:
    """Read the serving port from HEALTH_PORT, then the host PORT, then the fallback."""
    source = os.environ if env is None else env
    for name in ("HEALTH_PORT", "PORT"):
        raw = str(source.get(name) or "").strip()
        if not raw:
            continue
        try:
            port = int(raw)
        except ValueError:
            log.warning("Ignoring %s=%r because it is not a port number", name, raw)
            continue
        if 0 <= port <= 65535:
            return port
        log.warning("Ignoring %s=%r because it is outside 0-65535", name, raw)
    return DEFAULT_PORT


def health_enabled(env: Mapping[str, str] | None = None) -> bool:
    """The status endpoint is on by default; HEALTH_DISABLED=1 turns it off."""
    source = os.environ if env is None else env
    return str(source.get("HEALTH_DISABLED") or "").strip().lower() not in DISABLED_VALUES


class HealthServer:
    """Serve one small JSON status document on the host HTTP port."""

    def __init__(
        self,
        report: Callable[[], dict[str, Any]],
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        self.report = report
        self.host = host or str(os.getenv("HEALTH_HOST") or DEFAULT_HOST)
        self.port = resolve_port() if port is None else port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        """True while the background listener is serving requests."""
        return self._server is not None

    def start(self) -> bool:
        """Bind and serve in a daemon thread; return False when the endpoint stays off."""
        if not health_enabled():
            log.info("Status endpoint disabled by HEALTH_DISABLED")
            return False
        if self._server is not None:
            return True
        try:
            server = ThreadingHTTPServer((self.host, self.port), self._handler())
            server.daemon_threads = True
        except OSError as error:
            log.warning(
                "Status endpoint could not bind %s:%s (%s); the bot keeps running",
                self.host,
                self.port,
                error,
            )
            return False
        self.port = int(server.server_address[1])
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="grid-a1-health",
            daemon=True,
        )
        self._thread.start()
        log.info("Status endpoint listening on http://%s:%s/health", self.host, self.port)
        return True

    def stop(self) -> None:
        """Shut the listener down without touching the Discord connection."""
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def document(self) -> tuple[int, dict[str, Any]]:
        """Build the status document, degrading to 503 when the report fails."""
        try:
            report = dict(self.report())
        except Exception:
            log.exception("Status report failed")
            return 503, {"status": "degraded", "detail": "status report unavailable"}
        report.setdefault("status", "ok")
        return 200, report

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class StatusHandler(BaseHTTPRequestHandler):
            server_version = "grid-a1-health"
            sys_version = ""
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # stdlib naming conventions
                self._send(include_body=True)

            def do_HEAD(self) -> None:  # stdlib naming conventions
                self._send(include_body=False)

            def do_POST(self) -> None:  # stdlib naming conventions
                self._send_error(405, "method not allowed")

            do_PUT = do_POST
            do_DELETE = do_POST
            do_PATCH = do_POST

            def _send(self, include_body: bool) -> None:
                path = self.path.split("?", 1)[0].rstrip("/") or "/"
                if path not in STATUS_PATHS:
                    self._send_error(404, "not found")
                    return
                status, document = server.document()
                self._write(status, document, include_body=include_body)

            def _send_error(self, status: int, detail: str) -> None:
                self._write(status, {"status": "error", "detail": detail})

            def _write(self, status: int, document: dict[str, Any], include_body: bool = True) -> None:
                body = json.dumps(document, sort_keys=True).encode("utf-8") + b"\n"
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if include_body:
                    self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                log.debug("status request from %s: %s", self.address_string(), format % args)

        return StatusHandler
