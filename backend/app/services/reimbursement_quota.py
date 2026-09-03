from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeAlias

from sqlalchemy import Engine, Select, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.reimbursement import (
    ReimbursementDraft,
    ReimbursementDraftFile,
    ReimbursementDraftFileRole,
    ReimbursementDraftFileStatus,
    ReimbursementDraftStatus,
    ReimbursementSubmission,
    ReimbursementSubmissionStatus,
    ReimbursementUpload,
    ReimbursementUploadLocalStatus,
    ReimbursementUploadRole,
    ReimbursementUploadStatus,
    new_uuid,
    utc_now,
)
from app.services.reimbursement_staging import (
    ReimbursementStaging,
    StagedObject,
    StagingArea,
    StagingReservation,
)

_GENERATED_EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ACTIVE_DRAFT_STATUSES = frozenset(
    {ReimbursementDraftStatus.DRAFT.value, ReimbursementDraftStatus.REVIEW_READY.value}
)


class ReimbursementQuotaError(RuntimeError):
    """Base class for deterministic staging-quota failures."""


class ReimbursementQuotaExceeded(ReimbursementQuotaError):
    def __init__(self, *, maximum_bytes: int, reserved_bytes: int, requested_bytes: int) -> None:
        self.maximum_bytes = maximum_bytes
        self.reserved_bytes = reserved_bytes
        self.requested_bytes = requested_bytes
        super().__init__("reimbursement staging quota is exhausted")


class ReimbursementReservationConflict(ReimbursementQuotaError):
    pass


class ReservationKind(StrEnum):
    DRAFT_FILE = "DRAFT_FILE"
    GENERATED_UPLOAD = "GENERATED_UPLOAD"


@dataclass(frozen=True, slots=True)
class DraftFileOwner:
    corp_id: str
    user_id: str
    draft_id: str
    expected_revision: int


@dataclass(frozen=True, slots=True)
class SubmissionLease:
    corp_id: str
    user_id: str
    submission_id: str
    lease_token: str
    expected_status_version: int


ReservationAuthority: TypeAlias = DraftFileOwner | SubmissionLease


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    kind: ReservationKind
    record_id: str
    staging: StagingReservation


@dataclass(frozen=True, slots=True)
class QuotaUsage:
    reserved_bytes: int
    maximum_bytes: int

    @property
    def available_bytes(self) -> int:
        return max(0, self.maximum_bytes - self.reserved_bytes)


