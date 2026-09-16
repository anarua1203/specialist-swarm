"""
Regina — web UI for the demo.

A single-page UI over the same Regina orchestrator the CLIs use. Delegation
events stream to the browser as Server-Sent Events while the sub-agents work,
so the audience sees the fan-out, then the synthesised answer.

Standard library only; no extra dependencies. Single-user by design.

Endpoints:
    GET  /                 the page (web/index.html)
    GET  /api/info         {name, user, mode, today, roster, suggestions}
    GET  /api/ask?q=...    SSE: trace events, then {"kind": "answer", "text": ...}
    GET  /api/briefing     SSE: same, for the morning briefing
    POST /api/reset        forget the conversation

Usage:
    python regina_web.py                 # auto: live if credentials exist, else mock
    python regina_web.py --mock          # offline demo
    python regina_web.py --live --port 8080
"""

from __future__ import annotations

import argparse
import json
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


def make_handler(session: ReginaSession) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/":
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
            if urlparse(self.path).path == "/api/reset":
                session.reset()
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

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


def build_server(regina: Regina, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(ReginaSession(regina)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Regina web UI")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--mock", action="store_true", help="offline mode, no API calls")
    group.add_argument("--live", action="store_true", help="Claude API mode")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    mode = "mock" if args.mock else "live" if args.live else None
    regina = Regina(mode=mode)
    server = build_server(regina, args.host, args.port)
    print(f"👑 {regina.name} web UI ({regina.mode} mode) → http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye 👋")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
