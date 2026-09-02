from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError
from app.schemas.common import success
from app.services.file_coordination import (
    FileOperationBusy,
    SessionFileCoordinator,
    SessionFilesRetired,
)
from app.services.ocr_service import (
    OcrService,
    failed_expense_payload,
    parsed_expense_payload,
)
from app.services.receipt_keywords import load_receipt_keyword_rules
from app.services.sessions import (
    CurrentSession,
    require_csrf,
    require_fresh_active_file_session,
    require_selected_department,
)
from app.services.temp_files import find_session_file

logger = logging.getLogger(__name__)
router = APIRouter(tags=["local OCR"])


class OcrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file_ids: list[str] = Field(alias="fileIds", min_length=1, max_length=1)
    trip_year: int | None = Field(default=None, alias="tripYear", ge=2000, le=2100)


@router.post("/ocr")
async def recognize_receipts(
    body: OcrRequest,
    request: Request,
    current: Annotated[CurrentSession, Depends(require_csrf)],
) -> dict[str, object]:
    require_selected_department(current)
    settings: Settings = request.app.state.settings
    service: OcrService = request.app.state.ocr_service
    coordinator: SessionFileCoordinator = request.app.state.file_coordinator
    session_factory: sessionmaker[Session] = request.app.state.database_session_factory
    items: list[dict[str, object]] = []
    with session_factory() as database:
        keyword_rules = load_receipt_keyword_rules(database)
    for file_id in body.file_ids:
        try:
            async with coordinator.async_session_lease(current.record.session_id_hash):
                require_fresh_active_file_session(
                    session_factory,
                    settings,
                    current.record.session_id_hash,
                )
                stored = find_session_file(
                    settings,
                    current.record.session_id_hash,
                    file_id,
                )
                parsed = await service.recognize_file(
                    stored,
                    reference_year=body.trip_year,
                    keyword_rules=keyword_rules,
                )
                items.append(parsed_expense_payload(file_id, parsed))
        except SessionFilesRetired as exc:
            raise ApiError("UNAUTHORIZED", "登录状态已失效，请重新进入", 401) from exc
        except FileOperationBusy:
            items.append(
                failed_expense_payload(
                    file_id,
                    "FILE_OPERATION_BUSY",
                    "票据文件正在处理中，请稍后重试",
                )
            )
        except ApiError as exc:
            if exc.status_code == 401:
                raise
            items.append(failed_expense_payload(file_id, exc.code, exc.message))
        except Exception as exc:  # Defensive per-file isolation; never expose paths or OCR text.
            logger.error(
                "Unexpected receipt recognition error",
                extra={"exception_type": type(exc).__name__},
            )
            items.append(failed_expense_payload(file_id, "OCR_FAILED", "票据识别失败，请手工填写"))
    return success({"items": items})