class ReimbursementQuotaCoordinator:
    """Serialize quota admission and reservation lifecycle in SQLite.

    Every mutating method owns its database transaction. Callers therefore
    cannot accidentally perform the usage check and reservation write in two
    different transactions.
    """

    def __init__(
        self,
        engine: Engine,
        staging: ReimbursementStaging,
        *,
        max_bytes: int,
    ) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError("reimbursement quota currently requires SQLite")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")
        if max_bytes < staging.max_object_bytes:
            raise ValueError("max_bytes must cover one maximum-size staging object")
        self._engine = engine
        self._staging = staging
        self._max_bytes = max_bytes

    def usage(self) -> QuotaUsage:
        with self._engine.connect() as connection:
            reserved_bytes = int(connection.execute(_usage_statement()).scalar_one())
        return QuotaUsage(reserved_bytes=reserved_bytes, maximum_bytes=self._max_bytes)

    def reserve_draft_file(
        self,
        owner: DraftFileOwner,
        *,
        sort_order: int,
        processing_role: ReimbursementDraftFileRole,
        original_name: str,
        extension: str,
        media_type: str,
        reserved_bytes: int,
        expires_at: datetime,
    ) -> QuotaReservation:
        _require_positive_size(reserved_bytes)
        _require_sort_order(sort_order)
        if not isinstance(processing_role, ReimbursementDraftFileRole):
            raise ValueError("processing_role is invalid")
        now = utc_now()
        expiry = _naive_utc(expires_at)
        if expiry <= now:
            raise ValueError("reservation expiry must be in the future")
        record_id = new_uuid()
        staging_reservation = self._staging.new_reservation(
            StagingArea.DRAFTS,
            owner.draft_id,
            extension,
            reserved_bytes=reserved_bytes,
        )

        try:
            with self._write_session() as database:
                draft = database.scalar(_draft_owner_query(owner))
                if draft is None or draft.status not in _ACTIVE_DRAFT_STATUSES:
                    raise ReimbursementReservationConflict(
                        "draft ownership, revision, or state changed"
                    )
                self._admit(database, reserved_bytes)
                database.add(
                    ReimbursementDraftFile(
                        id=record_id,
                        draft_id=draft.id,
                        sort_order=sort_order,
                        processing_role=processing_role.value,
                        file_status=ReimbursementDraftFileStatus.RESERVED.value,
                        storage_key=staging_reservation.storage_key,
                        part_storage_key=staging_reservation.part_storage_key,
                        reserved_bytes=reserved_bytes,
                        reservation_expires_at=expiry,
                        original_name=_required_text(original_name, maximum=255),
                        extension=extension.lower().lstrip("."),
                        media_type=_required_text(media_type, maximum=128),
                        size_bytes=None,
                        sha256=None,
                    )
                )
        except IntegrityError as exc:
            raise ReimbursementReservationConflict("draft file reservation conflicts") from exc

        return QuotaReservation(
            kind=ReservationKind.DRAFT_FILE,
            record_id=record_id,
            staging=staging_reservation,
        )

    def reserve_generated_upload(
        self,
        lease: SubmissionLease,
        *,
        sort_order: int,
        file_name: str,
        reserved_bytes: int,
        expires_at: datetime,
    ) -> QuotaReservation:
        _require_positive_size(reserved_bytes)
        _require_sort_order(sort_order)
        normalized_name = _required_text(file_name, maximum=255)
        if not normalized_name.casefold().endswith(".xlsx"):
            raise ValueError("generated workbook name must end in .xlsx")
        now = utc_now()
        expiry = _naive_utc(expires_at)
        if expiry <= now:
            raise ValueError("reservation expiry must be in the future")
        record_id = new_uuid()
        staging_reservation = self._staging.new_reservation(
            StagingArea.GENERATED,
            lease.submission_id,
            "xlsx",
            reserved_bytes=reserved_bytes,
        )

        try:
            with self._write_session() as database:
                submission = database.scalar(_submission_lease_query(lease, now=now))
                if submission is None:
                    raise ReimbursementReservationConflict(
                        "submission ownership, lease, version, or state changed"
                    )
                self._admit(database, reserved_bytes)
                database.add(
                    ReimbursementUpload(
                        id=record_id,
                        submission_id=submission.id,
                        draft_id=submission.draft_id,
                        source_draft_file_id=None,
                        role=ReimbursementUploadRole.GENERATED_EXCEL.value,
                        sort_order=sort_order,
                        local_storage_key=staging_reservation.storage_key,
                        local_part_storage_key=staging_reservation.part_storage_key,
                        local_status=ReimbursementUploadLocalStatus.RESERVED.value,
                        reserved_bytes=reserved_bytes,
                        reservation_expires_at=expiry,
                        file_name=normalized_name,
                        file_type="xlsx",
                        media_type=_GENERATED_EXCEL_MEDIA_TYPE,
                        size_bytes=None,
                        sha256=None,
                        upload_status=ReimbursementUploadStatus.PENDING.value,
                        status_version=1,
                        attempt_count=0,
                    )
                )
        except IntegrityError as exc:
            raise ReimbursementReservationConflict(
                "generated workbook reservation conflicts"
            ) from exc

        return QuotaReservation(
            kind=ReservationKind.GENERATED_UPLOAD,
            record_id=record_id,
            staging=staging_reservation,
        )

    def mark_writing(
        self,
        authority: ReservationAuthority,
        reservation: QuotaReservation,
    ) -> None:
        now = utc_now()
        with self._write_session() as database:
            record = self._owned_record(database, authority, reservation, now=now)
            if _local_status(record) != ReimbursementDraftFileStatus.RESERVED.value:
                raise ReimbursementReservationConflict("reservation is not awaiting a writer")
            if record.reservation_expires_at is None or record.reservation_expires_at <= now:
                raise ReimbursementReservationConflict("reservation has expired")
            _set_local_status(record, ReimbursementDraftFileStatus.WRITING.value)

    def finalize(
        self,
        authority: ReservationAuthority,
        reservation: QuotaReservation,
        staged: StagedObject,
    ) -> None:
        if not isinstance(staged, StagedObject):
            raise ValueError("staged object is required")
        if staged.storage_key != reservation.staging.storage_key:
            raise ReimbursementReservationConflict("staged object does not match reservation")
        if (
            isinstance(staged.size_bytes, bool)
            or not isinstance(staged.size_bytes, int)
            or staged.size_bytes < 1
            or staged.size_bytes > reservation.staging.reserved_bytes
        ):
            raise ReimbursementReservationConflict("staged object exceeds reservation")
        if not _valid_sha256(staged.sha256):
            raise ReimbursementReservationConflict("staged object digest is invalid")

        with self._write_session() as database:
            record = self._owned_record(database, authority, reservation, now=utc_now())
            if _local_status(record) != ReimbursementDraftFileStatus.WRITING.value:
                raise ReimbursementReservationConflict("reservation is not being written")
            record.size_bytes = staged.size_bytes
            record.sha256 = staged.sha256
            record.reservation_expires_at = None
            if isinstance(record, ReimbursementDraftFile):
                record.file_status = ReimbursementDraftFileStatus.ACTIVE.value
                record.part_storage_key = None
            else:
                record.local_status = ReimbursementUploadLocalStatus.READY.value
                record.local_part_storage_key = None

    def release(
        self,
        authority: ReservationAuthority,
        reservation: QuotaReservation,
    ) -> None:
        """Delete the exact local object before releasing its database quota."""

        now = utc_now()
        with self._write_session() as database:
            record = self._owned_release_record(database, authority, reservation, now=now)
            local_status = _local_status(record)
            if local_status in {
                ReimbursementDraftFileStatus.RESERVED.value,
                ReimbursementDraftFileStatus.WRITING.value,
            }:
                if isinstance(record, ReimbursementUpload) and (
                    record.upload_status != ReimbursementUploadStatus.PENDING.value
                ):
                    raise ReimbursementReservationConflict(
                        "remote upload state still requires its local reservation"
                    )
                self._staging.discard_reservation(reservation.staging)
            elif isinstance(record, ReimbursementDraftFile) and (
                local_status == ReimbursementDraftFileStatus.ACTIVE.value
            ):
                referenced = database.scalar(
                    select(ReimbursementUpload.id)
                    .where(ReimbursementUpload.source_draft_file_id == record.id)
                    .limit(1)
                )
                if referenced is not None or record.size_bytes is None or record.sha256 is None:
                    raise ReimbursementReservationConflict(
                        "draft file is still required by a submission"
                    )
                self._staging.delete(
                    record.storage_key,
                    expected_size=record.size_bytes,
                    expected_sha256=record.sha256,
                    missing_ok=True,
                )
            elif isinstance(record, ReimbursementUpload) and (
                local_status == ReimbursementUploadLocalStatus.READY.value
            ):
                if record.upload_status not in {
                    ReimbursementUploadStatus.LINKED.value,
                    ReimbursementUploadStatus.CLEANED.value,
                    ReimbursementUploadStatus.DISCARDED.value,
                }:
                    raise ReimbursementReservationConflict(
                        "remote upload state still requires its local object"
                    )
                if record.size_bytes is None or record.sha256 is None:
                    raise ReimbursementReservationConflict("local object metadata is incomplete")
                if record.upload_status == ReimbursementUploadStatus.LINKED.value:
                    confirmed_instance_id = database.scalar(
                        select(ReimbursementSubmission.process_instance_id).where(
                            ReimbursementSubmission.id == record.submission_id,
                            ReimbursementSubmission.draft_id == record.draft_id,
                            ReimbursementSubmission.process_instance_id.is_not(None),
                        )
                    )
                    if confirmed_instance_id is None or not record.space_id or not record.file_id:
                        raise ReimbursementReservationConflict(
                            "linked upload has not been confirmed against an OA instance"
                        )
                self._staging.delete(
                    record.local_storage_key,
                    expected_size=record.size_bytes,
                    expected_sha256=record.sha256,
                    missing_ok=True,
                )
            else:
                raise ReimbursementReservationConflict("local reservation is not releasable")

            if isinstance(record, ReimbursementDraftFile):
                record.file_status = ReimbursementDraftFileStatus.PURGED.value
                record.part_storage_key = None
                record.reservation_expires_at = None
                record.purged_at = now
            else:
                if record.upload_status == ReimbursementUploadStatus.PENDING.value:
                    record.upload_status = ReimbursementUploadStatus.DISCARDED.value
                record.local_status = ReimbursementUploadLocalStatus.DELETED.value
                record.local_part_storage_key = None
                record.reservation_expires_at = None
                record.local_deleted_at = now
                record.status_version += 1

    def reclaim_expired(self, *, now: datetime | None = None) -> int:
        """Release only expired, unowned write attempts.

        Active submission leases and any upload that reached a remote mutation
        state are deliberately excluded. Filesystem names are removed before
        the database rows stop contributing to quota.
        """

        cutoff = _naive_utc(now) if now is not None else utc_now()
        reclaimed = 0
        with self._write_session() as database:
            draft_records = database.scalars(
                select(ReimbursementDraftFile).where(
                    ReimbursementDraftFile.file_status.in_(
                        {
                            ReimbursementDraftFileStatus.RESERVED.value,
                            ReimbursementDraftFileStatus.WRITING.value,
                        }
                    ),
                    ReimbursementDraftFile.reservation_expires_at.is_not(None),
                    ReimbursementDraftFile.reservation_expires_at <= cutoff,
                    ~select(ReimbursementSubmission.id)
                    .where(ReimbursementSubmission.draft_id == ReimbursementDraftFile.draft_id)
                    .exists(),
                    ~select(ReimbursementUpload.id)
                    .where(ReimbursementUpload.source_draft_file_id == ReimbursementDraftFile.id)
                    .exists(),
                )
            ).all()
            for record in draft_records:
                reservation = _draft_staging_reservation(record)
                self._staging.discard_reservation(reservation)
                record.file_status = ReimbursementDraftFileStatus.PURGED.value
                record.part_storage_key = None
                record.reservation_expires_at = None
                record.purged_at = cutoff
                reclaimed += 1

            upload_records = database.scalars(
                select(ReimbursementUpload)
                .join(
                    ReimbursementSubmission,
                    ReimbursementSubmission.id == ReimbursementUpload.submission_id,
                )
                .where(
                    ReimbursementUpload.role == ReimbursementUploadRole.GENERATED_EXCEL.value,
                    ReimbursementUpload.local_status.in_(
                        {
                            ReimbursementUploadLocalStatus.RESERVED.value,
                            ReimbursementUploadLocalStatus.WRITING.value,
                        }
                    ),
                    ReimbursementUpload.upload_status == ReimbursementUploadStatus.PENDING.value,
                    ReimbursementUpload.reservation_expires_at.is_not(None),
                    ReimbursementUpload.reservation_expires_at <= cutoff,
                    ReimbursementSubmission.process_instance_id.is_(None),
                    (
                        ReimbursementSubmission.lease_expires_at.is_(None)
                        | (ReimbursementSubmission.lease_expires_at <= cutoff)
                    ),
                )
            ).all()
            for record in upload_records:
                reservation = _upload_staging_reservation(record)
                self._staging.discard_reservation(reservation)
                record.upload_status = ReimbursementUploadStatus.DISCARDED.value
                record.local_status = ReimbursementUploadLocalStatus.DELETED.value
                record.local_part_storage_key = None
                record.reservation_expires_at = None
                record.local_deleted_at = cutoff
                record.status_version += 1
                reclaimed += 1
        return reclaimed

    def _admit(self, database: Session, requested_bytes: int) -> None:
        reserved_bytes = int(database.execute(_usage_statement()).scalar_one())
        if requested_bytes > self._max_bytes - reserved_bytes:
            raise ReimbursementQuotaExceeded(
                maximum_bytes=self._max_bytes,
                reserved_bytes=reserved_bytes,
                requested_bytes=requested_bytes,
            )

    @contextmanager
    def _write_session(self) -> Iterator[Session]:
        with self._engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            database = Session(bind=connection, autoflush=False, expire_on_commit=False)
            try:
                yield database
                database.flush()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                database.close()

    def _owned_record(
        self,
        database: Session,
        authority: ReservationAuthority,
        reservation: QuotaReservation,
        *,
        now: datetime,
    ) -> ReimbursementDraftFile | ReimbursementUpload:
        if not isinstance(reservation, QuotaReservation):
            raise ValueError("quota reservation is required")
        expected = reservation.staging
        if reservation.kind is ReservationKind.DRAFT_FILE and isinstance(authority, DraftFileOwner):
            record = database.scalar(
                select(ReimbursementDraftFile)
                .join(ReimbursementDraft, ReimbursementDraft.id == ReimbursementDraftFile.draft_id)
                .where(
                    ReimbursementDraftFile.id == reservation.record_id,
                    ReimbursementDraftFile.draft_id == authority.draft_id,
                    ReimbursementDraftFile.storage_key == expected.storage_key,
                    ReimbursementDraftFile.part_storage_key == expected.part_storage_key,
                    ReimbursementDraftFile.reserved_bytes == expected.reserved_bytes,
                    ReimbursementDraft.corp_id == authority.corp_id,
                    ReimbursementDraft.owner_user_id == authority.user_id,
                    ReimbursementDraft.revision == authority.expected_revision,
                    ReimbursementDraft.status.in_(_ACTIVE_DRAFT_STATUSES),
                )
            )
        elif reservation.kind is ReservationKind.GENERATED_UPLOAD and isinstance(
            authority, SubmissionLease
        ):
            record = database.scalar(
                select(ReimbursementUpload)
                .join(
                    ReimbursementSubmission,
                    ReimbursementSubmission.id == ReimbursementUpload.submission_id,
                )
                .where(
                    ReimbursementUpload.id == reservation.record_id,
                    ReimbursementUpload.submission_id == authority.submission_id,
                    ReimbursementUpload.role == ReimbursementUploadRole.GENERATED_EXCEL.value,
                    ReimbursementUpload.local_storage_key == expected.storage_key,
                    ReimbursementUpload.local_part_storage_key == expected.part_storage_key,
                    ReimbursementUpload.reserved_bytes == expected.reserved_bytes,
                    ReimbursementSubmission.corp_id == authority.corp_id,
                    ReimbursementSubmission.originator_user_id == authority.user_id,
                    ReimbursementSubmission.status
                    == ReimbursementSubmissionStatus.GENERATING_EXCEL.value,
                    ReimbursementSubmission.status_version == authority.expected_status_version,
                    ReimbursementSubmission.lease_token == authority.lease_token,
                    ReimbursementSubmission.lease_expires_at.is_not(None),
                    ReimbursementSubmission.lease_expires_at > now,
                )
            )
        else:
            record = None
        if record is None:
            raise ReimbursementReservationConflict(
                "reservation ownership, lease, version, or state changed"
            )
        return record

    def _owned_release_record(
        self,
        database: Session,
        authority: ReservationAuthority,
        reservation: QuotaReservation,
        *,
        now: datetime,
    ) -> ReimbursementDraftFile | ReimbursementUpload:
        if not isinstance(reservation, QuotaReservation):
            raise ValueError("quota reservation is required")
        expected = reservation.staging
        if reservation.kind is ReservationKind.DRAFT_FILE and isinstance(authority, DraftFileOwner):
            record = database.scalar(
                select(ReimbursementDraftFile)
                .join(ReimbursementDraft, ReimbursementDraft.id == ReimbursementDraftFile.draft_id)
                .where(
                    ReimbursementDraftFile.id == reservation.record_id,
                    ReimbursementDraftFile.draft_id == authority.draft_id,
                    ReimbursementDraftFile.storage_key == expected.storage_key,
                    ReimbursementDraftFile.reserved_bytes == expected.reserved_bytes,
                    ReimbursementDraft.corp_id == authority.corp_id,
                    ReimbursementDraft.owner_user_id == authority.user_id,
                    ReimbursementDraft.revision == authority.expected_revision,
                    ReimbursementDraft.status.in_(_ACTIVE_DRAFT_STATUSES),
                )
            )
        elif reservation.kind is ReservationKind.GENERATED_UPLOAD and isinstance(
            authority, SubmissionLease
        ):
            record = database.scalar(
                select(ReimbursementUpload)
                .join(
                    ReimbursementSubmission,
                    ReimbursementSubmission.id == ReimbursementUpload.submission_id,
                )
                .where(
                    ReimbursementUpload.id == reservation.record_id,
                    ReimbursementUpload.submission_id == authority.submission_id,
                    ReimbursementUpload.role == ReimbursementUploadRole.GENERATED_EXCEL.value,
                    ReimbursementUpload.local_storage_key == expected.storage_key,
                    ReimbursementUpload.reserved_bytes == expected.reserved_bytes,
                    ReimbursementSubmission.corp_id == authority.corp_id,
                    ReimbursementSubmission.originator_user_id == authority.user_id,
                    ReimbursementSubmission.status_version == authority.expected_status_version,
                    ReimbursementSubmission.lease_token == authority.lease_token,
                    ReimbursementSubmission.lease_expires_at.is_not(None),
                    ReimbursementSubmission.lease_expires_at > now,
                )
            )
        else:
            record = None
        if record is None:
            raise ReimbursementReservationConflict(
                "reservation ownership, lease, version, or state changed"
            )
        if _active_part_key(record) is not None and (
            _active_part_key(record) != expected.part_storage_key
        ):
            raise ReimbursementReservationConflict("reservation attempt changed")
        return record


