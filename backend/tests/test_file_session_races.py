from __future__ import annotations

import asyncio
import io
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy import select

from app.models.session import UserSession
from app.services.temp_files import delete_session_files
from tests.conftest import mock_login


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(output, format="PNG")
    return output.getvalue()


def upload(client: TestClient, csrf: str):
    return client.post(
        "/api/files/upload",
        headers={"X-CSRF-Token": csrf},
        files=[("files[]", ("receipt.png", png_bytes(), "image/png"))],
    )


def session_directories(client: TestClient) -> list[Path]:
    root = client.app.state.settings.temp_dir
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.is_dir() and len(path.name) == 64]


def current_session_hash(client: TestClient) -> str:
    with client.app.state.database_session_factory() as database:
        session_id_hash = database.scalar(select(UserSession.session_id_hash))
    assert session_id_hash is not None
    return str(session_id_hash)


def retire_and_delete_session(client: TestClient, session_id_hash: str) -> None:
    coordinator = client.app.state.file_coordinator
    settings = client.app.state.settings
    with coordinator.session_lease(session_id_hash, retire=True):
        with client.app.state.database_session_factory() as database:
            record = database.get(UserSession, session_id_hash)
            if record is not None:
                database.delete(record)
                database.commit()
        delete_session_files(settings, session_id_hash)


@pytest.mark.parametrize("operation", ["upload", "ocr", "delete"])
def test_file_operation_revalidates_after_logout_wins_lease(
    client_factory,
    monkeypatch,
    operation: str,
) -> None:
    client = client_factory(auth_mock_enabled=True, ocr_fake_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    session_id_hash = current_session_hash(client)
    temp_id: str | None = None
    if operation != "upload":
        setup = upload(client, csrf)
        assert setup.status_code == 200
        temp_id = str(setup.json()["data"]["files"][0]["id"])

    entered = Event()
    resume = Event()
    if operation == "ocr":
        import app.api.ocr as target_module
    else:
        import app.api.files as target_module
    original_check = target_module.require_selected_department

    def pause_after_initial_auth(current) -> None:
        original_check(current)
        entered.set()
        assert resume.wait(timeout=5), "test did not resume the paused file request"

    monkeypatch.setattr(target_module, "require_selected_department", pause_after_initial_auth)

    response_holder: dict[str, Response] = {}

    def run_operation() -> None:
        if operation == "upload":
            response_holder["response"] = upload(client, csrf)
        elif operation == "ocr":
            response_holder["response"] = client.post(
                "/api/ocr",
                headers={"X-CSRF-Token": csrf},
                json={"fileIds": [temp_id]},
            )
        else:
            response_holder["response"] = client.delete(
                f"/api/files/{temp_id}",
                headers={"X-CSRF-Token": csrf},
            )

    worker = Thread(target=run_operation)
    worker.start()
    assert entered.wait(timeout=5)

    retire_and_delete_session(client, session_id_hash)
    assert session_directories(client) == []

    resume.set()
    worker.join(timeout=5)
    assert not worker.is_alive(), "file request deadlocked after logout"
    response = response_holder["response"]
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert session_directories(client) == []


def test_logout_waits_for_admitted_upload_then_removes_its_file(
    client_factory,
    monkeypatch,
) -> None:
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    session_id_hash = current_session_hash(client)
    import app.api.files as files_api

    entered_store = Event()
    resume_store = Event()
    logout_done = Event()
    original_store = files_api.store_upload

    async def paused_store(*args, **kwargs):
        entered_store.set()
        resumed = await asyncio.to_thread(resume_store.wait, 5)
        assert resumed, "test did not resume the admitted upload"
        return await original_store(*args, **kwargs)

    monkeypatch.setattr(files_api, "store_upload", paused_store)
    responses: dict[str, Response] = {}

    def run_upload() -> None:
        responses["upload"] = upload(client, csrf)

    def run_logout() -> None:
        retire_and_delete_session(client, session_id_hash)
        logout_done.set()

    upload_thread = Thread(target=run_upload)
    upload_thread.start()
    assert entered_store.wait(timeout=5)

    logout_thread = Thread(target=run_logout)
    logout_thread.start()
    assert not logout_done.wait(timeout=0.1), "logout bypassed the active file lease"

    resume_store.set()
    upload_thread.join(timeout=5)
    logout_thread.join(timeout=5)
    assert not upload_thread.is_alive() and not logout_thread.is_alive()
    assert responses["upload"].status_code == 200
    assert logout_done.is_set()
    assert session_directories(client) == []
