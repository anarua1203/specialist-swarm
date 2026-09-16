"""
Regina — web UI for the demo.

A single-page UI over the same Regina orchestrator the CLIs use. Delegation
events stream to the browser as Server-Sent Events while the sub-agents work,
so the audience sees the fan-out, then the synthesised answer.

Standard library only; no extra dependencies. Single-user by design.
Set REGINA_WEB_PASSWORD to require a password (HTTP Basic auth, any username)
before exposing it beyond localhost. See DEPLOY.md.

Endpoints:
    GET  /healthz          200 "ok", never password-protected (load balancer checks)
    GET  /                 the page (web/index.html)
    GET  /api/info         {name, user, mode, today, roster, suggestions}
    GET  /api/ask?q=...    SSE: trace events, then {"kind": "answer", "text": ...}
    GET  /api/briefing     SSE: same, for the morning briefing
    POST /api/reset        forget the conversation

Usage:
    python regina_web.py                 # auto: live if credentials exist, else mock
    python regina_web.py --mock          # offline demo
    python regina_web.py --live --port 8080
    PORT=8080 REGINA_WEB_HOST=0.0.0.0 python regina_web.py   # container / PaaS style
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import queue
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Generator
from urllib.parse import parse_qs, urlparse

from regina import Regina, config
from regina_chat import SUGGESTIONS

INDEX_HTML = config.ROOT / "web" / "index.html"
_DONE = object()  # queue sentinel: the request has finished


class ReginaSession:
    """Owns the shared Regina instance and turns one request into an event stream."""

    def __init__(self, regina: Regina) -> None:
        self.regina = regina
        self._lock = threading.Lock()  # one request at a time; events must not interleave

    def info(self) -> dict[str, Any]:
        return {
            "name": self.regina.name,
            "user": config.USER_NAME,
            "mode": self.regina.mode,
            "model": self.regina.model if self.regina.mode == "live" else None,
            "today": config.today().strftime("%A, %B %d, %Y"),
            "roster": [
                {"key": a.key, "name": a.name, "description": a.description}
                for a in self.regina.roster.values()
            ],
            "suggestions": SUGGESTIONS,
        }

    def stream(self, run: Callable[[Regina], str]) -> Generator[dict[str, Any], None, None]:
        """Run `run(regina)` in a worker thread and yield its events as they happen.

        Regina fires on_event from ThreadPoolExecutor workers, so events go
        through a thread-safe queue rather than straight to the socket.
        """
        with self._lock:
            events: queue.Queue = queue.Queue()
            t0 = time.perf_counter()

            def on_event(kind: str, payload: dict[str, Any]) -> None:
                events.put({"kind": kind, "t": round(time.perf_counter() - t0, 3), **payload})

            def worker() -> None:
                try:
                    text = run(self.regina)
                    events.put({"kind": "answer", "t": round(time.perf_counter() - t0, 3), "text": text})
                except Exception as exc:  # surface API/credential errors in the UI
                    events.put({"kind": "error", "message": f"{type(exc).__name__}: {exc}"})
                finally:
                    events.put(_DONE)

            self.regina.on_event = on_event
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            try:
                while (event := events.get()) is not _DONE:
                    yield event
            finally:
                # If the browser left mid-request, keep the lock until Regina is actually
                # done, so a late reply can't leak into the next request's trace.
                thread.join()

    def reset(self) -> None:
        with self._lock:
            self.regina.reset()


def make_handler(session: ReginaSession, password: str | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._send(HTTPStatus.OK, b"ok", "text/plain")
            elif not self._authorized():
                self._send_unauthorized()
            elif url.path == "/":
                self._send(HTTPStatus.OK, INDEX_HTML.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/info":
                self._send_json(session.info())
            elif url.path == "/api/ask":
                question = parse_qs(url.query).get("q", [""])[0].strip()
                if not question:
                    self._send_json({"error": "Missing ?q= question"}, HTTPStatus.BAD_REQUEST)
                    return
                self._send_stream(session.stream(lambda r: r.ask(question)))
            elif url.path == "/api/briefing":
                self._send_stream(session.stream(lambda r: r.briefing()))
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._send_unauthorized()
            elif urlparse(self.path).path == "/api/reset":
                session.reset()
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        # ---- auth --------------------------------------------------------------

        def _authorized(self) -> bool:
            """No password configured = open. Otherwise Basic auth; the username is ignored."""
            if not password:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
            except ValueError:
                return False
            return hmac.compare_digest(given.encode(), password.encode())

        def _send_unauthorized(self) -> None:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="Regina", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        # ---- response helpers ------------------------------------------------

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _send_stream(self, events: Generator[dict[str, Any], None, None]) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # stop nginx-style proxies buffering the stream
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for event in events:
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser went away mid-answer; nothing to do
            finally:
                events.close()  # release the session lock even if the client left early
            self.close_connection = True

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # keep the terminal clean for the demo

    return Handler


def build_server(
    regina: Regina, host: str = "127.0.0.1", port: int = 8000, password: str | None = None
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(ReginaSession(regina), password))


def main() -> None:
    parser = argparse.ArgumentParser(description="Regina web UI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="offline mode, no API calls")
    group.add_argument("--live", action="store_true", help="Claude API mode")
    parser.add_argument("--host", default=os.environ.get("REGINA_WEB_HOST", "127.0.0.1"),
                        help="bind address; 0.0.0.0 inside containers (env: REGINA_WEB_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")),
                        help="listen port (env: PORT, as set by most PaaS hosts)")
    args = parser.parse_args()

    mode = "mock" if args.mock else "live" if args.live else None
    regina = Regina(mode=mode)
    password = os.environ.get("REGINA_WEB_PASSWORD") or None
    server = build_server(regina, args.host, args.port, password)
    print(f"👑 {regina.name} web UI ({regina.mode} mode, "
          f"{'password required' if password else 'no password'}) → http://{args.host}:{args.port}", flush=True)
    if not password and args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("⚠  Listening beyond localhost without REGINA_WEB_PASSWORD: anyone who can reach this "
              "port can use it" + (" and spend your API credits." if regina.mode == "live" else "."), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye 👋")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