def _usage_statement():
    return text(
        """
        SELECT COALESCE(SUM(reserved_bytes), 0)
        FROM (
            SELECT storage_key, MAX(reserved_bytes) AS reserved_bytes
            FROM (
                SELECT storage_key, reserved_bytes
                FROM reimbursement_draft_files
                WHERE file_status != 'PURGED'
                UNION ALL
                SELECT local_storage_key AS storage_key, reserved_bytes
                FROM reimbursement_uploads
                WHERE local_status != 'DELETED'
            ) AS live_reservations
            GROUP BY storage_key
        ) AS physical_objects
        """
    )


def _draft_owner_query(owner: DraftFileOwner) -> Select[tuple[ReimbursementDraft]]:
    if not isinstance(owner, DraftFileOwner):
        raise ValueError("draft file owner is required")
    if owner.expected_revision < 1:
        raise ValueError("expected_revision must be positive")
    return select(ReimbursementDraft).where(
        ReimbursementDraft.id == owner.draft_id,
        ReimbursementDraft.corp_id == owner.corp_id,
        ReimbursementDraft.owner_user_id == owner.user_id,
        ReimbursementDraft.revision == owner.expected_revision,
    )


def _submission_lease_query(
    lease: SubmissionLease,
    *,
    now: datetime,
) -> Select[tuple[ReimbursementSubmission]]:
    if not isinstance(lease, SubmissionLease):
        raise ValueError("submission lease is required")
    if lease.expected_status_version < 1:
        raise ValueError("expected_status_version must be positive")
    token = _required_text(lease.lease_token, maximum=64)
    return select(ReimbursementSubmission).where(
        ReimbursementSubmission.id == lease.submission_id,
        ReimbursementSubmission.corp_id == lease.corp_id,
        ReimbursementSubmission.originator_user_id == lease.user_id,
        ReimbursementSubmission.status == ReimbursementSubmissionStatus.GENERATING_EXCEL.value,
        ReimbursementSubmission.status_version == lease.expected_status_version,
        ReimbursementSubmission.lease_token == token,
        ReimbursementSubmission.lease_expires_at.is_not(None),
        ReimbursementSubmission.lease_expires_at > now,
    )


