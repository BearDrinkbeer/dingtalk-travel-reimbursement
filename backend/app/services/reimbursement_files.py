from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import UploadFile

from app.core.config import Settings
from app.core.errors import ApiError
from app.domain.expenses import ExpenseTotals, calculate_expense_totals
from app.domain.subsidy import SubsidyCalculation, calculate_subsidy
from app.models.reimbursement import (
    ReimbursementAttachmentKind,
    ReimbursementDraft,
    ReimbursementDraftFile,
    ReimbursementDraftFileRole,
    ReimbursementDraftFileStatus,
    ReimbursementOcrStatus,
    ReimbursementUpload,
    utc_now,
)
from app.schemas.reimbursements import ReimbursementDraftInput
from app.services.application_settings import get_expense_settings
from app.services.excel_generator import (
    ResolvedProject,
    WorkbookResult,
    generate_expense_workbook,
)
from app.services.multipart_uploads import prepare_spool_directory
from app.services.oa_template_profiles import require_submission_ready_catalog
from app.services.ocr_service import (
    OcrService,
    failed_expense_payload,
    failed_itinerary_payload,
    parsed_expense_payload,
)
from app.services.process_jobs import KillableProcessRunner
from app.services.receipt_keywords import load_receipt_keyword_rules
from app.services.reimbursement_drafts import (
    DraftActor,
    apply_ocr_evidence,
    bump_owned_draft_revision,
    complete_expense_items,
    detach_draft_file_from_input,
    require_complete_draft_input,
    require_owned_draft,
    validate_and_calculate_input,
)
from app.services.reimbursement_quota import (
    DraftFileOwner,
    QuotaReservation,
    ReimbursementQuotaCoordinator,
    ReimbursementQuotaExceeded,
    ReimbursementReservationConflict,
)
from app.services.reimbursement_staging import (
    ReimbursementStaging,
    StagedObject,
    StagingIntegrityError,
    StagingLayoutError,
    StagingLimitExceeded,
    StagingObjectExists,
    StagingObjectNotFound,
)
from app.services.temp_files import (
    StoredFile,
    validate_new_file,
    validate_upload_name,
    validate_upload_type,
)

logger = logging.getLogger(__name__)

_OCR_RUNNING_MARKER_KEY = "operationId"
_OCR_STALE_GRACE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class DraftFileSnapshot:
    id: str
    draft_id: str
    original_name: str
    extension: str
    media_type: str
    processing_role: str
    sort_order: int
    file_status: str
    storage_key: str
    size_bytes: int
    sha256: str
    ocr_status: str
    ocr_result_json: str | None
    attachment_kind: str = "other"


@dataclass(frozen=True, slots=True)
class DraftFileMutationResult:
    revision: int
    file: DraftFileSnapshot


@dataclass(frozen=True, slots=True)
class DraftFileDeletion:
    file_id: str
    draft_id: str
    storage_key: str
    size_bytes: int
    sha256: str
    revision: int
    completed: bool = False


@dataclass(frozen=True, slots=True)
class WorkbookPreviewSnapshot:
    canonical_input_json: str
    input: ReimbursementDraftInput
    employee_name: str
    department_name: str
    project: ResolvedProject
    subsidy: SubsidyCalculation | None
    totals: ExpenseTotals


def serialize_draft_file(file: DraftFileSnapshot) -> dict[str, object]:
    ocr_result: object | None = None
    if (
        file.ocr_status
        in {
            ReimbursementOcrStatus.COMPLETE.value,
            ReimbursementOcrStatus.FAILED.value,
        }
        and file.ocr_result_json is not None
    ):
        try:
            ocr_result = json.loads(file.ocr_result_json)
        except (TypeError, ValueError):
            logger.error(
                "Stored reimbursement OCR result is invalid",
                extra={"draft_file_id": file.id},
            )
    return {
        "id": file.id,
        "name": file.original_name,
        "role": file.processing_role,
        "attachmentKind": file.attachment_kind,
        "sortOrder": file.sort_order,
        "status": file.file_status,
        "mediaType": file.media_type,
        "sizeBytes": file.size_bytes,
        "ocrStatus": file.ocr_status,
        "ocrResult": ocr_result,
    }


