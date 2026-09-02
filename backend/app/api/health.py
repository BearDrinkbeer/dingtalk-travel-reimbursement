import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.request_id import request_id_for
from app.services.readiness import check_readiness

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, object]:
    return {"success": True, "data": {"status": "ok"}}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    report = await asyncio.to_thread(
        check_readiness,
        settings,
        request.app.state.database_engine,
    )
    data = {
        "status": "ready" if report.ready else "not_ready",
        "checks": report.checks,
    }
    if report.ready:
        return JSONResponse(content={"success": True, "data": data})
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "error": {
                "code": "SERVICE_NOT_READY",
                "message": "服务尚未就绪",
            },
            "data": data,
            "requestId": request_id_for(request),
        },
    )