def _require_positive_size(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("reserved_bytes must be a positive integer")


def _require_sort_order(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("sort_order must be a non-negative integer")


def _required_text(value: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError("text value is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or any(c in normalized for c in "\r\n\x00"):
        raise ValueError("text value is invalid")
    return normalized


def _naive_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp is invalid")
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _local_status(record: ReimbursementDraftFile | ReimbursementUpload) -> str:
    if isinstance(record, ReimbursementDraftFile):
        return record.file_status
    return record.local_status


def _set_local_status(record: ReimbursementDraftFile | ReimbursementUpload, value: str) -> None:
    if isinstance(record, ReimbursementDraftFile):
        record.file_status = value
    else:
        record.local_status = value


def _active_part_key(record: ReimbursementDraftFile | ReimbursementUpload) -> str | None:
    if isinstance(record, ReimbursementDraftFile):
        return record.part_storage_key
    return record.local_part_storage_key


def _draft_staging_reservation(record: ReimbursementDraftFile) -> StagingReservation:
    if record.part_storage_key is None:
        raise ReimbursementReservationConflict("draft reservation attempt is incomplete")
    return StagingReservation(
        storage_key=record.storage_key,
        part_storage_key=record.part_storage_key,
        reserved_bytes=record.reserved_bytes,
    )


def _upload_staging_reservation(record: ReimbursementUpload) -> StagingReservation:
    if record.local_part_storage_key is None:
        raise ReimbursementReservationConflict("upload reservation attempt is incomplete")
    return StagingReservation(
        storage_key=record.local_storage_key,
        part_storage_key=record.local_part_storage_key,
        reserved_bytes=record.reserved_bytes,
    )


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None
