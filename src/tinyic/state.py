"""Per-install persistent state under ``~/.tinyic/state.json``.

A tiny key/value store for state that must survive across debates in one
install -- currently only the devil's-advocate rotation counter (FR-4.3, fixes
review defect B8, where a fresh orchestrator always reset the counter to 0 so
the "rotating" DA never rotated).

The location mirrors the event log's ``TINYIC_RUNS_DIR`` override: the
``TINYIC_STATE_DIR`` environment variable redirects the directory (tests point
it at a sandbox so a debate never reads or writes the real home file), else it
is ``~/.tinyic``. Writes are load-modify-write through an atomic ``os.replace``
so an unrelated key is never clobbered and a crash never leaves a torn file.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

STATE_DIR_ENV_VAR = "TINYIC_STATE_DIR"
STATE_FILENAME = "state.json"

# Serializes this process's own load-modify-write cycles. Cross-process
# atomicity rests on ``os.replace`` (atomic on POSIX and Windows).
_LOCK = threading.RLock()


def state_dir() -> Path:
    """Directory holding the per-install state file."""
    override = os.environ.get(STATE_DIR_ENV_VAR)
    base = Path(override) if override else Path.home() / ".tinyic"
    return base.expanduser()


def state_path() -> Path:
    """Full path to the per-install ``state.json``."""
    return state_dir() / STATE_FILENAME


def _read_all(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (FileNotFoundError, ValueError, OSError):
        # A missing file is the common first-run case; a corrupt/partial file
        # must not crash a debate, so treat any unreadable state as empty.
        return {}
    return data if isinstance(data, dict) else {}


def read_counter(name: str) -> int:
    """Current value of counter ``name`` (0 when absent or unreadable)."""
    with _LOCK:
        value = _read_all(state_path()).get(name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def advance_counter(name: str, *, by: int = 1) -> int:
    """Return the current value of ``name`` and persist ``value + by``.

    The returned value is the pre-increment reading -- the caller uses it as the
    slot to act on now, while the persisted successor seeds the next reader.
    """
    with _LOCK:
        path = state_path()
        data = _read_all(path)
        current = data.get(name, 0)
        if not isinstance(current, int) or isinstance(current, bool):
            current = 0
        data[name] = current + by
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, path)
        return current


__all__ = [
    "STATE_DIR_ENV_VAR",
    "STATE_FILENAME",
    "advance_counter",
    "read_counter",
    "state_dir",
    "state_path",
]
