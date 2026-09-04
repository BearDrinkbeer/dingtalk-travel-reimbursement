from __future__ import annotations

import asyncio
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, timedelta

import pytest
from conftest import mock_login
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.errors import ApiError
from app.database.base import Base
from app.integrations.dingtalk.storage import (
    ApprovalAttachment,
    ApprovalFile,
    ApprovalSpace,
    AttachmentProbe,
    AttachmentRecycleOutcome,
    DingTalkStorageCommitOutcomeUnknown,
    DingTalkStorageUploadError,
)
from app.integrations.dingtalk.workflow import (
    CreatedProcessInstance,
    CreateProcessInstanceCommand,
    FormComponent,
    FormOption,
    FormSchema,
    RelatedTemplatePolicy,
    WorkflowFormValue,
    WorkflowInstanceIdPage,
    WorkflowProcessInstance,
    schema_fingerprint,
)
from app.main import create_app
from app.models.reimbursement import (
    ReimbursementDraft,
    ReimbursementDraftFile,
    ReimbursementDraftFileRole,
    ReimbursementDraftFileStatus,
    ReimbursementDraftRelatedApproval,
    ReimbursementDraftStatus,
    ReimbursementOcrStatus,
    ReimbursementSubmission,
    ReimbursementSubmissionStatus,
    ReimbursementUpload,
    ReimbursementUploadLocalStatus,
    ReimbursementUploadRole,
    ReimbursementUploadStatus,
    utc_now,
)
from app.schemas.reimbursements import ReimbursementDraftInput
from app.services.oa_reimbursement import (
    DatabaseSubmissionState,
    DurableOAReimbursementWorker,
    LinkedLocalFileMaintenance,
    OAReimbursementProcessor,
    SnapshotSubmissionMaterializer,
)
from app.services.oa_template_profiles import (
    TravelProfileConfirmation,
    confirm_template_catalog,
    require_submission_ready_catalog,
)
from app.services.reimbursement_drafts import validate_and_calculate_input
from app.services.reimbursement_staging import StagingArea

REIMBURSEMENT_PROCESS_CODE = "PROC-INTEGRATION-REIMBURSEMENT"
TRAVEL_PROCESS_CODE = "PROC-INTEGRATION-TRAVEL"
TRAVEL_INSTANCE_ID = "travel-instance-1"
TRAVEL_INSTANCE_ID_2 = "travel-instance-2"
OA_INSTANCE_ID = "oa-instance-1"
OA_BUSINESS_ID = "OA-202609040001"
MAPPINGS = {
    "company": "company-id",
    "budgetCode": "budget-id",
    "travelType": "travel-type-id",
    "startDate": "start-id",
    "endDate": "end-id",
    "durationDays": "duration-id",
    "description": "description-id",
    "reimbursementAmount": "amount-id",
    "relatedApprovals": "related-id",
    "attachments": "attachment-id",
}


def _component(
    component_id: str,
    component_type: str,
    label: str,
    *,
    options: tuple[FormOption, ...] = (),
    related_policy: RelatedTemplatePolicy | None = None,
) -> FormComponent:
    return FormComponent(
        component_id=component_id,
        component_type=component_type,
        label=label,
        biz_alias=f"alias-{component_id}",
        required=True,
        disabled=False,
        hidden=False,
        ancestor_disabled=False,
        ancestor_hidden=False,
        nested=False,
        in_subtable=False,
        unsupported_container_ancestor=False,
        value_format="yyyy-MM-dd" if component_type == "DDDateField" else None,
        unit=None,
        options=options,
        parent_component_id=None,
        related_template_policy=related_policy,
    )


def _schema(process_code: str, components: tuple[FormComponent, ...]) -> FormSchema:
    modified_at = "2026-09-04T00:00:00Z"
    status = "PUBLISHED"
    form_uuid = f"uuid-{process_code}"
    return FormSchema(
        process_code=process_code,
        form_code=process_code,
        form_uuid=form_uuid,
        modified_at=modified_at,
        status=status,
        template_name=f"{process_code}申请",
        title=f"{process_code}申请",
        components=components,
        fingerprint=schema_fingerprint(
            process_code,
            process_code,
            form_uuid,
            modified_at,
            status,
            components,
        ),
    )