def list_draft_files(
    database: Session,
    *,
    draft_id: str,
    actor: DraftActor,
) -> tuple[int, list[DraftFileSnapshot]]:
    draft = require_owned_draft(database, draft_id=draft_id, actor=actor)
    files = database.scalars(
        select(ReimbursementDraftFile)
        .where(
            ReimbursementDraftFile.draft_id == draft.id,
            ReimbursementDraftFile.file_status.in_(
                {
                    ReimbursementDraftFileStatus.ACTIVE.value,
                    ReimbursementDraftFileStatus.DELETING.value,
                }
            ),
        )
        .order_by(ReimbursementDraftFile.sort_order, ReimbursementDraftFile.id)
    ).all()
    return draft.revision, [_snapshot(item) for item in files]


def read_draft_file_content(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    staging: ReimbursementStaging,
) -> tuple[DraftFileSnapshot, bytes]:
    draft = require_owned_draft(database, draft_id=draft_id, actor=actor)
    if draft.expires_at <= utc_now() or draft.status == "EXPIRED":
        raise ApiError("REIMBURSEMENT_DRAFT_EXPIRED", "报销资料已过期，请重新上传", 409)
    file = _snapshot(_require_active_file(database, draft_id=draft.id, file_id=file_id))
    if file.media_type not in {"application/pdf", "image/png", "image/jpeg"}:
        raise ApiError("UNSUPPORTED_FILE_TYPE", "此文件类型不能预览", 415)
    try:
        content = staging.read_bytes(
            file.storage_key, expected_size=file.size_bytes, expected_sha256=file.sha256
        )
    except (StagingIntegrityError, StagingObjectNotFound, StagingLayoutError, OSError):
        raise ApiError(
            "REIMBURSEMENT_DRAFT_FILE_CHANGED", "附件已丢失或内容发生变化，请重新上传", 409
        ) from None
    return file, content


async def persist_draft_upload(
    *,
    upload: UploadFile,
    actor: DraftActor,
    draft_id: str,
    expected_revision: int,
    processing_role: ReimbursementDraftFileRole,
    settings: Settings,
    session_factory: sessionmaker[Session],
    quota: ReimbursementQuotaCoordinator,
    staging: ReimbursementStaging,
    process_runner: KillableProcessRunner,
    attachment_kind: ReimbursementAttachmentKind = ReimbursementAttachmentKind.OTHER,
) -> DraftFileMutationResult:
    if processing_role is ReimbursementDraftFileRole.EXPENSE_SOURCE and attachment_kind != "other":
        raise ApiError("VALIDATION_ERROR", "费用来源文件不能指定证明材料用途", 422)
    size_bytes, first_bytes, worker_path = _inspect_upload_spool(upload, settings=settings)
    upload_type = validate_upload_type(upload.filename, first_bytes)
    await validate_new_file(
        worker_path,
        upload_type.extension,
        settings,
        process_runner,
        supporting_pdf=processing_role is ReimbursementDraftFileRole.ATTACHMENT_ONLY,
    )

    with session_factory() as database:
        draft = require_owned_draft(
            database,
            draft_id=draft_id,
            actor=actor,
            mutable=True,
        )
        _require_revision(draft, expected_revision)
        retained_file_count = database.scalar(
            select(func.count())
            .select_from(ReimbursementDraftFile)
            .where(
                ReimbursementDraftFile.draft_id == draft.id,
                ReimbursementDraftFile.file_status != ReimbursementDraftFileStatus.PURGED.value,
            )
        )
        if int(retained_file_count or 0) >= settings.session_max_files:
            raise ApiError(
                "REIMBURSEMENT_FILE_LIMIT",
                f"每次报销最多保留 {settings.session_max_files} 个文件",
                413,
            )
        retained_bytes = database.scalar(
            select(func.coalesce(func.sum(ReimbursementDraftFile.reserved_bytes), 0)).where(
                ReimbursementDraftFile.draft_id == draft.id,
                ReimbursementDraftFile.file_status != ReimbursementDraftFileStatus.PURGED.value,
            )
        )
        if int(retained_bytes or 0) + size_bytes > settings.session_max_bytes:
            raise ApiError(
                "REIMBURSEMENT_DRAFT_STORAGE_LIMIT",
                "当前报销的文件总量超过限制",
                413,
            )
        maximum_sort_order = database.scalar(
            select(func.max(ReimbursementDraftFile.sort_order)).where(
                ReimbursementDraftFile.draft_id == draft.id
            )
        )
        sort_order = int(maximum_sort_order) + 1 if maximum_sort_order is not None else 0

    owner = DraftFileOwner(
        corp_id=actor.corp_id,
        user_id=actor.user_id,
        draft_id=draft_id,
        expected_revision=expected_revision,
    )
    reservation: QuotaReservation | None = None
    finalized = False
    try:
        reservation = quota.reserve_draft_file(
            owner,
            sort_order=sort_order,
            processing_role=processing_role,
            attachment_kind=attachment_kind.value,
            original_name=upload_type.original_name,
            extension=upload_type.extension,
            media_type=upload_type.media_type,
            reserved_bytes=size_bytes,
            expires_at=utc_now() + timedelta(minutes=settings.upload_ttl_minutes),
        )
        quota.mark_writing(owner, reservation)
        upload.file.seek(0)
        staged = await _write_staging_cancellation_safe(
            staging,
            reservation,
            upload.file,
            expected_size=size_bytes,
        )
        new_revision = quota.finalize_draft_file(
            owner,
            reservation,
            staged,
            actor=actor,
        )
        finalized = True
    except BaseException:
        if reservation is not None and not finalized:
            _abandon_upload_without_masking_error(quota, owner, reservation)
        raise

    with session_factory() as database:
        file = _require_active_file(database, draft_id=draft_id, file_id=reservation.record_id)
        return DraftFileMutationResult(revision=new_revision, file=_snapshot(file))


