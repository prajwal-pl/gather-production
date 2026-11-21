"""Standard-input / standard-output JSON-lines server.

Reader and workers are daemon threads so a hung provider call can never keep
the OS process alive after the shell closes the pipe. Writes are serialized;
one line is one event or response.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
from typing import Any, TextIO

from .protocol import ProtocolError, RpcError, encode, fail, ok, parse_request
from .router import Router

_SENTINEL = object()


class StdioServer:
    def __init__(
        self,
        router: Router,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        fast_workers: int = 4,
        slow_workers: int = 2,
        queue_size: int = 100,
    ) -> None:
        self.router = router
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._fast: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._slow: queue.Queue[Any] = queue.Queue(maxsize=max(1, slow_workers * 2))
        self._fast_workers: list[threading.Thread] = []
        self._slow_workers: list[threading.Thread] = []

        for index in range(max(1, fast_workers)):
            self._fast_workers.append(self._spawn(f"gather-ipc-fast-{index}", self._fast))
        for index in range(max(1, slow_workers)):
            self._slow_workers.append(self._spawn(f"gather-ipc-slow-{index}", self._slow))

    # -- writes ------------------------------------------------------------ #

    def send(self, payload: dict[str, Any]) -> None:
        line = encode(payload) + "\n"
        with self._write_lock:
            try:
                self.stdout.write(line)
                self.stdout.flush()
            except (BrokenPipeError, ValueError):
                # The shell went away. Stop; the process will exit shortly.
                self.stop()

    def emit(self, record: dict[str, Any]) -> None:
        self.send(record)

    # -- lifecycle --------------------------------------------------------- #

    def stop(self) -> None:
        self._stop.set()

    def serve_forever(self) -> None:
        self._reader = threading.Thread(
            target=self._read_loop, name="gather-ipc-reader", daemon=True
        )
        self._reader.start()
        try:
            self._stop.wait()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        # Sentinels go to the back of each FIFO queue, so requests already
        # accepted are finished before a worker exits. Active slow runs get a
        # short grace period; the shell kills the process after that.
        for _ in self._fast_workers:
            try:
                self._fast.put_nowait(_SENTINEL)
            except queue.Full:
                pass
        for _ in self._slow_workers:
            try:
                self._slow.put_nowait(_SENTINEL)
            except queue.Full:
                pass

        deadline = time.monotonic() + 2.5
        for worker in self._workers:
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)

    # -- reading ----------------------------------------------------------- #

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                line = self.stdin.readline()
            except (OSError, ValueError):
                break
            if line == "":
                # Parent closed stdin: normal shutdown.
                break
            self._handle_line(line)
        self.stop()

    def _handle_line(self, line: str) -> None:
        try:
            request = parse_request(line)
        except ProtocolError as exc:
            self.send(fail(None, "bad_request", str(exc)))
            return

        route = self.router.route(request.method)
        if route is None:
            self.send(
                fail(
                    request.id,
                    "method_not_found",
                    f"unknown method {request.method!r}",
                )
            )
            return

        target = self._slow if route.slow else self._fast
        try:
            target.put_nowait((request, route))
        except queue.Full:
            self.send(
                fail(
                    request.id,
                    "busy",
                    "too many in-flight requests; retry shortly",
                )
            )

    # -- workers ----------------------------------------------------------- #

    def _spawn(self, name: str, target_queue: queue.Queue[Any]) -> threading.Thread:
        worker = threading.Thread(
            target=self._work,
            args=(target_queue,),
            name=name,
            daemon=True,
        )
        worker.start()
        self._workers.append(worker)
        return worker

    def _work(self, target_queue: queue.Queue[Any]) -> None:
        while True:
            item = target_queue.get()
            try:
                if item is _SENTINEL:
                    return
                request, route = item
                self._run(request, route)
            finally:
                target_queue.task_done()

    def _run(self, request, route) -> None:
        try:
            result = route.handler(request.params)
        except RpcError as exc:
            self.send(
                fail(request.id, exc.code, exc.message, details=exc.details)
            )
        except Exception as exc:  # noqa: BLE001 - a handler bug must not kill the engine
            traceback.print_exc(file=self.stderr)
            self.send(
                fail(
                    request.id,
                    "internal_error",
                    f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            self.send(ok(request.id, result))