def _schemas() -> tuple[FormSchema, FormSchema, FormOption]:
    company = FormOption(value="北京", label="北京公司", key="company-beijing")
    budget = FormOption(value="26007 项目", label="26007 项目", key="budget-26007")
    travel_type = FormOption(
        value="市外项目出差（短期）",
        label="市外项目出差（短期）",
        key="short-project",
    )
    reimbursement = _schema(
        REIMBURSEMENT_PROCESS_CODE,
        (
            _component("company-id", "DDSelectField", "所属公司", options=(company,)),
            _component("budget-id", "DDSelectField", "预算代码", options=(budget,)),
            _component(
                "travel-type-id",
                "DDSelectField",
                "出差类别",
                options=(travel_type,),
            ),
            _component("start-id", "DDDateField", "开始时间"),
            _component("end-id", "DDDateField", "结束时间"),
            _component("duration-id", "NumberField", "时长（天）"),
            _component("description-id", "TextareaField", "明细说明"),
            _component("amount-id", "NumberField", "报销金额"),
            _component(
                "related-id",
                "RelateField",
                "关联审批单",
                related_policy=RelatedTemplatePolicy(
                    mode="RESTRICTED",
                    process_codes=(TRAVEL_PROCESS_CODE,),
                ),
            ),
            _component("attachment-id", "DDAttachment", "附件"),
        ),
    )
    travel = _schema(
        TRAVEL_PROCESS_CODE,
        (
            _component("travel-start-id", "DDDateField", "开始日期"),
            _component("travel-end-id", "DDDateField", "结束日期"),
        ),
    )
    return reimbursement, travel, travel_type