def update_draft_file(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    expected_revision: int,
    processing_role: ReimbursementDraftFileRole | None,
    original_name: str | None,
    attachment_kind: ReimbursementAttachmentKind | None = None,
) -> DraftFileMutationResult:
    draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
        mutable=True,
    )
    _require_revision(draft, expected_revision)
    file = _require_active_file(database, draft_id=draft.id, file_id=file_id)
    if file.ocr_status == ReimbursementOcrStatus.RUNNING.value:
        raise ApiError(
            "REIMBURSEMENT_FILE_BUSY",
            "票据正在识别，请稍后再修改",
            409,
        )
    if processing_role is None and original_name is None and attachment_kind is None:
        raise ApiError("VALIDATION_ERROR", "请指定要修改的文件信息", 422)
    if original_name is not None:
        file.original_name = validate_upload_name(
            original_name,
            expected_extension=file.extension,
        )
    detached_input_json: str | None = None
    next_role = processing_role.value if processing_role is not None else file.processing_role
    next_kind = attachment_kind.value if attachment_kind is not None else file.attachment_kind
    if next_role == ReimbursementDraftFileRole.EXPENSE_SOURCE.value:
        if attachment_kind is not None and attachment_kind != "other":
            raise ApiError("VALIDATION_ERROR", "费用来源文件不能指定证明材料用途", 422)
        next_kind = "other"
    if next_role != file.processing_role or next_kind != file.attachment_kind:
        detached_input_json = detach_draft_file_from_input(
            database, draft=draft, file_id=file.id,
        ).canonical_json
        file.processing_role = next_role
        file.attachment_kind = next_kind
        file.ocr_status = ReimbursementOcrStatus.NOT_REQUESTED.value
        file.ocr_result_json = None

    new_revision = bump_owned_draft_revision(
        database,
        draft_id=draft.id,
        actor=actor,
        expected_revision=expected_revision,
    )
    if detached_input_json is not None:
        database.execute(
            update(ReimbursementDraft)
            .where(
                ReimbursementDraft.id == draft.id,
                ReimbursementDraft.revision == new_revision,
            )
            .values(input_json=detached_input_json)
            .execution_options(synchronize_session=False)
        )
    database.commit()
    database.refresh(file)
    return DraftFileMutationResult(revision=new_revision, file=_snapshot(file))


