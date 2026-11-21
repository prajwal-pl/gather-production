"""One engine per workspace.

Tauri has a single-instance plugin, but the engine is also runnable on its own
(dev, tests, a future CLI). An advisory lock next to the DB makes the rule
executable: two engines would double-poll providers and race on SQLite.

The lock is released by the OS if the process dies, so a crash never leaves a
stale lock that blocks startup.
"""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":  # pragma: no cover - exercised on Windows CI
    import msvcrt
else:
    import fcntl


class EngineLock:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            if os.name == "nt":  # pragma: no cover
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def holder_pid(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def __enter__(self) -> "EngineLock":
        if not self.acquire():
            raise RuntimeError(f"another gather engine holds {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