class LocalWorkflowBoundary:
    def __init__(
        self,
        reimbursement: FormSchema,
        travel: FormSchema,
        *,
        membership_pages: tuple[tuple[str, ...], ...] | None = None,
        advance_validation_clock=None,
    ) -> None:
        self.schemas = {
            reimbursement.process_code: reimbursement,
            travel.process_code: travel,
        }
        self.created_command: CreateProcessInstanceCommand | None = None
        self.create_calls = 0
        self.query_calls = 0
        self.travel_detail_calls = 0
        self.membership_pages = membership_pages or ((TRAVEL_INSTANCE_ID,),)
        self.advance_validation_clock = advance_validation_clock

    async def get_form_schema(self, process_code: str) -> FormSchema:
        if self.advance_validation_clock is not None:
            self.advance_validation_clock(timedelta(seconds=2))
        return self.schemas[process_code]

    async def list_process_instance_ids(
        self,
        *,
        process_code: str,
        next_token: int,
        **_kwargs,
    ):
        assert process_code == TRAVEL_PROCESS_CODE
        self.query_calls += 1
        if self.advance_validation_clock is not None:
            self.advance_validation_clock(timedelta(seconds=2))
        instance_ids = self.membership_pages[next_token]
        following = next_token + 1
        return WorkflowInstanceIdPage(
            instance_ids,
            following if following < len(self.membership_pages) else None,
        )

    async def create_process_instance(
        self,
        command: CreateProcessInstanceCommand,
    ) -> CreatedProcessInstance:
        self.create_calls += 1
        assert self.created_command is None
        self.created_command = command
        return CreatedProcessInstance(OA_INSTANCE_ID)

    async def get_process_instance(self, instance_id: str) -> WorkflowProcessInstance:
        if instance_id in {TRAVEL_INSTANCE_ID, TRAVEL_INSTANCE_ID_2}:
            self.travel_detail_calls += 1
            if self.advance_validation_clock is not None:
                self.advance_validation_clock(timedelta(seconds=2))
            return WorkflowProcessInstance(
                instance_id=instance_id,
                title="境内出差申请",
                business_id=f"TRAVEL-{instance_id}",
                originator_user_id="mock-user",
                originator_department_id="100",
                status="COMPLETED",
                result="agree",
                created_at="2026-08-20T01:02:03Z",
                finished_at="2026-08-20T02:02:03Z",
                form_values=(
                    WorkflowFormValue(
                        component_id="travel-start-id",
                        name="开始日期",
                        component_type="DDDateField",
                        value="2026-09-01",
                        ext_value=None,
                        biz_alias="alias-travel-start-id",
                    ),
                    WorkflowFormValue(
                        component_id="travel-end-id",
                        name="结束日期",
                        component_type="DDDateField",
                        value="2026-09-03",
                        ext_value=None,
                        biz_alias="alias-travel-end-id",
                    ),
                ),
            )
        assert instance_id == OA_INSTANCE_ID
        assert self.created_command is not None
        command = self.created_command
        return WorkflowProcessInstance(
            instance_id=OA_INSTANCE_ID,
            title="差旅费报销申请",
            business_id=OA_BUSINESS_ID,
            originator_user_id=command.originator_user_id,
            originator_department_id=str(command.department_id),
            status="RUNNING",
            result=None,
            created_at="2026-09-04T08:00:01Z",
            finished_at=None,
            form_values=tuple(
                WorkflowFormValue(
                    component_id=value.component_id,
                    name=value.name,
                    component_type=value.component_type,
                    value=value.value,
                    ext_value=None,
                    biz_alias=value.biz_alias,
                )
                for value in command.form_values
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalPreparedUpload:
    file: ApprovalFile


class LocalStorageBoundary:
    def __init__(
        self,
        *,
        auto_rename_first: bool = False,
        reject_put_at: int | None = None,
        rejected_commits: int = 0,
        unknown_commit_at: int | None = None,
    ) -> None:
        self.put_files: list[ApprovalFile] = []
        self.committed: list[ApprovalAttachment] = []
        self.auto_rename_first = auto_rename_first
        self.reject_put_at = reject_put_at
        self.rejected_commits = rejected_commits
        self.unknown_commit_at = unknown_commit_at
        self.commit_calls = 0
        self.probed: list[ApprovalAttachment] = []
        self.recycled: list[ApprovalAttachment] = []

    async def get_approval_space(self, user_id: str) -> ApprovalSpace:
        assert user_id == "mock-user"
        return ApprovalSpace("space-integration")

    async def prepare_upload(
        self,
        *,
        space: ApprovalSpace,
        union_id: str,
        file: ApprovalFile,
    ) -> LocalPreparedUpload:
        assert space.space_id == "space-integration"
        assert union_id == "mock-union-id:mock-user"
        return LocalPreparedUpload(file)

    async def put_prepared_upload(self, *, prepared: LocalPreparedUpload) -> None:
        self.put_files.append(prepared.file)
        if len(self.put_files) == self.reject_put_at:
            raise DingTalkStorageUploadError(http_status=503)

    async def commit_prepared_upload(
        self,
        *,
        prepared: LocalPreparedUpload,
    ) -> ApprovalAttachment:
        assert prepared.file is self.put_files[-1]
        self.commit_calls += 1
        if self.commit_calls == self.unknown_commit_at:
            raise DingTalkStorageCommitOutcomeUnknown(http_status=None)
        if self.rejected_commits:
            self.rejected_commits -= 1
            raise ApiError(
                "DINGTALK_STORAGE_COMMIT_REJECTED",
                "commit was definitively rejected",
                400,
            )
        committed_name = prepared.file.file_name
        if self.auto_rename_first and not self.committed:
            stem, extension = committed_name.rsplit(".", 1)
            committed_name = f"{stem}(1).{extension}"
        attachment = ApprovalAttachment(
            space_id="space-integration",
            file_id=f"remote-file-{len(self.committed)}",
            file_name=committed_name,
            file_size=prepared.file.size,
            file_type=prepared.file.file_type,
        )
        self.committed.append(attachment)
        return attachment

    async def probe_attachment(
        self,
        *,
        union_id: str,
        expected: ApprovalAttachment,
    ) -> AttachmentProbe:
        assert union_id == "mock-union-id:mock-user"
        self.probed.append(expected)
        return AttachmentProbe.EXACT

    async def recycle_attachment(
        self,
        *,
        union_id: str,
        attachment: ApprovalAttachment,
    ) -> AttachmentRecycleOutcome:
        assert union_id == "mock-union-id:mock-user"
        self.recycled.append(attachment)
        return AttachmentRecycleOutcome.CLEANED


def _draft_input(*, source_file_id: str | None = None) -> ReimbursementDraftInput:
    return ReimbursementDraftInput.model_validate(
        {
            "ocrDispositionVersion": 1,
            "companyValue": "北京",
            "budgetCodeValue": "26007 项目",
            "project": {"mode": "manual", "text": "26007 MES 项目"},
            "trip": {
                "tripType": "project",
                "startDate": "2026-09-01",
                "startTime": "08:00",
                "endDate": "2026-09-03",
                "endTime": "18:00",
            },
            "items": [
                {
                    "category": "local_transport",
                    "date": "2026-09-01",
                    "displayDate": "9月1日",
                    "description": "机场到酒店",
                    "amount": "44.89",
                    "receiptCount": 2,
                    **({"sourceFileId": source_file_id} if source_file_id else {}),
                },
                {
                    "category": "rail_fare",
                    "date": "2026-09-03",
                    "displayDate": "9月3日",
                    "description": "北京南-合肥南",
                    "amount": "473.50",
                    "receiptCount": 1,
                },
            ],
            "dismissedOcrFileIds": [],
        }
    )


def _persist_ready_draft(
    client,
    workflow: LocalWorkflowBoundary,
    travel_type: FormOption,
    *,
    travel_instance_ids: tuple[str, ...] = (TRAVEL_INSTANCE_ID,),
):
    settings = client.app.state.settings
    now = utc_now()
    with client.app.state.database_session_factory() as database:
        reimbursement_schema = workflow.schemas[REIMBURSEMENT_PROCESS_CODE]
        travel_schema = workflow.schemas[TRAVEL_PROCESS_CODE]
        asyncio.run(
            confirm_template_catalog(
                database,
                workflow,
                reimbursement_process_code=REIMBURSEMENT_PROCESS_CODE,
                expected_config_version=None,
                reimbursement_schema_fingerprint=reimbursement_schema.fingerprint,
                reimbursement_mappings=MAPPINGS,
                travel_profiles=[
                    TravelProfileConfirmation(
                        profile_key="domestic",
                        display_name="境内出差",
                        process_code=TRAVEL_PROCESS_CODE,
                        schema_fingerprint=travel_schema.fingerprint,
                        mappings={
                            "startDate": "travel-start-id",
                            "endDate": "travel-end-id",
                        },
                        travel_type_option=travel_type,
                    )
                ],
                related_approval_smoke_test_confirmed=True,
                administrator_user_id="mock-user",
            )
        )
        catalog = require_submission_ready_catalog(database)
        calculation = validate_and_calculate_input(
            database,
            catalog=catalog,
            draft_input=_draft_input(),
            max_items=settings.expense_max_items,
            validate_project=True,
        )
        draft = ReimbursementDraft(
            corp_id="corp-fixed",
            owner_user_id="mock-user",
            status=ReimbursementDraftStatus.REVIEW_READY.value,
            revision=4,
            department_id="100",
            department_name="测试部门",
            template_process_code=REIMBURSEMENT_PROCESS_CODE,
            template_config_version=catalog.config_version,
            schema_fingerprint=reimbursement_schema.fingerprint,
            input_json=calculation.canonical_json,
            related_instance_ids_json=json.dumps(list(travel_instance_ids)),
            expires_at=now + timedelta(days=7),
        )
        database.add(draft)
        database.flush()
        database.add_all(
            [
                ReimbursementDraftRelatedApproval(
                    draft_id=draft.id,
                    corp_id=draft.corp_id,
                    owner_user_id=draft.owner_user_id,
                    sort_order=sort_order,
                    process_instance_id=instance_id,
                    travel_profile_key="domestic",
                    process_code=TRAVEL_PROCESS_CODE,
                    catalog_config_version=catalog.config_version,
                    travel_schema_fingerprint=travel_schema.fingerprint,
                    listed_from_ms=1_780_000_000_000,
                    listed_to_ms=1_790_000_000_000,
                    travel_start_date=date(2026, 9, 1),
                    travel_end_date=date(2026, 9, 3),
                    title="境内出差申请",
                    business_id=f"TRAVEL-{instance_id}",
                    instance_created_at=now,
                    verified_at=now,
                )
                for sort_order, instance_id in enumerate(travel_instance_ids)
            ]
        )
        files: list[ReimbursementDraftFile] = []
        for sort_order, role, name, extension, media_type, content in (
            (3, "EXPENSE_SOURCE", "发票.pdf", "pdf", "application/pdf", b"%PDF-invoice"),
            (8, "ATTACHMENT_ONLY", "行程单.png", "png", "image/png", b"PNG-itinerary"),
        ):
            reservation = client.app.state.reimbursement_staging.new_reservation(
                StagingArea.DRAFTS,
                draft.id,
                extension,
                reserved_bytes=len(content),
            )
            staged = client.app.state.reimbursement_staging.write_bytes(
                reservation,
                content,
                expected_size=len(content),
                expected_sha256=hashlib.sha256(content).hexdigest(),
            )
            files.append(
                ReimbursementDraftFile(
                    draft_id=draft.id,
                    sort_order=sort_order,
                    processing_role=role,
                    file_status=ReimbursementDraftFileStatus.ACTIVE.value,
                    storage_key=staged.storage_key,
                    part_storage_key=None,
                    reserved_bytes=staged.size_bytes,
                    reservation_expires_at=None,
                    original_name=name,
                    extension=extension,
                    media_type=media_type,
                    size_bytes=staged.size_bytes,
                    sha256=staged.sha256,
                    ocr_status=(
                        ReimbursementOcrStatus.COMPLETE.value
                        if role == ReimbursementDraftFileRole.EXPENSE_SOURCE.value
                        else ReimbursementOcrStatus.NOT_REQUESTED.value
                    ),
                    ocr_result_json=(
                        "{}"
                        if role == ReimbursementDraftFileRole.EXPENSE_SOURCE.value
                        else None
                    ),
                )
            )
        database.add_all(files)
        database.flush()
        linked_calculation = validate_and_calculate_input(
            database,
            catalog=catalog,
            draft_input=_draft_input(source_file_id=files[0].id),
            max_items=settings.expense_max_items,
            validate_project=True,
        )
        draft.input_json = linked_calculation.canonical_json
        database.commit()
        return draft.id, tuple(item.id for item in files)


def _worker(
    client,
    workflow: LocalWorkflowBoundary,
    storage: LocalStorageBoundary,
    *,
    clock=None,
    lease_seconds: int = 180,
):
    settings = client.app.state.settings
    worker_id = "phase6d-integration-worker"
    materializer = SnapshotSubmissionMaterializer(
        workflow=workflow,
        staging=client.app.state.reimbursement_staging,
        excel_template_path=settings.excel_template_path,
        approval_url_factory=settings.dingtalk_approval_url,
    )
    state = DatabaseSubmissionState(
        session_factory=client.app.state.database_session_factory,
        worker_id=worker_id,
        quota=client.app.state.reimbursement_quota,
        staging=client.app.state.reimbursement_staging,
        materializer=materializer,
        generated_reservation_seconds=300,
    )
    processor = OAReimbursementProcessor(
        state=state,
        materializer=materializer,
        workflow=workflow,
        storage=storage,
        lease_seconds=lease_seconds,
        clock=clock,
    )
    maintenance = LinkedLocalFileMaintenance(
        session_factory=client.app.state.database_session_factory,
        staging=client.app.state.reimbursement_staging,
    )
    return DurableOAReimbursementWorker(
        worker_id=worker_id,
        state=state,
        processor=processor,
        maintenance=maintenance,
        lease_seconds=lease_seconds,
        poll_interval_seconds=0.01,
        clock=clock,
    )


def test_validation_shares_membership_pages_across_related_approvals(
    client_factory,
) -> None:
    reimbursement_schema, travel_schema, travel_type = _schemas()
    clock_value = [utc_now()]

    def advance_validation_clock(delta: timedelta) -> None:
        clock_value[0] += delta

    workflow = LocalWorkflowBoundary(
        reimbursement_schema,
        travel_schema,
        membership_pages=(
            ("unrelated-travel-instance",),
            (TRAVEL_INSTANCE_ID, TRAVEL_INSTANCE_ID_2),
        ),
        advance_validation_clock=advance_validation_clock,
    )
    storage = LocalStorageBoundary()
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    draft_id, _source_file_ids = _persist_ready_draft(
        client,
        workflow,
        travel_type,
        travel_instance_ids=(TRAVEL_INSTANCE_ID, TRAVEL_INSTANCE_ID_2),
    )
    workflow.query_calls = 0
    workflow.travel_detail_calls = 0

    submitted = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "66666666-6666-4666-8666-666666666666",
        },
    )
    assert submitted.status_code == 202, submitted.text

    worker = _worker(
        client,
        workflow,
        storage,
        clock=lambda: clock_value[0],
        lease_seconds=5,
    )
    assert asyncio.run(worker.run_once()) is True

    polled = client.get(
        f"/api/oa/reimbursements/submissions/{submitted.json()['data']['submissionId']}"
    )
    assert polled.status_code == 200
    assert polled.json()["data"]["status"] == ReimbursementSubmissionStatus.SUBMITTED.value
    assert workflow.query_calls == 2
    assert workflow.travel_detail_calls == 2
    assert workflow.create_calls == 1
    assert asyncio.run(worker.run_once()) is False
    assert workflow.query_calls == 2
    assert workflow.travel_detail_calls == 2