def begin_draft_file_delete(
    database: Session,
    *,
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    expected_revision: int,
) -> DraftFileDeletion:
    draft = require_owned_draft(
        database,
        draft_id=draft_id,
        actor=actor,
    )
    _require_revision(draft, expected_revision)
    file = database.scalar(
        select(ReimbursementDraftFile).where(
            ReimbursementDraftFile.id == file_id,
            ReimbursementDraftFile.draft_id == draft.id,
            ReimbursementDraftFile.file_status.in_(
                {
                    ReimbursementDraftFileStatus.ACTIVE.value,
                    ReimbursementDraftFileStatus.DELETING.value,
                    ReimbursementDraftFileStatus.PURGED.value,
                }
            ),
        )
    )
    if file is None or file.size_bytes is None or file.sha256 is None:
        raise _file_not_found_error()
    if file.file_status == ReimbursementDraftFileStatus.ACTIVE.value:
        require_owned_draft(
            database,
            draft_id=draft_id,
            actor=actor,
            mutable=True,
        )
        if file.ocr_status == ReimbursementOcrStatus.RUNNING.value:
            raise ApiError(
                "REIMBURSEMENT_FILE_BUSY",
                "票据正在识别，请稍后再删除",
                409,
            )
        referenced = database.scalar(
            select(ReimbursementUpload.id)
            .where(ReimbursementUpload.source_draft_file_id == file.id)
            .limit(1)
        )
        if referenced is not None:
            raise ApiError(
                "REIMBURSEMENT_FILE_IN_USE",
                "文件已被提交流程使用，不能删除",
                409,
            )
        new_revision = bump_owned_draft_revision(
            database,
            draft_id=draft.id,
            actor=actor,
            expected_revision=expected_revision,
        )
        detached_input_json = detach_draft_file_from_input(
            database,
            draft=draft,
            file_id=file.id,
        ).canonical_json
        database.execute(
            update(ReimbursementDraft)
            .where(
                ReimbursementDraft.id == draft.id,
                ReimbursementDraft.revision == new_revision,
            )
            .values(input_json=detached_input_json)
            .execution_options(synchronize_session=False)
        )
        file.file_status = ReimbursementDraftFileStatus.DELETING.value
        deletion = DraftFileDeletion(
            file_id=file.id,
            draft_id=file.draft_id,
            storage_key=file.storage_key,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
            revision=new_revision,
            completed=False,
        )
        database.commit()
        return deletion
    else:
        # A retry resumes a durable delete intent without requiring the draft to
        # remain mutable or bumping its revision twice. Snapshot before rollback:
        # rollback expires ORM attributes and reading them afterwards would open
        # a new synchronous transaction across the caller's await.
        new_revision = draft.revision
        deletion = DraftFileDeletion(
            file_id=file.id,
            draft_id=file.draft_id,
            storage_key=file.storage_key,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
            revision=new_revision,
            completed=file.file_status == ReimbursementDraftFileStatus.PURGED.value,
        )
        database.rollback()
        return deletion


