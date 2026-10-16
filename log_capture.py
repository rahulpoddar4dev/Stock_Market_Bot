# log_capture.py - Dual-tee logging for terminal and live dashboard log stream

import sys
import threading
from pathlib import Path

LOG_FILE = Path("bot_stdout.log")
_LOCK = threading.Lock()


class _TeeStream:
    def __init__(self, original_stream, log_file: Path):
        self._original = original_stream
        self._log_file = log_file

    def write(self, text: str):
        try:
            self._original.write(text)
            self._original.flush()
        except Exception:
            pass

        if not text:
            return

        with _LOCK:
            try:
                with open(self._log_file, "a", encoding="utf-8", errors="replace") as f:
                    f.write(text)
            except Exception:
                pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self._original, "isatty", lambda: False)()

    def fileno(self):
        return getattr(self._original, "fileno", lambda: 1)()


def init_log_capture():
    global LOG_FILE
    if not isinstance(sys.stdout, _TeeStream):
        sys.stdout = _TeeStream(sys.stdout, LOG_FILE)
    if not isinstance(sys.stderr, _TeeStream):
        sys.stderr = _TeeStream(sys.stderr, LOG_FILE)


def read_live_logs(max_bytes: int = 3 * 1024 * 1024) -> tuple[str, str, str]:
    """
    Returns (date_str, size_str, log_content)
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

    if not LOG_FILE.exists():
        return today_str, "0.0 KB", f"[{today_str}] Live log stream ready. Waiting for bot engine output...\n"

    try:
        size_bytes = LOG_FILE.stat().st_size
        size_kb = f"{round(size_bytes / 1024, 1)} KB"

        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            if size_bytes > max_bytes:
                f.seek(size_bytes - max_bytes)
                f.readline() # discard partial line
            content = f.read()
        return today_str, size_kb, content
    except Exception as e:
        return today_str, "0.0 KB", f"Error reading log file: {e}\n"
