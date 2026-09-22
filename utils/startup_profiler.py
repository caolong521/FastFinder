from __future__ import annotations

import time
from pathlib import Path


class StartupProfiler:
    """Very small startup profiler.

    It is intentionally dependency-free and only appends a few lines to
    logs/startup.log.  This makes future slow-start regressions easy to locate,
    including when FastFinder is launched through pythonw.exe / a packaged EXE.
    """

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        self.started = time.perf_counter()
        self.last = self.started
        self.lines: list[str] = []
        self.mark("process start")

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        total_ms = (now - self.started) * 1000.0
        step_ms = (now - self.last) * 1000.0
        self.last = now
        self.lines.append(f"{total_ms:9.1f} ms  (+{step_ms:8.1f})  {name}")

    def flush(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n=== FastFinder startup {timestamp} ===\n")
                for line in self.lines:
                    f.write(line + "\n")
        except Exception:
            pass
