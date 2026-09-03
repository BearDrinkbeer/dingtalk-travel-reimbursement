from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.session import create_database_engine
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
    utc_now,
)
from app.services.reimbursement_quota import (
    DraftFileOwner,
    ReimbursementQuotaCoordinator,
    ReimbursementQuotaExceeded,
    ReimbursementReservationConflict,
    SubmissionLease,
)
from app.services.reimbursement_staging import (
    ReimbursementStaging,
    StagedObject,
    StagingObjectNotFound,
)

_HASH = "a" * 64


def _new_draft() -> ReimbursementDraft:
    return ReimbursementDraft(
        corp_id="corp-test",
        owner_user_id="employee-1",
        status=ReimbursementDraftStatus.DRAFT.value,
        revision=1,
        department_id="department-1",
        department_name="测试部门",
        template_process_code="PROC-REIMBURSEMENT",
        template_config_version=1,
        schema_fingerprint=_HASH,
        input_json="{}",
        related_instance_ids_json="[]",
        expires_at=utc_now() + timedelta(days=1),
    )


def _new_generating_submission(draft: ReimbursementDraft) -> ReimbursementSubmission:
    now = utc_now()
    return ReimbursementSubmission(
        draft_id=draft.id,
        corp_id=draft.corp_id,
        originator_user_id=draft.owner_user_id,
        originator_union_id="union-1",
        originator_name="测试员工",
        department_id=draft.department_id,
        department_name=draft.department_name,
        template_process_code=draft.template_process_code,
        template_config_version=draft.template_config_version,
        schema_fingerprint=draft.schema_fingerprint,
        form_snapshot_json=draft.input_json,
        related_instance_ids_json=draft.related_instance_ids_json,
        snapshot_sha256="b" * 64,
        idempotency_key_hash="c" * 64,
        status=ReimbursementSubmissionStatus.GENERATING_EXCEL.value,
        status_version=3,
        lease_owner="worker-1",
        lease_token="lease-token-1",
        lease_expires_at=now + timedelta(minutes=10),
    )


def test_concurrent_database_reservations_admit_only_one_writer(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'quota.db'}"
    setup_engine = create_database_engine(database_url)
    Base.metadata.create_all(setup_engine)
    with setup_engine.begin() as connection:
        draft = _new_draft()
        with Session(bind=connection) as database:
            database.add(draft)
            database.flush()
            draft_id = draft.id
    setup_engine.dispose()

    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    first_engine = create_database_engine(database_url)
    second_engine = create_database_engine(database_url)
    coordinators = (
        ReimbursementQuotaCoordinator(first_engine, staging, max_bytes=100),
        ReimbursementQuotaCoordinator(second_engine, staging, max_bytes=100),
    )
    owner = DraftFileOwner(
        corp_id="corp-test",
        user_id="employee-1",
        draft_id=draft_id,
        expected_revision=1,
    )
    barrier = Barrier(2)

    def reserve(index: int):
        barrier.wait(timeout=5)
        return coordinators[index].reserve_draft_file(
            owner,
            sort_order=index,
            processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
            original_name=f"发票-{index}.pdf",
            extension="pdf",
            media_type="application/pdf",
            reserved_bytes=60,
            expires_at=utc_now() + timedelta(minutes=5),
        )

    outcomes: list[object] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, index) for index in range(2)]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except Exception as exc:  # noqa: BLE001 - winner is intentionally nondeterministic
                outcomes.append(exc)

    assert sum(isinstance(item, ReimbursementQuotaExceeded) for item in outcomes) == 1
    with first_engine.connect() as connection:
        rows = connection.execute(select(ReimbursementDraftFile)).all()
    assert len(rows) == 1
    assert coordinators[0].usage().reserved_bytes == 60
    assert not list((tmp_path / "staging").rglob("*.pdf"))

    first_engine.dispose()
    second_engine.dispose()


