from __future__ import annotations

import httpx
import pytest

from app.integrations.dingtalk.client import (
    DingTalkOpenAPIClient,
    DingTalkOpenAPIError,
)


@pytest.mark.asyncio
async def test_idempotent_get_retries_rate_limit_with_sanitized_error(
    settings_factory,
) -> None:
    calls = {"token": 0, "openapi": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "private-token"})
        calls["openapi"] += 1
        return httpx.Response(
            429,
            json={
                "code": "Throttling.RateLimit",
                "message": "private upstream diagnostic",
            },
        )

    client = DingTalkOpenAPIClient(
        settings_factory(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(DingTalkOpenAPIError) as caught:
            await client.request_openapi_json("GET", "/v1.0/example")
    finally:
        await client.close()

    error = caught.value
    assert calls == {"token": 1, "openapi": 3}
    assert error.status_code == 502
    assert error.code == "DINGTALK_OPENAPI_FAILED"
    assert error.http_status == 429
    assert error.upstream_code == "Throttling.RateLimit"
    assert "private" not in str(error)
    assert "private" not in repr(error)


@pytest.mark.asyncio
async def test_idempotent_get_retries_server_errors(settings_factory) -> None:
    calls = {"token": 0, "openapi": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "private-token"})
        calls["openapi"] += 1
        return httpx.Response(
            503,
            json={"code": "ServiceUnavailable", "message": "private failure detail"},
        )

    client = DingTalkOpenAPIClient(
        settings_factory(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(DingTalkOpenAPIError) as caught:
            await client.request_openapi_json("GET", "/v1.0/example")
    finally:
        await client.close()

    assert calls == {"token": 1, "openapi": 3}
    assert caught.value.http_status == 503
    assert caught.value.upstream_code == "ServiceUnavailable"
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", ["timeout", "transport"])
async def test_idempotent_get_retries_transport_failures_without_retaining_details(
    settings_factory,
    failure_type: str,
) -> None:
    calls = {"token": 0, "openapi": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "private-token"})
        calls["openapi"] += 1
        if failure_type == "timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        raise httpx.ConnectError("private transport detail", request=request)

    client = DingTalkOpenAPIClient(
        settings_factory(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(DingTalkOpenAPIError) as caught:
            await client.request_openapi_json("GET", "/v1.0/example")
    finally:
        await client.close()

    assert calls == {"token": 1, "openapi": 3}
    assert caught.value.http_status is None
    assert caught.value.upstream_code is None
    assert "private" not in str(caught.value)
    assert "private" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_success_status_with_non_json_body_is_a_sanitized_protocol_error(
    settings_factory,
) -> None:
    calls = {"token": 0, "openapi": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "private-token"})
        calls["openapi"] += 1
        return httpx.Response(200, text="private non-json upstream body")

    client = DingTalkOpenAPIClient(
        settings_factory(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(DingTalkOpenAPIError) as caught:
            await client.request_openapi_json("GET", "/v1.0/example")
    finally:
        await client.close()

    assert calls == {"token": 1, "openapi": 1}
    assert caught.value.http_status == 200
    assert caught.value.upstream_code is None
    assert "private" not in str(caught.value)
    assert "private" not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_type", "expected_status", "expected_code"),
    [
        ("rate_limit", 429, "Throttling.RateLimit"),
        ("server", 503, "ServiceUnavailable"),
        ("timeout", None, None),
        ("transport", None, None),
    ],
)
async def test_non_idempotent_post_does_not_retry_transient_failure(
    settings_factory,
    failure_type: str,
    expected_status: int | None,
    expected_code: str | None,
) -> None:
    calls = {"token": 0, "openapi": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "private-token"})
        calls["openapi"] += 1
        if failure_type == "timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        if failure_type == "transport":
            raise httpx.ConnectError("private transport detail", request=request)
        if failure_type == "rate_limit":
            return httpx.Response(429, json={"code": "Throttling.RateLimit"})
        return httpx.Response(503, json={"code": "ServiceUnavailable"})

    client = DingTalkOpenAPIClient(
        settings_factory(),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(DingTalkOpenAPIError) as caught:
            await client.request_openapi_json(
                "POST",
                "/v1.0/workflow/processInstances",
                json={"private": "request body"},
            )
    finally:
        await client.close()

    assert calls == {"token": 1, "openapi": 1}
    assert caught.value.http_status == expected_status
    assert caught.value.upstream_code == expected_code
