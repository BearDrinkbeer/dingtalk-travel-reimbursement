from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from time import perf_counter

import httpx
from fastapi import FastAPI, Request, Response

from app.api.auth import router as auth_router
from app.api.calculations import router as calculations_router
from app.api.excel import router as excel_router
from app.api.files import router as files_router
from app.api.health import router as health_router
from app.api.oa_templates import router as oa_templates_router
from app.api.ocr import router as ocr_router
from app.api.projects import router as projects_router
from app.api.receipt_keywords import router as receipt_keywords_router
from app.api.settings import router as settings_router
from app.core.config import Settings, get_settings
from app.core.errors import ApiError, error_response, install_error_handlers
from app.core.logging import configure_logging
from app.core.request_id import bind_request_id, request_id_from_header, reset_request_id
from app.database.session import create_database_engine, create_session_factory
from app.integrations.dingtalk.client import DingTalkOpenAPIClient
from app.integrations.dingtalk.storage import DingTalkStorageClient
from app.integrations.dingtalk.workflow import DingTalkWorkflowClient
from app.ocr.engine import FakeOcrEngine
from app.ocr.types import LocalOcrEngine
from app.services.dingtalk import DingTalkService
from app.services.file_coordination import SessionFileCoordinator
from app.services.multipart_uploads import prepare_spool_directory
from app.services.ocr_service import OcrService
from app.services.process_jobs import KillableProcessRunner
from app.services.reimbursement_staging import ReimbursementStaging
from app.services.sessions import SessionCleanupGate, purge_expired_sessions
from app.services.temp_files import cleanup_expired_temp_files

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    dingtalk_transport: httpx.AsyncBaseTransport | None = None,
    dingtalk_upload_transport: httpx.AsyncBaseTransport | None = None,
    ocr_engine: LocalOcrEngine | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    if ocr_engine is not None and ocr_engine.is_fake and runtime_settings.app_env == "production":
        raise ValueError("A fake OCR engine cannot be injected in production")
    reimbursement_staging = ReimbursementStaging(
        runtime_settings.reimbursement_staging_dir,
        max_object_bytes=runtime_settings.upload_max_file_bytes,
    )
    database_engine = create_database_engine(runtime_settings.database_url)
    database_session_factory = create_session_factory(database_engine)
    dingtalk_client = DingTalkOpenAPIClient(runtime_settings, transport=dingtalk_transport)
    dingtalk_service = DingTalkService(dingtalk_client)
    dingtalk_workflow = DingTalkWorkflowClient(dingtalk_client)
    dingtalk_storage = DingTalkStorageClient(
        dingtalk_client,
        runtime_settings,
        upload_transport=dingtalk_upload_transport,
    )
    session_cleanup_gate = SessionCleanupGate(runtime_settings.session_cleanup_interval_seconds)
    file_coordinator = SessionFileCoordinator(
        file_wait_seconds=runtime_settings.file_operation_wait_seconds,
        upload_wait_seconds=runtime_settings.upload_admission_wait_seconds,
    )
    if runtime_settings.ocr_fake_enabled:
        ocr_engine = ocr_engine or FakeOcrEngine()
    process_runner = KillableProcessRunner(
        admission_timeout_seconds=runtime_settings.process_job_admission_wait_seconds
    )
    ocr_service = OcrService(runtime_settings, ocr_engine, process_runner)

    async def periodic_temp_cleanup(stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=runtime_settings.temp_cleanup_interval_seconds,
                )
            except TimeoutError:
                try:
                    await asyncio.to_thread(
                        cleanup_expired_temp_files,
                        runtime_settings,
                        file_coordinator,
                    )
                except Exception as exc:
                    logger.error(
                        "Periodic temporary-file cleanup failed",
                        extra={"exception_type": type(exc).__name__},
                    )

    async def stop_periodic_temp_cleanup(
        stop: asyncio.Event,
        task: asyncio.Task[None],
    ) -> None:
        stop.set()
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with AsyncExitStack() as resources:
            resources.callback(database_engine.dispose)
            resources.push_async_callback(dingtalk_client.close)
            resources.push_async_callback(dingtalk_storage.close)
            resources.push_async_callback(process_runner.close)
            resources.push_async_callback(ocr_service.close)

            Path(runtime_settings.temp_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
            prepare_spool_directory(runtime_settings)
            reimbursement_staging.prepare()
            cleanup_expired_temp_files(runtime_settings, file_coordinator)
            with database_session_factory() as database:
                purge_expired_sessions(database)
            session_cleanup_gate.mark_completed()
            await ocr_service.ensure_ready()
            cleanup_stop = asyncio.Event()
            cleanup_task = asyncio.create_task(periodic_temp_cleanup(cleanup_stop))
            resources.push_async_callback(
                stop_periodic_temp_cleanup,
                cleanup_stop,
                cleanup_task,
            )
            yield

    application = FastAPI(
        title="DingTalk Expense API",
        version="0.2.0",
        docs_url="/api/docs" if runtime_settings.app_env != "production" else None,
        openapi_url="/api/openapi.json" if runtime_settings.app_env != "production" else None,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.database_engine = database_engine
    application.state.database_session_factory = database_session_factory
    application.state.dingtalk_service = dingtalk_service
    application.state.dingtalk_client = dingtalk_client
    application.state.dingtalk_workflow = dingtalk_workflow
    application.state.dingtalk_storage = dingtalk_storage
    application.state.reimbursement_staging = reimbursement_staging
    application.state.session_cleanup_gate = session_cleanup_gate
    application.state.ocr_service = ocr_service
    application.state.process_runner = process_runner
    application.state.file_coordinator = file_coordinator

    @application.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = request_id_from_header(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        started_at = perf_counter()
        try:
            if request.method == "POST" and request.url.path == "/api/files/upload":
                declared_length = request.headers.get("Content-Length")
                if declared_length:
                    try:
                        if int(declared_length) > runtime_settings.upload_max_request_bytes:
                            return error_response(
                                request,
                                "REQUEST_TOO_LARGE",
                                "上传请求体超过限制",
                                413,
                            )
                    except ValueError:
                        return error_response(
                            request,
                            "MALFORMED_REQUEST",
                            "请求长度无效",
                            400,
                        )
                received_bytes = 0
                original_receive = request._receive

                async def limited_receive():
                    nonlocal received_bytes
                    message = await original_receive()
                    if message.get("type") == "http.request":
                        received_bytes += len(message.get("body", b""))
                        if received_bytes > runtime_settings.upload_max_request_bytes:
                            raise ApiError("REQUEST_TOO_LARGE", "上传请求体超过限制", 413)
                    return message

                request._receive = limited_receive
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
            )
            return response
        finally:
            reset_request_id(token)

    install_error_handlers(application)
    application.include_router(health_router, prefix="/api")
    application.include_router(auth_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    application.include_router(receipt_keywords_router, prefix="/api")
    application.include_router(settings_router, prefix="/api")
    application.include_router(calculations_router, prefix="/api")
    application.include_router(excel_router, prefix="/api")
    application.include_router(files_router, prefix="/api")
    application.include_router(ocr_router, prefix="/api")
    application.include_router(oa_templates_router, prefix="/api")
    return application


app = create_app()
