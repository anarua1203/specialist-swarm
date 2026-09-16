"""Terminal trace of Regina's delegations — the visible parallelism is the demo."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, TextIO


class Trace:
    def __init__(self, enabled: bool = True, stream: TextIO = sys.stderr) -> None:
        self.enabled = enabled
        self.stream = stream
        self._lock = threading.Lock()
        self._t0 = time.perf_counter()

    def __call__(self, kind: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        t = time.perf_counter() - self._t0
        if kind == "thinking":
            line = f"[orchestrator]     Regina is planning ({payload['model']})"
        elif kind == "fan_out":
            line = f"[fan-out ×{len(payload['agents'])}]      {', '.join(payload['agents'])}"
        elif kind == "delegate":
            brief = payload["brief"].replace("\n", " ")
            line = f"[delegate →]       {payload['agent']}: \"{brief[:90]}{'…' if len(brief) > 90 else ''}\""
        elif kind == "reply":
            line = f"[reply ←]          {payload['agent']} ({payload['backend']}, {payload['elapsed_s']*1000:.0f} ms, {payload['chars']} chars)"
        elif kind == "synthesize":
            line = "[synthesise]       Regina is writing the answer"
        else:
            line = f"[{kind}] {payload}"
        with self._lock:
            print(f"  {t:6.2f}s  {line}", file=self.stream, flush=True)
