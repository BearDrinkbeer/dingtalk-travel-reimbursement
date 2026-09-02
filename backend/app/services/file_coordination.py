from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from threading import Lock


class SessionFilesRetired(RuntimeError):
    """The session file namespace has been retired by logout."""


class FileOperationBusy(RuntimeError):
    """A session file mutation could not start before its bounded deadline."""


class UploadBusy(RuntimeError):
    """Global upload admission could not start before its bounded deadline."""


@dataclass(slots=True)
class _SessionState:
    operation_lock: Lock = field(default_factory=Lock)
    async_gate: asyncio.Lock = field(default_factory=asyncio.Lock)
    retired: bool = False
    references: int = 0


class SessionFileCoordinator:
    """Serialize file mutations per session and globally serialize admission scans."""

    def __init__(
        self,
        *,
        file_wait_seconds: float = 5.0,
        upload_wait_seconds: float = 1.0,
    ) -> None:
        self._states_guard = Lock()
        self._states: dict[str, _SessionState] = {}
        self._upload_guard = Lock()
        self._upload_async_gate = asyncio.Lock()
        self._file_wait_seconds = file_wait_seconds
        self._upload_wait_seconds = upload_wait_seconds

    @property
    def active_session_states(self) -> int:
        """Return the number of live registry entries for diagnostics and tests."""

        with self._states_guard:
            return len(self._states)

    def _reserve_state(self, session_id_hash: str, *, retire: bool = False) -> _SessionState:
        with self._states_guard:
            state = self._states.setdefault(session_id_hash, _SessionState())
            state.references += 1
            if retire:
                # Publish retirement when logout queues, so later operations
                # cannot slip in ahead of it while an older lease is active.
                state.retired = True
            return state

    def _release_reference(self, session_id_hash: str, state: _SessionState) -> None:
        with self._states_guard:
            state.references -= 1
            if state.references < 0:
                raise RuntimeError("session file state reference count became negative")
            if state.references == 0 and self._states.get(session_id_hash) is state:
                self._states.pop(session_id_hash)

    def _release_session(self, session_id_hash: str, state: _SessionState) -> None:
        state.operation_lock.release()
        self._release_reference(session_id_hash, state)

    def _release_async_session(self, session_id_hash: str, state: _SessionState) -> None:
        state.operation_lock.release()
        state.async_gate.release()
        self._release_reference(session_id_hash, state)

    def _validate_acquired_state(
        self,
        session_id_hash: str,
        state: _SessionState,
        *,
        retire: bool,
        allow_retired: bool,
    ) -> _SessionState:
        with self._states_guard:
            if state.retired and not (allow_retired or retire):
                state.operation_lock.release()
                state.references -= 1
                if state.references == 0 and self._states.get(session_id_hash) is state:
                    self._states.pop(session_id_hash)
                raise SessionFilesRetired("session file namespace is retired")
        return state

    def _acquire_session(
        self,
        session_id_hash: str,
        *,
        retire: bool,
        allow_retired: bool,
        blocking: bool,
    ) -> _SessionState | None:
        state = self._reserve_state(session_id_hash, retire=retire)
        try:
            acquired = state.operation_lock.acquire(blocking=blocking)
        except BaseException:
            self._release_reference(session_id_hash, state)
            raise
        if not acquired:
            self._release_reference(session_id_hash, state)
            return None
        return self._validate_acquired_state(
            session_id_hash,
            state,
            retire=retire,
            allow_retired=allow_retired,
        )

    async def _acquire_session_async(
        self,
        session_id_hash: str,
        *,
        retire: bool,
        allow_retired: bool,
        wait_seconds: float | None,
    ) -> _SessionState:
        state = self._reserve_state(session_id_hash, retire=retire)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (wait_seconds or self._file_wait_seconds)
        gate_acquired = False
        operation_acquired = False
        reference_owned = True
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(state.async_gate.acquire(), timeout=remaining)
            gate_acquired = True
            while not operation_acquired:
                operation_acquired = state.operation_lock.acquire(blocking=False)
                if operation_acquired:
                    break
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.sleep(min(0.01, remaining))
            try:
                return self._validate_acquired_state(
                    session_id_hash,
                    state,
                    retire=retire,
                    allow_retired=allow_retired,
                )
            except BaseException:
                operation_acquired = False  # validation releases the threading lock
                reference_owned = False  # validation also releases the registry reference
                raise
        except TimeoutError as exc:
            raise FileOperationBusy("session file operation deadline exceeded") from exc
        finally:
            if not operation_acquired:
                if gate_acquired and state.async_gate.locked():
                    state.async_gate.release()
                if reference_owned:
                    self._release_reference(session_id_hash, state)

    @staticmethod
    async def _acquire_lock_async(
        lock: Lock,
        gate: asyncio.Lock,
        timeout_seconds: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        gate_acquired = False
        try:
            await asyncio.wait_for(gate.acquire(), timeout=timeout_seconds)
            gate_acquired = True
            while not lock.acquire(blocking=False):
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                await asyncio.sleep(min(0.01, remaining))
        except BaseException:
            if gate_acquired and gate.locked():
                gate.release()
            raise

    @contextmanager
    def session_lease(
        self,
        session_id_hash: str,
        *,
        retire: bool = False,
        allow_retired: bool = False,
    ) -> Iterator[None]:
        state = self._acquire_session(
            session_id_hash,
            retire=retire,
            allow_retired=allow_retired,
            blocking=True,
        )
        assert state is not None
        try:
            yield
        finally:
            self._release_session(session_id_hash, state)

    @asynccontextmanager
    async def async_session_lease(
        self,
        session_id_hash: str,
        *,
        retire: bool = False,
        allow_retired: bool = False,
        wait_seconds: float | None = None,
    ) -> AsyncIterator[None]:
        state = await self._acquire_session_async(
            session_id_hash,
            retire=retire,
            allow_retired=allow_retired,
            wait_seconds=wait_seconds,
        )
        try:
            yield
        finally:
            self._release_async_session(session_id_hash, state)

    @asynccontextmanager
    async def async_upload_lease(self, session_id_hash: str) -> AsyncIterator[None]:
        """Hold global admission and the session namespace for one upload."""

        try:
            await self._acquire_lock_async(
                self._upload_guard,
                self._upload_async_gate,
                self._upload_wait_seconds,
            )
        except TimeoutError as exc:
            raise UploadBusy("global upload admission deadline exceeded") from exc
        try:
            try:
                async with self.async_session_lease(session_id_hash):
                    yield
            except FileOperationBusy as exc:
                raise UploadBusy("session upload deadline exceeded") from exc
        finally:
            self._upload_guard.release()
            self._upload_async_gate.release()

    @contextmanager
    def try_upload_cleanup_lease(self) -> Iterator[bool]:
        """Keep orphan-spool cleanup away from an admitted upload request."""

        acquired = self._upload_guard.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                self._upload_guard.release()

    @contextmanager
    def try_cleanup_lease(self, session_id_hash: str) -> Iterator[bool]:
        state = self._acquire_session(
            session_id_hash,
            retire=False,
            allow_retired=True,
            blocking=False,
        )
        if state is None:
            yield False
            return
        try:
            yield True
        finally:
            self._release_session(session_id_hash, state)