def test_submit_worker_readback_and_cleanup_are_one_durable_local_flow(
    client_factory,
) -> None:
    reimbursement_schema, travel_schema, travel_type = _schemas()
    workflow = LocalWorkflowBoundary(reimbursement_schema, travel_schema)
    storage = LocalStorageBoundary(auto_rename_first=True)
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    draft_id, source_file_ids = _persist_ready_draft(client, workflow, travel_type)
    first_key = "11111111-1111-4111-8111-111111111111"

    submitted = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": first_key},
    )
    assert submitted.status_code == 202, submitted.text
    submission_id = submitted.json()["data"]["submissionId"]
    same_request = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": first_key},
    )
    assert same_request.status_code == 202
    assert same_request.json()["data"]["submissionId"] == submission_id

    assert asyncio.run(_worker(client, workflow, storage).run_once()) is True

    polled = client.get(f"/api/oa/reimbursements/submissions/{submission_id}")
    assert polled.status_code == 200, polled.text
    assert {
        "submissionId": submission_id,
        "status": ReimbursementSubmissionStatus.SUBMITTED.value,
        "processInstanceId": OA_INSTANCE_ID,
        "businessId": OA_BUSINESS_ID,
    }.items() <= polled.json()["data"].items()
    refreshed = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 999},
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "22222222-2222-4222-8222-222222222222",
        },
    )
    assert refreshed.status_code == 202, refreshed.text
    assert refreshed.json()["data"]["submissionId"] == submission_id
    assert workflow.create_calls == 1

    assert workflow.created_command is not None
    attachment_field = workflow.created_command.form_values[-1]
    assert attachment_field.component_type == "DDAttachment"
    attachment_values = json.loads(attachment_field.value)
    assert [item["fileId"] for item in attachment_values] == [
        "remote-file-0",
        "remote-file-1",
        "remote-file-2",
    ]
    assert [item["fileName"] for item in attachment_values[:2]] == [
        "发票(1).pdf",
        "行程单.png",
    ]
    assert attachment_values[-1]["fileName"].endswith(".xlsx")
    assert [item.file_name for item in storage.put_files[:2]] == ["发票.pdf", "行程单.png"]
    load_workbook(io.BytesIO(storage.put_files[-1].content))

    with client.app.state.database_session_factory() as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        uploads = database.scalars(
            select(ReimbursementUpload)
            .where(ReimbursementUpload.submission_id == submission_id)
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        assert submission is not None
        assert submission.status == ReimbursementSubmissionStatus.SUBMITTED.value
        assert submission.approval_url.startswith("dingtalk://dingtalkclient/action/openapp?")
        assert [item.role for item in uploads] == [
            ReimbursementUploadRole.ORIGINAL.value,
            ReimbursementUploadRole.ORIGINAL.value,
            ReimbursementUploadRole.GENERATED_EXCEL.value,
        ]
        assert [item.sort_order for item in uploads] == [0, 1, 2]
        assert [item.file_name for item in uploads[:2]] == [
            "发票(1).pdf",
            "行程单.png",
        ]
        assert {item.upload_status for item in uploads} == {
            ReimbursementUploadStatus.LINKED.value
        }
        assert {item.local_status for item in uploads} == {
            ReimbursementUploadLocalStatus.READY.value
        }
        local_paths = tuple(
            client.app.state.reimbursement_staging.root / item.local_storage_key
            for item in uploads
        )
        assert all(path.is_file() for path in local_paths)

    assert asyncio.run(_worker(client, workflow, storage).run_once()) is False

    with client.app.state.database_session_factory() as database:
        uploads = database.scalars(
            select(ReimbursementUpload)
            .where(ReimbursementUpload.submission_id == submission_id)
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        sources = tuple(database.get(ReimbursementDraftFile, item) for item in source_file_ids)
        assert {item.upload_status for item in uploads} == {
            ReimbursementUploadStatus.LINKED.value
        }
        assert {item.local_status for item in uploads} == {
            ReimbursementUploadLocalStatus.DELETED.value
        }
        assert all(source is not None for source in sources)
        assert {source.file_status for source in sources if source is not None} == {
            ReimbursementDraftFileStatus.PURGED.value
        }
        assert all(source.purged_at is not None for source in sources if source is not None)
    assert all(not path.exists() for path in local_paths)
    assert workflow.create_calls == 1


def test_transient_put_failure_retries_on_a_later_worker_claim(client_factory) -> None:
    reimbursement_schema, travel_schema, travel_type = _schemas()
    workflow = LocalWorkflowBoundary(reimbursement_schema, travel_schema)
    storage = LocalStorageBoundary(auto_rename_first=True, reject_put_at=2)
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    draft_id, _source_file_ids = _persist_ready_draft(client, workflow, travel_type)
    response = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "33333333-3333-4333-8333-333333333333",
        },
    )
    assert response.status_code == 202, response.text
    submission_id = response.json()["data"]["submissionId"]
    clock_value = [utc_now()]
    worker = _worker(client, workflow, storage, clock=lambda: clock_value[0])

    assert asyncio.run(worker.run_once()) is True

    with client.app.state.database_session_factory() as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        uploads = database.scalars(
            select(ReimbursementUpload)
            .where(ReimbursementUpload.submission_id == submission_id)
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        assert submission is not None
        assert [item.upload_status for item in uploads] == [
            ReimbursementUploadStatus.COMMITTED.value,
            ReimbursementUploadStatus.PUTTING.value,
            ReimbursementUploadStatus.PENDING.value,
        ]
        assert uploads[0].file_name == "发票(1).pdf"
        assert uploads[1].put_started_at is not None
        assert uploads[1].commit_started_at is None
        assert submission.status == ReimbursementSubmissionStatus.FAILED_RETRYABLE.value
    assert workflow.create_calls == 0

    clock_value[0] += timedelta(seconds=10)
    assert asyncio.run(worker.run_once()) is True

    polled = client.get(f"/api/oa/reimbursements/submissions/{submission_id}")
    assert polled.status_code == 200
    assert polled.json()["data"]["status"] == ReimbursementSubmissionStatus.SUBMITTED.value
    assert storage.commit_calls == 3
    assert [item.file_name for item in storage.put_files[:3]] == [
        "发票.pdf",
        "行程单.png",
        "行程单.png",
    ]
    assert [(item.file_id, item.file_name) for item in storage.probed] == [
        ("remote-file-0", "发票(1).pdf")
    ]
    assert workflow.create_calls == 1


@pytest.mark.parametrize(
    ("damage", "expected_error_code"),
    (
        ("missing", "REIMBURSEMENT_LOCAL_FILE_MISSING"),
        ("corrupt", "REIMBURSEMENT_LOCAL_FILE_CHANGED"),
    ),
)
def test_damaged_locked_file_after_one_commit_cleans_orphan_and_never_retries(
    client_factory,
    damage: str,
    expected_error_code: str,
) -> None:
    reimbursement_schema, travel_schema, travel_type = _schemas()
    workflow = LocalWorkflowBoundary(reimbursement_schema, travel_schema)
    storage = LocalStorageBoundary(auto_rename_first=True)
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    draft_id, _source_file_ids = _persist_ready_draft(client, workflow, travel_type)
    response = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": (
                "44444444-4444-4444-8444-444444444444"
                if damage == "missing"
                else "44444444-4444-4444-8444-444444444445"
            ),
        },
    )
    assert response.status_code == 202, response.text
    submission_id = response.json()["data"]["submissionId"]

    with client.app.state.database_session_factory() as database:
        originals = database.scalars(
            select(ReimbursementUpload)
            .where(
                ReimbursementUpload.submission_id == submission_id,
                ReimbursementUpload.role == ReimbursementUploadRole.ORIGINAL.value,
            )
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        damaged = originals[1]
        if damage == "missing":
            assert client.app.state.reimbursement_staging.delete(
                damaged.local_storage_key,
                expected_size=damaged.size_bytes,
                expected_sha256=damaged.sha256,
            )
        else:
            damaged_path = (
                client.app.state.reimbursement_staging.root
                / damaged.local_storage_key
            )
            damaged_path.write_bytes(b"corrupted locked attachment")

    worker = _worker(client, workflow, storage)
    assert asyncio.run(worker.run_once()) is True

    with client.app.state.database_session_factory() as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        uploads = database.scalars(
            select(ReimbursementUpload)
            .where(ReimbursementUpload.submission_id == submission_id)
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        assert submission is not None
        assert submission.status == ReimbursementSubmissionStatus.FAILED_FINAL.value
        assert [item.upload_status for item in uploads] == [
            ReimbursementUploadStatus.CLEANED.value,
            ReimbursementUploadStatus.PENDING.value,
            ReimbursementUploadStatus.PENDING.value,
        ]
        assert submission.last_error_code == expected_error_code
    assert [
        (item.file_id, item.file_name) for item in storage.recycled
    ] == [("remote-file-0", "发票(1).pdf")]
    assert workflow.create_calls == 0
    first_round = (len(storage.put_files), storage.commit_calls, len(storage.recycled))

    assert asyncio.run(worker.run_once()) is False
    assert (len(storage.put_files), storage.commit_calls, len(storage.recycled)) == first_round


def test_unknown_second_commit_never_cleans_or_retries_remote_files(
    client_factory,
) -> None:
    reimbursement_schema, travel_schema, travel_type = _schemas()
    workflow = LocalWorkflowBoundary(reimbursement_schema, travel_schema)
    storage = LocalStorageBoundary(unknown_commit_at=2)
    client = client_factory(auth_mock_enabled=True)
    csrf = str(mock_login(client)["csrfToken"])
    draft_id, _source_file_ids = _persist_ready_draft(client, workflow, travel_type)
    response = client.post(
        f"/api/oa/reimbursements/{draft_id}/submit",
        json={"expectedRevision": 4},
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": "55555555-5555-4555-8555-555555555555",
        },
    )
    assert response.status_code == 202, response.text
    submission_id = response.json()["data"]["submissionId"]
    worker = _worker(client, workflow, storage)

    assert asyncio.run(worker.run_once()) is True

    with client.app.state.database_session_factory() as database:
        submission = database.get(ReimbursementSubmission, submission_id)
        uploads = database.scalars(
            select(ReimbursementUpload)
            .where(ReimbursementUpload.submission_id == submission_id)
            .order_by(ReimbursementUpload.sort_order)
        ).all()
        assert submission is not None
        assert submission.status == ReimbursementSubmissionStatus.MANUAL_REVIEW.value
        assert [item.upload_status for item in uploads] == [
            ReimbursementUploadStatus.COMMITTED.value,
            ReimbursementUploadStatus.COMMIT_UNCERTAIN.value,
            ReimbursementUploadStatus.PENDING.value,
        ]
    assert storage.recycled == []
    assert workflow.create_calls == 0
    first_round = (len(storage.put_files), storage.commit_calls)

    assert asyncio.run(worker.run_once()) is False
    assert (len(storage.put_files), storage.commit_calls) == first_round
    assert storage.recycled == []


def test_enabled_worker_lifespan_starts_and_stops_cleanly(
    settings_factory,
    monkeypatch,
) -> None:
    settings = settings_factory(
        dingtalk_oa_worker_enabled=True,
        dingtalk_oa_worker_poll_interval_seconds=0.1,
    )
    application = create_app(settings)
    Base.metadata.create_all(application.state.database_engine)
    lifecycle: list[str] = []

    async def observe_worker_lifecycle(stop: asyncio.Event) -> None:
        lifecycle.append("started")
        await stop.wait()
        lifecycle.append("stopped")

    monkeypatch.setattr(
        application.state.oa_reimbursement_worker,
        "run",
        observe_worker_lifecycle,
    )

    with TestClient(application) as client:
        assert client.get("/api/health").status_code == 200
        assert application.state.oa_reimbursement_worker is not None
        assert lifecycle == ["started"]

    assert lifecycle == ["started", "stopped"]


def test_credentialed_lifespan_does_not_start_worker_without_explicit_opt_in(
    settings_factory,
    monkeypatch,
) -> None:
    settings = settings_factory()
    assert settings.dingtalk_client_id
    assert settings.dingtalk_client_secret
    assert settings.dingtalk_oa_worker_enabled is False
    application = create_app(settings)
    Base.metadata.create_all(application.state.database_engine)
    lifecycle: list[str] = []

    async def observe_worker_lifecycle(_stop: asyncio.Event) -> None:
        lifecycle.append("started")

    monkeypatch.setattr(
        application.state.oa_reimbursement_worker,
        "run",
        observe_worker_lifecycle,
    )

    with TestClient(application) as client:
        assert client.get("/api/health").status_code == 200
        assert lifecycle == []

    assert lifecycle == []