def test_generated_excel_reservation_is_owned_by_the_active_submission_lease(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.flush()
        submission = _new_generating_submission(draft)
        database.add(submission)
        database.commit()
        submission_id = submission.id

    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    lease = SubmissionLease(
        corp_id="corp-test",
        user_id="employee-1",
        submission_id=submission_id,
        lease_token="lease-token-1",
        expected_status_version=3,
    )

    reservation = coordinator.reserve_generated_upload(
        lease,
        sort_order=2,
        file_name="差旅费报销单.xlsx",
        reserved_bytes=80,
        expires_at=utc_now() + timedelta(minutes=5),
    )

    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        assert upload.role == ReimbursementUploadRole.GENERATED_EXCEL.value
        assert upload.local_status == ReimbursementUploadLocalStatus.RESERVED.value
        assert upload.local_storage_key == reservation.staging.storage_key
        assert upload.local_part_storage_key == reservation.staging.part_storage_key
    assert coordinator.usage().reserved_bytes == 80

    engine.dispose()


def test_draft_file_reservation_moves_to_writing_and_finalizes_with_cas(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.commit()
        draft_id = draft.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    owner = DraftFileOwner("corp-test", "employee-1", draft_id, 1)
    reservation = coordinator.reserve_draft_file(
        owner,
        sort_order=0,
        processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
        original_name="发票.pdf",
        extension="pdf",
        media_type="application/pdf",
        reserved_bytes=20,
        expires_at=utc_now() + timedelta(minutes=5),
    )

    coordinator.mark_writing(owner, reservation)
    with Session(engine) as database:
        row = database.get(ReimbursementDraftFile, reservation.record_id)
        assert row is not None
        assert row.file_status == "WRITING"

    staged = staging.write_bytes(reservation.staging, b"verified-content")
    coordinator.finalize(owner, reservation, staged)

    with Session(engine) as database:
        row = database.get(ReimbursementDraftFile, reservation.record_id)
        assert row is not None
        assert row.file_status == "ACTIVE"
        assert row.part_storage_key is None
        assert row.reservation_expires_at is None
        assert row.size_bytes == len(b"verified-content")
        assert row.sha256 == staged.sha256
    with pytest.raises(ReimbursementReservationConflict):
        coordinator.finalize(owner, reservation, staged)

    engine.dispose()


def test_finalize_never_accepts_more_bytes_than_were_reserved(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.commit()
        draft_id = draft.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    owner = DraftFileOwner("corp-test", "employee-1", draft_id, 1)
    reservation = coordinator.reserve_draft_file(
        owner,
        sort_order=0,
        processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
        original_name="发票.pdf",
        extension="pdf",
        media_type="application/pdf",
        reserved_bytes=10,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    coordinator.mark_writing(owner, reservation)

    oversized = StagedObject(
        storage_key=reservation.staging.storage_key,
        size_bytes=11,
        sha256="d" * 64,
    )
    with pytest.raises(ReimbursementReservationConflict):
        coordinator.finalize(owner, reservation, oversized)

    with Session(engine) as database:
        row = database.get(ReimbursementDraftFile, reservation.record_id)
        assert row is not None
        assert row.file_status == "WRITING"
        assert row.size_bytes is None

    engine.dispose()


def test_generated_upload_lifecycle_rejects_a_stale_worker_lease(tmp_path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.flush()
        submission = _new_generating_submission(draft)
        database.add(submission)
        database.commit()
        submission_id = submission.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    lease = SubmissionLease(
        "corp-test",
        "employee-1",
        submission_id,
        "lease-token-1",
        3,
    )
    reservation = coordinator.reserve_generated_upload(
        lease,
        sort_order=0,
        file_name="差旅费报销单.xlsx",
        reserved_bytes=30,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    stale_lease = SubmissionLease(
        "corp-test",
        "employee-1",
        submission_id,
        "stale-token",
        3,
    )

    with pytest.raises(ReimbursementReservationConflict):
        coordinator.mark_writing(stale_lease, reservation)

    coordinator.mark_writing(lease, reservation)
    staged = staging.write_bytes(reservation.staging, b"workbook-bytes")
    coordinator.finalize(lease, reservation, staged)

    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        assert upload.local_status == ReimbursementUploadLocalStatus.READY.value
        assert upload.size_bytes == len(b"workbook-bytes")
        assert upload.sha256 == staged.sha256
        assert upload.local_part_storage_key is None

    engine.dispose()


def test_release_aborts_an_unwritten_draft_reservation_and_frees_quota(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.commit()
        draft_id = draft.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    owner = DraftFileOwner("corp-test", "employee-1", draft_id, 1)
    reservation = coordinator.reserve_draft_file(
        owner,
        sort_order=0,
        processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
        original_name="发票.pdf",
        extension="pdf",
        media_type="application/pdf",
        reserved_bytes=90,
        expires_at=utc_now() + timedelta(minutes=5),
    )

    coordinator.release(owner, reservation)

    assert coordinator.usage().reserved_bytes == 0
    with Session(engine) as database:
        row = database.get(ReimbursementDraftFile, reservation.record_id)
        assert row is not None
        assert row.file_status == "PURGED"
        assert row.purged_at is not None
        assert row.part_storage_key is None

    engine.dispose()


def test_release_refuses_remote_commit_but_linked_release_only_deletes_local_copy(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.flush()
        submission = _new_generating_submission(draft)
        database.add(submission)
        database.commit()
        submission_id = submission.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    lease = SubmissionLease(
        "corp-test",
        "employee-1",
        submission_id,
        "lease-token-1",
        3,
    )
    reservation = coordinator.reserve_generated_upload(
        lease,
        sort_order=0,
        file_name="差旅费报销单.xlsx",
        reserved_bytes=30,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    coordinator.mark_writing(lease, reservation)
    staged = staging.write_bytes(reservation.staging, b"workbook-bytes")
    coordinator.finalize(lease, reservation, staged)
    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        upload.upload_status = "COMMITTING"
        upload.commit_started_at = utc_now()
        database.commit()

    with pytest.raises(ReimbursementReservationConflict):
        coordinator.release(lease, reservation)
    assert coordinator.usage().reserved_bytes == 30

    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        upload.upload_status = "COMMITTED"
        upload.space_id = "space-1"
        upload.file_id = "file-1"
        database.commit()

    with pytest.raises(ReimbursementReservationConflict):
        coordinator.release(lease, reservation)
    assert coordinator.usage().reserved_bytes == 30

    now = utc_now()
    with Session(engine) as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert submission is not None and upload is not None
        submission.status = ReimbursementSubmissionStatus.VERIFYING.value
        submission.oa_create_started_at = now
        submission.oa_request_hash = "e" * 64
        submission.process_instance_id = "instance-1"
        upload.upload_status = "LINKED"
        upload.space_id = "space-1"
        upload.file_id = "file-1"
        upload.linked_at = now
        database.commit()

    coordinator.release(lease, reservation)

    assert coordinator.usage().reserved_bytes == 0
    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        assert upload.local_status == ReimbursementUploadLocalStatus.DELETED.value
        assert upload.local_deleted_at is not None
        assert upload.upload_status == "LINKED"
        assert upload.file_id == "file-1"
        assert upload.space_id == "space-1"
    with pytest.raises(StagingObjectNotFound):
        staging.read_bytes(
            reservation.staging.storage_key,
            expected_size=staged.size_bytes,
            expected_sha256=staged.sha256,
        )

    engine.dispose()


def test_expired_draft_write_reclaims_its_installed_but_unfinalized_object(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.commit()
        draft_id = draft.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    owner = DraftFileOwner("corp-test", "employee-1", draft_id, 1)
    reservation = coordinator.reserve_draft_file(
        owner,
        sort_order=0,
        processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
        original_name="发票.pdf",
        extension="pdf",
        media_type="application/pdf",
        reserved_bytes=70,
        expires_at=utc_now() + timedelta(minutes=1),
    )
    coordinator.mark_writing(owner, reservation)
    staged = staging.write_bytes(reservation.staging, b"unfinalized-content")

    assert coordinator.reclaim_expired(now=utc_now() + timedelta(minutes=2)) == 1
    assert coordinator.usage().reserved_bytes == 0
    with Session(engine) as database:
        record = database.get(ReimbursementDraftFile, reservation.record_id)
        assert record is not None
        assert record.file_status == ReimbursementDraftFileStatus.PURGED.value
        assert record.purged_at is not None
    with pytest.raises(StagingObjectNotFound):
        staging.read_bytes(
            reservation.staging.storage_key,
            expected_size=staged.size_bytes,
            expected_sha256=staged.sha256,
        )

    engine.dispose()


def test_expired_reclaim_skips_an_active_submission_then_frees_its_stale_reservation(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(f"sqlite:///{tmp_path / 'quota.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.flush()
        submission = _new_generating_submission(draft)
        database.add(submission)
        database.commit()
        submission_id = submission.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    lease = SubmissionLease(
        "corp-test",
        "employee-1",
        submission_id,
        "lease-token-1",
        3,
    )
    reservation = coordinator.reserve_generated_upload(
        lease,
        sort_order=0,
        file_name="差旅费报销单.xlsx",
        reserved_bytes=70,
        expires_at=utc_now() + timedelta(minutes=1),
    )
    reclaim_time = utc_now() + timedelta(minutes=2)

    assert coordinator.reclaim_expired(now=reclaim_time) == 0
    assert coordinator.usage().reserved_bytes == 70

    with Session(engine) as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        assert submission is not None
        submission.lease_expires_at = reclaim_time - timedelta(seconds=1)
        database.commit()

    assert coordinator.reclaim_expired(now=reclaim_time) == 1
    assert coordinator.usage().reserved_bytes == 0
    with Session(engine) as database:
        upload = database.get(ReimbursementUpload, reservation.record_id)
        assert upload is not None
        assert upload.local_status == ReimbursementUploadLocalStatus.DELETED.value
        assert upload.upload_status == "DISCARDED"
        assert upload.local_deleted_at == reclaim_time

    engine.dispose()


def test_restart_rebuilds_usage_and_deduplicates_an_original_upload_manifest(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'quota.db'}"
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as database:
        draft = _new_draft()
        database.add(draft)
        database.commit()
        draft_id = draft.id
    staging = ReimbursementStaging(
        (tmp_path / "staging").resolve(),
        max_object_bytes=100,
    )
    staging.prepare()
    coordinator = ReimbursementQuotaCoordinator(engine, staging, max_bytes=100)
    owner = DraftFileOwner("corp-test", "employee-1", draft_id, 1)
    reservation = coordinator.reserve_draft_file(
        owner,
        sort_order=0,
        processing_role=ReimbursementDraftFileRole.EXPENSE_SOURCE,
        original_name="发票.pdf",
        extension="pdf",
        media_type="application/pdf",
        reserved_bytes=60,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    coordinator.mark_writing(owner, reservation)
    staged = staging.write_bytes(reservation.staging, b"source-document")
    coordinator.finalize(owner, reservation, staged)

    with Session(engine) as database:
        draft = database.get(ReimbursementDraft, draft_id)
        source = database.get(ReimbursementDraftFile, reservation.record_id)
        assert draft is not None and source is not None
        submission = _new_generating_submission(draft)
        database.add(submission)
        database.flush()
        database.add(
            ReimbursementUpload(
                submission_id=submission.id,
                draft_id=draft.id,
                source_draft_file_id=source.id,
                role=ReimbursementUploadRole.ORIGINAL.value,
                sort_order=0,
                local_storage_key=source.storage_key,
                local_part_storage_key=None,
                local_status=ReimbursementUploadLocalStatus.READY.value,
                reserved_bytes=source.reserved_bytes,
                reservation_expires_at=None,
                file_name=source.original_name,
                file_type=source.extension,
                media_type=source.media_type,
                size_bytes=source.size_bytes,
                sha256=source.sha256,
                upload_status="PENDING",
                status_version=1,
                attempt_count=0,
            )
        )
        database.commit()
        submission_id = submission.id
    engine.dispose()

    restarted_engine = create_database_engine(database_url)
    restarted = ReimbursementQuotaCoordinator(restarted_engine, staging, max_bytes=100)
    assert restarted.usage().reserved_bytes == 60
    lease = SubmissionLease(
        "corp-test",
        "employee-1",
        submission_id,
        "lease-token-1",
        3,
    )
    with pytest.raises(ReimbursementQuotaExceeded):
        restarted.reserve_generated_upload(
            lease,
            sort_order=1,
            file_name="差旅费报销单.xlsx",
            reserved_bytes=50,
            expires_at=utc_now() + timedelta(minutes=5),
        )
    with Session(restarted_engine) as database:
        uploads = database.scalars(
            select(ReimbursementUpload).where(
                ReimbursementUpload.role == ReimbursementUploadRole.GENERATED_EXCEL.value
            )
        ).all()
        assert uploads == []
    assert not list((tmp_path / "staging").rglob("*.xlsx"))

    restarted_engine.dispose()
