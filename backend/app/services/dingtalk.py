from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ApiError

TOKEN_INVALID_CODES = {40014, 42001}
PERMISSION_ERROR_CODES = {88, 43007, 60011, 60020}


@dataclass(frozen=True, slots=True)
class DepartmentIdentity:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class DingTalkIdentity:
    user_id: str
    name: str
    departments: tuple[DepartmentIdentity, ...]


class DingTalkService:
    """Small adapter around the organization-app login APIs.

    It owns the application token cache. Auth codes, access tokens and secrets are
    deliberately never passed to logging calls.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0),
            transport=transport,
        )
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    def evict_token(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0

    async def get_identity(self, auth_code: str) -> DingTalkIdentity:
        user_info = await self._oapi(
            "/topapi/v2/user/getuserinfo",
            {"code": auth_code},
            retry_invalid_token=True,
            retry_transient=False,
        )
        user_id = str(user_info.get("userid") or "").strip()
        if not user_id:
            raise self._safe_error()

        user = await self._oapi(
            "/topapi/v2/user/get",
            {"userid": user_id, "language": "zh_CN"},
            retry_invalid_token=True,
            retry_transient=True,
        )
        name = str(user.get("name") or "").strip()
        raw_department_ids = user.get("dept_id_list")
        if not name or not isinstance(raw_department_ids, list):
            raise self._safe_error()

        departments: list[DepartmentIdentity] = []
        seen: set[str] = set()
        for raw_id in raw_department_ids:
            department_id = str(raw_id).strip()
            if not department_id or department_id in seen:
                continue
            seen.add(department_id)
            department = await self._oapi(
                "/topapi/v2/department/get",
                {"dept_id": raw_id, "language": "zh_CN"},
                retry_invalid_token=True,
                retry_transient=True,
            )
            department_name = str(department.get("name") or "").strip()
            if department_name:
                departments.append(DepartmentIdentity(department_id, department_name))

        if not departments:
            raise ApiError(
                "DINGTALK_PERMISSION_MISSING",
                "无法读取所属部门，请联系管理员检查钉钉应用权限",
                502,
            )
        return DingTalkIdentity(user_id=user_id, name=name, departments=tuple(departments))

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._access_token and monotonic() < self._access_token_expires_at:
            return self._access_token

        async with self._token_lock:
            if (
                not force_refresh
                and self._access_token
                and monotonic() < self._access_token_expires_at
            ):
                return self._access_token
            url = f"https://api.dingtalk.com/v1.0/oauth2/{self._settings.dingtalk_corp_id}/token"
            payload = await self._request_json(
                "POST",
                url,
                json={
                    "client_id": self._settings.dingtalk_client_id,
                    "client_secret": self._settings.dingtalk_client_secret,
                    "grant_type": "client_credentials",
                },
            )
            access_token = str(
                payload.get("access_token") or payload.get("accessToken") or ""
            ).strip()
            expires_in = payload.get("expires_in", payload.get("expireIn", 7200))
            try:
                cache_seconds = max(1, int(expires_in) - 300)
            except (TypeError, ValueError):
                cache_seconds = 6900
            if not access_token:
                raise self._safe_error()
            self._access_token = access_token
            self._access_token_expires_at = monotonic() + cache_seconds
            return access_token

    async def _oapi(
        self,
        path: str,
        body: dict[str, Any],
        *,
        retry_invalid_token: bool,
        retry_transient: bool,
    ) -> dict[str, Any]:
        for token_attempt in range(2):
            token = await self._get_access_token(force_refresh=token_attempt == 1)
            payload = await self._request_json(
                "POST",
                f"https://oapi.dingtalk.com{path}",
                params={"access_token": token},
                json=body,
                accept_oapi_error=True,
                retry_transient=retry_transient,
            )
            errcode = self._error_code(payload)
            if errcode == 0:
                result = payload.get("result")
                return result if isinstance(result, dict) else payload
            if retry_invalid_token and errcode in TOKEN_INVALID_CODES and token_attempt == 0:
                self.evict_token()
                continue
            if errcode in PERMISSION_ERROR_CODES:
                raise ApiError(
                    "DINGTALK_PERMISSION_MISSING",
                    "钉钉应用权限不足，请联系管理员",
                    502,
                )
            raise self._safe_error()
        raise self._safe_error()

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        accept_oapi_error: bool = False,
        retry_transient: bool = True,
    ) -> dict[str, Any]:
        attempts = 3 if retry_transient else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, url, params=params, json=json)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.2 * (2**attempt))
                    continue
                raise self._safe_error() from None

            if response.status_code >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            if not 200 <= response.status_code < 300:
                raise self._safe_error()
            try:
                payload = response.json()
            except ValueError:
                raise self._safe_error() from None
            if not isinstance(payload, dict):
                raise self._safe_error()
            if accept_oapi_error and self._error_code(payload) == -1 and attempt + 1 < attempts:
                await asyncio.sleep(0.2 * (2**attempt))
                continue
            return payload
        raise self._safe_error()

    @staticmethod
    def _error_code(payload: dict[str, Any]) -> int:
        try:
            return int(payload.get("errcode", 0))
        except (TypeError, ValueError):
            return -2

    @staticmethod
    def _safe_error() -> ApiError:
        return ApiError(
            "DINGTALK_AUTH_FAILED",
            "钉钉身份验证失败，请从公司钉钉工作台重新进入",
            502,
        )
