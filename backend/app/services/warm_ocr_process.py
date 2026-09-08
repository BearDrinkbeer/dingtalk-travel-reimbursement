"""A single reusable OCR-only child; all other jobs keep fresh-process isolation."""

from __future__ import annotations

import gc
import logging
import multiprocessing
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

from app.core.logging import configure_logging
from app.core.request_id import bind_request_id, current_request_id, reset_request_id
from app.services.process_jobs import (
    _FreshProcessFailure,
    _shield_process_cleanup,
    _wait_for_descriptor,
    run_in_fresh_process,
)


def _entry(connection: Connection, idle_seconds: float, max_jobs: int) -> None:
    configure_logging("INFO")
    try:
        for _ in range(max_jobs):
            if not connection.poll(idle_seconds):
                return
            function, args, request_id = connection.recv()
            token = bind_request_id(request_id)
            try:
                result = function(*args)
                connection.send(("ok", result))
                failed = isinstance(result, dict) and result.get("ok") is False
                del result, function, args
                gc.collect()
                if failed:
                    return
            except BaseException as exc:
                connection.send(("error", type(exc).__name__))
                return
            finally:
                reset_request_id(token)
    except (EOFError, OSError):
        pass
    finally:
        connection.close()


class WarmOcrProcess:
    """Called under KillableProcessRunner's one-slot admission and deadline."""

    def __init__(
        self,
        *,
        allowed: tuple[Callable[..., Any], ...] | None = None,
        idle_seconds: float = 60,
        max_jobs: int = 50,
    ) -> None:
        if allowed is None:
            from app.ocr.itinerary_worker import recognize_itinerary_worker
            from app.ocr.workers import recognize_document_worker

            allowed = (recognize_document_worker, recognize_itinerary_worker)
        self.allowed = allowed
        self.idle_seconds = idle_seconds
        self.max_jobs = max_jobs
        self._process: multiprocessing.Process | None = None
        self._connection: Connection | None = None
        self._profile: str | None = None
        self._jobs = 0
        self._last_used = 0.0

    async def close(self) -> None:
        process, connection = self._process, self._connection
        self._process = self._connection = None
        self._profile = None
        self._jobs = 0
        if connection is not None:
            connection.close()
        if process is not None:
            await _shield_process_cleanup(process)

    async def __call__(self, function: Callable[..., Any], *args: object, **kwargs: Any) -> Any:
        if function not in self.allowed:
            return await run_in_fresh_process(function, *args, **kwargs)
        # Production OCR entry points share settings/PDF/OS limit argument positions.
        # Rebuild on any profile change; never retain several model configurations.
        profile = repr(args[2:5])
        if self._process is not None and (
            self._process.exitcode is not None
            or profile != self._profile
            or self._jobs >= self.max_jobs
            or time.monotonic() - self._last_used >= self.idle_seconds * 0.9
        ):
            await self.close()
        cold = self._process is None
        started = time.perf_counter()
        if cold:
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(target=_entry, args=(child, self.idle_seconds, self.max_jobs))
            try:
                process.start()
            except BaseException:
                parent.close()
                child.close()
                process.close()
                raise
            child.close()
            self._process, self._connection = process, parent
            self._profile = profile
        try:
            assert self._connection is not None
            self._connection.send((function, args, current_request_id()))
            await _wait_for_descriptor(self._connection.fileno())
            kind, result = self._connection.recv()
            self._jobs += 1
            self._last_used = time.monotonic()
            if kind != "ok":
                raise _FreshProcessFailure("OCR worker failed")
            if isinstance(result, dict) and result.get("ok") is False:
                await self.close()
            logging.getLogger(__name__).info(
                "OCR job completed (cold process)"
                if cold
                else "OCR job completed (reused process)",
                extra={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
            return result
        except BaseException as exc:
            # In particular, cancellation/timeout must reap before admission is released.
            await self.close()
            if isinstance(exc, (EOFError, OSError)):
                raise _FreshProcessFailure("OCR worker exited") from exc
            raise
