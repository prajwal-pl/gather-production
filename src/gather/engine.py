"""Desktop engine entry point.

    gather-engine --data-dir ~/.local/share/gather --ipc stdio

stdout is the protocol. Everything else — logs, warnings, tracebacks — goes to
stderr and a rotated file under ``<data-dir>/logs``. The Tauri shell spawns
this process, waits for ``engine.ready``, and talks JSON lines.

``python -m gather.engine`` is the same entry point, which is what dev mode
uses instead of the frozen sidecar.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .config import Settings, load_settings
from .ipc import Handlers, StdioServer, build_router
from .lock import EngineLock
from .runtime import build_runtime
from .scheduler import Scheduler
from .secrets import (
    SecretStoreError,
    apply_stored_settings,
    build_secret_store,
    resolve_secrets,
)


class _Redactor(logging.Filter):
    """Replace known secret values in log records. Presence only, never values."""

    def __init__(self) -> None:
        super().__init__()
        self._values: list[str] = []

    def set_secrets(self, values: Sequence[str]) -> None:
        self._values = sorted(
            {value for value in values if value and len(value) >= 8},
            key=len,
            reverse=True,
        )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for value in self._values:
            if value in message:
                message = message.replace(value, "***")
        record.msg = message
        record.args = ()
        return True


def _setup_logging(data_dir: Path, *, level: int = logging.INFO):
    """Logs to stderr and a rotated file. stdout stays protocol-only."""
    logs = Path(data_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gather.engine")
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False

    redactor = _Redactor()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.addFilter(redactor)
    logger.addHandler(stream)

    try:
        file_handler = RotatingFileHandler(
            logs / "engine.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only data dir still deserves a working stderr log.
        pass

    def log(message: str) -> None:
        logger.info(message)

    return log, redactor, logger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gather-engine",
        description="gather desktop engine (stdio JSON-lines)",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        help="database, checkpoints, logs and secrets (default: OS app-data dir)",
    )
    parser.add_argument(
        "--ipc",
        choices=["stdio"],
        default="stdio",
        help="transport (only stdio is supported)",
    )
    parser.add_argument(
        "--secrets",
        choices=["auto", "keychain", "file", "env"],
        default="auto",
        help="secret backend; auto prefers the OS keychain",
    )
    parser.add_argument(
        "--no-scheduler",
        action="store_true",
        help="do not start the background polling thread",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="use offline fixtures and the fixture model (tests/dev)",
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=f"gather-engine {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # JSON lines are UTF-8 on the wire. Windows consoles and some Linux
    # locales default to a legacy codec, which would corrupt names and emoji.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass

    args = _parser().parse_args(list(argv) if argv is not None else None)

    data_dir = Path(args.data_dir).expanduser() if args.data_dir else None
    if data_dir is not None:
        os.environ["GATHER_DATA_DIR"] = str(data_dir)
    # The engine has no inbound webhook edge; ingestion is the poller.
    os.environ.setdefault("GATHER_INGRESS", "stdio")

    settings: Settings = load_settings(data_dir=data_dir)
    level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    log, redactor, logger = _setup_logging(settings.data_dir, level=level)

    lock = EngineLock(settings.data_dir / "engine.lock")
    if not lock.acquire():
        holder = lock.holder_pid() or "unknown"
        logger.error(
            "another gather engine is already running for %s (pid %s)",
            settings.data_dir,
            holder,
        )
        return 2

    runtime = None
    scheduler: Scheduler | None = None
    try:
        store = build_secret_store(settings.data_dir, mode=args.secrets, log=log)
        keys = resolve_secrets(store, on_error=lambda message: log(f"[secrets] {message}"))
        redactor.set_secrets(keys.values())
        settings = apply_stored_settings(settings, store)

        runtime = build_runtime(
            settings,
            log=log,
            force_fixture=args.fixture,
            secrets=keys,
        )

        scheduler = None
        if not args.no_scheduler and settings.scheduler_enabled:
            scheduler = Scheduler(runtime, runtime.events, log=log)

        server_ref: dict[str, StdioServer] = {}

        def request_shutdown() -> None:
            server = server_ref.get("server")
            if server is not None:
                server.stop()

        handlers = Handlers(
            runtime,
            events=runtime.events,
            secrets=store,
            keys=keys,
            scheduler=scheduler,
            shutdown=request_shutdown,
            log=log,
        )
        router = build_router(handlers)
        server = StdioServer(router)
        server_ref["server"] = server

        # Only now is there somewhere to send protocol events.
        runtime.events.set_sink(server.emit)

        def _signal(_signum: int, _frame: Any) -> None:
            server.stop()

        signal.signal(signal.SIGTERM, _signal)
        signal.signal(signal.SIGINT, _signal)

        runtime.events.emit(
            "engine.ready",
            {
                "version": __version__,
                "workspace_id": runtime.org_id,
                "data_dir": str(settings.data_dir),
                "fixture_mode": not bool(getattr(runtime.llm, "specs", ())),
                "models": [spec.label for spec in getattr(runtime.llm, "specs", ())],
                "scheduler": scheduler is not None,
                "pid": os.getpid(),
            },
        )
        log(
            f"[engine] ready workspace={runtime.org_id} "
            f"data_dir={settings.data_dir} pid={os.getpid()}"
        )

        if scheduler is not None:
            scheduler.start()

        server.serve_forever()
        return 0
    except SecretStoreError as exc:
        logger.error("secret store error: %s", exc)
        return 3
    except Exception:  # noqa: BLE001 - startup failures must be legible
        logger.error("engine failed to start:\n%s", traceback.format_exc())
        return 1
    finally:
        if scheduler is not None:
            scheduler.stop(timeout=2.0)
        if runtime is not None:
            try:
                runtime.close()
            except Exception:  # noqa: BLE001
                logger.debug("runtime close failed", exc_info=True)
        lock.release()
        log("[engine] stopped")


if __name__ == "__main__":
    sys.exit(main())