async def complete_draft_file_delete(
    *,
    deletion: DraftFileDeletion,
    actor: DraftActor,
    session_factory: sessionmaker[Session],
    staging: ReimbursementStaging,
) -> int:
    if deletion.completed:
        return deletion.revision
    task = asyncio.create_task(
        asyncio.to_thread(
            _delete_file_and_release_quota,
            deletion,
            actor,
            session_factory,
            staging,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Once DELETING is committed, finish physical deletion and the matching
        # quota release even if the HTTP caller disappears.
        try:
            await task
        except Exception as exc:  # pragma: no cover - defensive recovery logging
            logger.error(
                "Cancelled reimbursement file deletion needs retry",
                extra={
                    "draft_file_id": deletion.file_id,
                    "exception_type": type(exc).__name__,
                },
            )
        raise


async def recognize_draft_file(
    *,
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    expected_revision: int,
    reference_year: int | None,
    settings: Settings,
    session_factory: sessionmaker[Session],
    staging: ReimbursementStaging,
    ocr_service: OcrService,
) -> DraftFileMutationResult:
    operation_id = str(uuid4())
    marker = json.dumps(
        {_OCR_RUNNING_MARKER_KEY: operation_id},
        separators=(",", ":"),
        sort_keys=True,
    )
    with session_factory() as database:
        draft = require_owned_draft(
            database,
            draft_id=draft_id,
            actor=actor,
            mutable=True,
        )
        _require_revision(draft, expected_revision)
        file = _require_active_file(database, draft_id=draft.id, file_id=file_id)
        is_itinerary = (
            file.processing_role == ReimbursementDraftFileRole.ATTACHMENT_ONLY.value
            and file.attachment_kind == ReimbursementAttachmentKind.ITINERARY.value
        )
        if (
            file.processing_role != ReimbursementDraftFileRole.EXPENSE_SOURCE.value
            and not is_itinerary
        ):
            raise ApiError(
                "REIMBURSEMENT_FILE_OCR_NOT_ALLOWED",
                "仅票据来源或行程单材料可进行识别",
                409,
            )
        running_cutoff = utc_now() - timedelta(
            seconds=settings.ocr_timeout_seconds + _OCR_STALE_GRACE_SECONDS
        )
        if (
            file.ocr_status == ReimbursementOcrStatus.RUNNING.value
            and file.updated_at > running_cutoff
        ):
            raise ApiError(
                "REIMBURSEMENT_FILE_OCR_RUNNING",
                "票据正在识别，请稍后查看",
                409,
            )
        # A RUNNING marker older than the worker timeout belongs to a dead
        # process and can be atomically replaced by this attempt.
        source = _snapshot(file)
        operation_revision = bump_owned_draft_revision(
            database,
            draft_id=draft.id,
            actor=actor,
            expected_revision=expected_revision,
        )
        file.ocr_status = ReimbursementOcrStatus.RUNNING.value
        file.ocr_result_json = marker
        keyword_rules = load_receipt_keyword_rules(database)
        database.commit()

    worker_path: Path | None = None
    final_status = ReimbursementOcrStatus.COMPLETE
    failure_payload = failed_itinerary_payload if is_itinerary else failed_expense_payload
    try:
        worker_path = await _materialize_cancellation_safe(source, settings, staging)
        stored = StoredFile(
            temp_id=file_id, path=worker_path, extension=source.extension,
            media_type=source.media_type, size=source.size_bytes,
            original_name=source.original_name,
        )
        if is_itinerary:
            payload = await ocr_service.recognize_itinerary_file(
                stored, reference_year=reference_year,
            )
        else:
            parsed = await ocr_service.recognize_file(
                stored, reference_year=reference_year, keyword_rules=keyword_rules,
            )
            payload = parsed_expense_payload(file_id, parsed)
    except asyncio.CancelledError:
        payload = failure_payload(
            file_id,
            "OCR_CANCELLED",
            "票据识别已中断，请重试",
        )
        await asyncio.shield(
            asyncio.to_thread(
                _settle_cancelled_ocr,
                session_factory=session_factory,
                actor=actor,
                draft_id=draft_id,
                file_id=file_id,
                operation_revision=operation_revision,
                marker=marker,
                payload=payload,
            )
        )
        raise
    except ApiError as exc:
        final_status = ReimbursementOcrStatus.FAILED
        payload = failure_payload(file_id, exc.code, exc.message)
    except Exception as exc:  # Defensive: never log OCR text or a staging path.
        logger.error(
            "Unexpected durable reimbursement OCR error",
            extra={"draft_file_id": file_id, "exception_type": type(exc).__name__},
        )
        final_status = ReimbursementOcrStatus.FAILED
        payload = failure_payload(
            file_id,
            "OCR_FAILED",
            "票据识别失败，请手工填写",
        )
    finally:
        if worker_path is not None:
            worker_path.unlink(missing_ok=True)

    try:
        return _finish_ocr(
            session_factory=session_factory,
            actor=actor,
            draft_id=draft_id,
            file_id=file_id,
            operation_revision=operation_revision,
            marker=marker,
            final_status=final_status,
            payload=payload,
        )
    except ApiError:
        _mark_ocr_interrupted(
            session_factory=session_factory,
            actor=actor,
            draft_id=draft_id,
            file_id=file_id,
            marker=marker,
        )
        raise


async def generate_draft_excel_preview(
    *,
    actor: DraftActor,
    employee_name: str,
    draft_id: str,
    expected_revision: int,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> WorkbookResult:
    with session_factory() as database:
        snapshot = _workbook_preview_snapshot(
            database,
            actor=actor,
            employee_name=employee_name,
            draft_id=draft_id,
            expected_revision=expected_revision,
            settings=settings,
        )

    result = await asyncio.to_thread(
        generate_expense_workbook,
        template_path=settings.excel_template_path,
        employee_name=snapshot.employee_name,
        department_name=snapshot.department_name,
        project=snapshot.project,
        trip=snapshot.input.trip,
        items=complete_expense_items(snapshot.input),
        subsidy=snapshot.subsidy,
        totals=snapshot.totals,
    )

    # Do not serve a workbook calculated from a draft that changed during the
    # CPU-bound generation step.
    with session_factory() as database:
        current = require_owned_draft(database, draft_id=draft_id, actor=actor)
        _require_revision(current, expected_revision)
        if current.input_json != snapshot.canonical_input_json:
            raise _revision_conflict_error()
    return result


def _workbook_preview_snapshot(
    database: Session,
    *,
    actor: DraftActor,
    employee_name: str,
    draft_id: str,
    expected_revision: int,
    settings: Settings,
) -> WorkbookPreviewSnapshot:
    draft = require_owned_draft(database, draft_id=draft_id, actor=actor)
    _require_revision(draft, expected_revision)
    try:
        draft_input = ReimbursementDraftInput.model_validate_json(draft.input_json)
    except ValidationError as exc:
        raise ApiError(
            "REIMBURSEMENT_DRAFT_INVALID",
            "报销内容无法生成 Excel，请重新确认填写内容",
            409,
        ) from exc
    if len(draft_input.items) > settings.expense_max_items:
        raise ApiError(
            "TOO_MANY_EXPENSE_LINES",
            f"当前部署每张报销单最多处理 {settings.expense_max_items} 条费用明细",
            422,
        )
    # A locked historical draft keeps its original receipt counts and evidence.
    if draft.locked_at is None:
        draft_input = apply_ocr_evidence(database, draft_id=draft.id, draft_input=draft_input)
    require_complete_draft_input(draft_input)
    catalog = require_submission_ready_catalog(database)
    calculation = validate_and_calculate_input(
        database,
        catalog=catalog,
        draft_input=draft_input,
        max_items=settings.expense_max_items,
        validate_project=True,
    )
    draft_input = ReimbursementDraftInput.model_validate(calculation.input_data)
    project = ResolvedProject(
        display_text=draft_input.project.text, filename_component=draft_input.project.text
    )
    subsidy = None
    if draft_input.trip is not None:
        expense_settings = get_expense_settings(database)
        subsidy_trip_type = draft_input.trip.subsidy_trip_type()
        subsidy = calculate_subsidy(
            trip_type=subsidy_trip_type,
            period=draft_input.trip.as_period(),
            configured_daily_rate=expense_settings.daily_rate_for(subsidy_trip_type),
            policy_confirmed=draft_input.trip.policy_confirmed,
            confirmed_effective_days=draft_input.trip.confirmed_effective_days,
            no_subsidy_exception=draft_input.trip.no_subsidy_exception,
        )
    totals = calculate_expense_totals(complete_expense_items(draft_input), subsidy)
    return WorkbookPreviewSnapshot(
        canonical_input_json=draft.input_json,
        input=draft_input,
        employee_name=employee_name,
        department_name=draft.department_name,
        project=project,
        subsidy=subsidy,
        totals=totals,
    )


def _inspect_upload_spool(
    upload: UploadFile,
    *,
    settings: Settings,
) -> tuple[int, bytes, Path]:
    upload.file.seek(0, os.SEEK_END)
    size_bytes = upload.file.tell()
    if size_bytes < 1:
        raise ApiError("EMPTY_FILE", "不能上传空文件", 400)
    if size_bytes > settings.upload_max_file_bytes:
        raise ApiError("FILE_TOO_LARGE", "单个文件超过大小限制", 413)
    upload.file.seek(0)
    first_bytes = upload.file.read(16)
    upload.file.seek(0)
    upload.file.flush()
    # The bounded parser owns this path and unlinks it when the UploadFile is
    # closed. Calling fileno rolls an in-memory upload into that private spool.
    upload.file.fileno()
    spool_name = getattr(upload.file, "name", None)
    if not isinstance(spool_name, str) or not spool_name:
        raise ApiError("TEMP_STORAGE_INVALID", "上传缓存空间无效", 500)
    return size_bytes, first_bytes, Path(spool_name)


async def _write_staging_cancellation_safe(
    staging: ReimbursementStaging,
    reservation: QuotaReservation,
    source: BinaryIO,
    *,
    expected_size: int,
) -> StagedObject:
    task = asyncio.create_task(
        asyncio.to_thread(
            staging.write_stream,
            reservation.staging,
            source,
            expected_size=expected_size,
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Never race reservation cleanup against the thread that may still be
        # linking the final object.
        try:
            await task
        except Exception:
            pass
        raise


def _abandon_upload_without_masking_error(
    quota: ReimbursementQuotaCoordinator,
    owner: DraftFileOwner,
    reservation: QuotaReservation,
) -> None:
    try:
        quota.abandon_draft_file(owner, reservation)
    except Exception as exc:
        logger.error(
            "Failed to discard an unfinished reimbursement upload",
            extra={
                "draft_file_id": reservation.record_id,
                "exception_type": type(exc).__name__,
            },
        )


def _delete_file_and_release_quota(
    deletion: DraftFileDeletion,
    actor: DraftActor,
    session_factory: sessionmaker[Session],
    staging: ReimbursementStaging,
) -> int:
    staging.delete(
        deletion.storage_key,
        expected_size=deletion.size_bytes,
        expected_sha256=deletion.sha256,
        missing_ok=True,
    )
    with session_factory() as database:
        file = database.scalar(
            select(ReimbursementDraftFile)
            .join(ReimbursementDraft, ReimbursementDraft.id == ReimbursementDraftFile.draft_id)
            .where(
                ReimbursementDraftFile.id == deletion.file_id,
                ReimbursementDraftFile.draft_id == deletion.draft_id,
                ReimbursementDraftFile.storage_key == deletion.storage_key,
                ReimbursementDraftFile.size_bytes == deletion.size_bytes,
                ReimbursementDraftFile.sha256 == deletion.sha256,
                ReimbursementDraftFile.file_status == ReimbursementDraftFileStatus.DELETING.value,
                ReimbursementDraft.corp_id == actor.corp_id,
                ReimbursementDraft.owner_user_id == actor.user_id,
                ReimbursementDraft.department_id == actor.department_id,
                ReimbursementDraft.department_name == actor.department_name,
                ReimbursementDraft.revision == deletion.revision,
            )
        )
        if file is None:
            raise _revision_conflict_error()
        file.file_status = ReimbursementDraftFileStatus.PURGED.value
        file.part_storage_key = None
        file.reservation_expires_at = None
        file.purged_at = utc_now()
        database.commit()
    return deletion.revision


def _materialize_verified_file(
    file: DraftFileSnapshot,
    settings: Settings,
    staging: ReimbursementStaging,
) -> Path:
    spool_directory = prepare_spool_directory(settings)
    descriptor, raw_path = tempfile.mkstemp(
        prefix="ocr-",
        suffix=f".{file.extension}",
        dir=spool_directory,
    )
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        output = os.fdopen(descriptor, "wb")
        descriptor = -1
        with (
            output,
            staging.open_verified(
                file.storage_key,
                expected_size=file.size_bytes,
                expected_sha256=file.sha256,
            ) as source,
        ):
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        return path
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


async def _materialize_cancellation_safe(
    file: DraftFileSnapshot,
    settings: Settings,
    staging: ReimbursementStaging,
) -> Path:
    task = asyncio.create_task(
        asyncio.to_thread(_materialize_verified_file, file, settings, staging)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # The copy runs in a thread and cannot be force-cancelled. Wait for it
        # and remove any completed copy before unwinding the request.
        try:
            path = await task
            path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _finish_ocr(
    *,
    session_factory: sessionmaker[Session],
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    operation_revision: int,
    marker: str,
    final_status: ReimbursementOcrStatus,
    payload: dict[str, object],
) -> DraftFileMutationResult:
    with session_factory() as database:
        draft = require_owned_draft(
            database,
            draft_id=draft_id,
            actor=actor,
            mutable=True,
        )
        _require_revision(draft, operation_revision)
        file = database.scalar(
            select(ReimbursementDraftFile).where(
                ReimbursementDraftFile.id == file_id,
                ReimbursementDraftFile.draft_id == draft.id,
                ReimbursementDraftFile.file_status == ReimbursementDraftFileStatus.ACTIVE.value,
                ReimbursementDraftFile.ocr_status == ReimbursementOcrStatus.RUNNING.value,
                ReimbursementDraftFile.ocr_result_json == marker,
            )
        )
        if file is None:
            raise ApiError(
                "REIMBURSEMENT_FILE_OPERATION_CONFLICT",
                "票据文件在识别期间已变更",
                409,
            )
        file.ocr_status = final_status.value
        file.ocr_result_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        database.commit()
        database.refresh(file)
        return DraftFileMutationResult(revision=operation_revision, file=_snapshot(file))


def _set_ocr_failed_without_revision(
    session_factory: sessionmaker[Session],
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    marker: str,
    payload: dict[str, object],
) -> None:
    with session_factory() as database:
        file = database.scalar(
            select(ReimbursementDraftFile)
            .join(ReimbursementDraft, ReimbursementDraft.id == ReimbursementDraftFile.draft_id)
            .where(
                ReimbursementDraftFile.id == file_id,
                ReimbursementDraftFile.draft_id == draft_id,
                ReimbursementDraftFile.file_status == ReimbursementDraftFileStatus.ACTIVE.value,
                ReimbursementDraftFile.ocr_status == ReimbursementOcrStatus.RUNNING.value,
                ReimbursementDraftFile.ocr_result_json == marker,
                ReimbursementDraft.corp_id == actor.corp_id,
                ReimbursementDraft.owner_user_id == actor.user_id,
                ReimbursementDraft.department_id == actor.department_id,
                ReimbursementDraft.department_name == actor.department_name,
            )
        )
        if file is None:
            return
        file.ocr_status = ReimbursementOcrStatus.FAILED.value
        file.ocr_result_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        database.commit()


def _mark_ocr_interrupted(
    *,
    session_factory: sessionmaker[Session],
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    marker: str,
) -> None:
    payload = failed_expense_payload(
        file_id,
        "OCR_RESULT_CONFLICT",
        "报销内容在识别期间已变更，请重试",
    )
    _set_ocr_failed_without_revision(
        session_factory,
        actor,
        draft_id,
        file_id,
        marker,
        payload,
    )


def _settle_cancelled_ocr(
    *,
    session_factory: sessionmaker[Session],
    actor: DraftActor,
    draft_id: str,
    file_id: str,
    operation_revision: int,
    marker: str,
    payload: dict[str, object],
) -> None:
    try:
        _finish_ocr(
            session_factory=session_factory,
            actor=actor,
            draft_id=draft_id,
            file_id=file_id,
            operation_revision=operation_revision,
            marker=marker,
            final_status=ReimbursementOcrStatus.FAILED,
            payload=payload,
        )
    except ApiError:
        _mark_ocr_interrupted(
            session_factory=session_factory,
            actor=actor,
            draft_id=draft_id,
            file_id=file_id,
            marker=marker,
        )


def _require_active_file(
    database: Session,
    *,
    draft_id: str,
    file_id: str,
) -> ReimbursementDraftFile:
    file = database.scalar(
        select(ReimbursementDraftFile).where(
            ReimbursementDraftFile.id == file_id,
            ReimbursementDraftFile.draft_id == draft_id,
            ReimbursementDraftFile.file_status == ReimbursementDraftFileStatus.ACTIVE.value,
        )
    )
    if file is None:
        raise _file_not_found_error()
    return file


def _snapshot(file: ReimbursementDraftFile) -> DraftFileSnapshot:
    if file.size_bytes is None or file.sha256 is None:
        raise ApiError(
            "REIMBURSEMENT_FILE_INVALID",
            "票据文件状态异常，请重新上传",
            409,
        )
    return DraftFileSnapshot(
        id=file.id,
        draft_id=file.draft_id,
        original_name=file.original_name,
        extension=file.extension,
        media_type=file.media_type,
        processing_role=file.processing_role,
        sort_order=file.sort_order,
        file_status=file.file_status,
        storage_key=file.storage_key,
        size_bytes=file.size_bytes,
        sha256=file.sha256,
        ocr_status=file.ocr_status,
        ocr_result_json=file.ocr_result_json,
        attachment_kind=file.attachment_kind,
    )


def _require_revision(draft: ReimbursementDraft, expected_revision: int) -> None:
    if draft.revision != expected_revision:
        raise _revision_conflict_error()


def _revision_conflict_error() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_DRAFT_REVISION_CONFLICT",
        "报销内容已在其他操作中更新，请刷新后重试",
        409,
    )


def _file_not_found_error() -> ApiError:
    return ApiError(
        "REIMBURSEMENT_FILE_NOT_FOUND",
        "票据文件不存在",
        404,
    )


def map_reimbursement_storage_error(exc: Exception) -> ApiError:
    if isinstance(exc, ReimbursementQuotaExceeded):
        return ApiError(
            "REIMBURSEMENT_STORAGE_FULL",
            "报销文件空间暂无足够容量，请删除无用文件后重试",
            503,
        )
    if isinstance(exc, ReimbursementReservationConflict):
        return _revision_conflict_error()
    if isinstance(exc, StagingLimitExceeded):
        return ApiError("FILE_TOO_LARGE", "单个文件超过大小限制", 413)
    if isinstance(
        exc,
        (
            StagingIntegrityError,
            StagingLayoutError,
            StagingObjectExists,
            StagingObjectNotFound,
        ),
    ):
        return ApiError(
            "REIMBURSEMENT_STORAGE_ERROR",
            "票据文件保存失败，请重试",
            500,
        )
    return ApiError("INTERNAL_ERROR", "服务暂时不可用", 500)
