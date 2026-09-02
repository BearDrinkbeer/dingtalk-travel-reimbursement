from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.common import success
from app.services.file_coordination import (
    FileOperationBusy,
    SessionFileCoordinator,
    SessionFilesRetired,
    UploadBusy,
)
from app.services.multipart_uploads import parse_upload_files
from app.services.process_jobs import KillableProcessRunner
from app.services.sessions import (
    CurrentSession,
    require_csrf,
    require_fresh_active_file_session,
    require_selected_department,
)
from app.services.temp_files import (
    close_upload_file,
    delete_session_file,
    new_upload_budget,
    store_upload,
)

router = APIRouter(tags=["temporary files"])


def _require_multipart(request: Request) -> None:
    content_type = request.headers.get("Content-Type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError("MALFORMED_MULTIPART", "上传请求必须使用 multipart/form-data", 400)


@router.post("/files/upload")
async def upload_files(
    request: Request,
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> dict[str, object]:
    require_selected_department(current)
    settings: Settings = request.app.state.settings
    _require_multipart(request)
    coordinator: SessionFileCoordinator = request.app.state.file_coordinator
    process_runner: KillableProcessRunner = request.app.state.process_runner
    session_factory: sessionmaker[Session] = request.app.state.database_session_factory
    stored_file = None
    uploads = []
    try:
        async with coordinator.async_upload_lease(current.record.session_id_hash):
            require_fresh_active_file_session(
                session_factory,
                settings,
                current.record.session_id_hash,
            )
            budget = new_upload_budget(
                settings,
                current.record.session_id_hash,
                1,
            )
            uploads = await parse_upload_files(
                request.headers,
                request.stream(),
                settings,
                budget,
            )
            stored_file = await store_upload(
                uploads[0],
                settings,
                current.record.session_id_hash,
                None,
                process_runner,
            )
    except SessionFilesRetired as exc:
        raise ApiError("UNAUTHORIZED", "登录状态已失效，请重新进入", 401) from exc
    except UploadBusy as exc:
        raise ApiError("UPLOAD_BUSY", "上传服务繁忙，请稍后重试", 429) from exc
    finally:
        for upload in uploads:
            close_upload_file(upload)
    assert stored_file is not None
    return success(
        {
            "files": [
                {
                    "id": stored_file.temp_id,
                    "name": stored_file.original_name,
                    "status": "uploaded",
                }
            ]
        }
    )


@router.delete("/files/{temp_id}")
async def delete_file(
    temp_id: str,
    request: Request,
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> dict[str, object]:
    require_selected_department(current)
    settings: Settings = request.app.state.settings
    coordinator: SessionFileCoordinator = request.app.state.file_coordinator
    session_factory: sessionmaker[Session] = request.app.state.database_session_factory
    try:
        async with coordinator.async_session_lease(current.record.session_id_hash):
            require_fresh_active_file_session(
                session_factory,
                settings,
                current.record.session_id_hash,
            )
            delete_session_file(settings, current.record.session_id_hash, temp_id)
    except SessionFilesRetired as exc:
        raise ApiError("UNAUTHORIZED", "登录状态已失效，请重新进入", 401) from exc
    except FileOperationBusy as exc:
        raise ApiError("FILE_OPERATION_BUSY", "票据文件正在处理中，请稍后重试", 429) from exc
    return success({})
