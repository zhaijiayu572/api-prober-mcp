from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from api_prober_mcp.http.client import RawHttpResponse
from api_prober_mcp.service import ProberService


async def approve(_: str, __: str) -> bool:
    return True


@pytest.mark.asyncio
async def test_session_auth_request_redacts_secret_and_supports_inspect(tmp_path: Path) -> None:
    service = ProberService(tmp_path)

    async def fake_execute(**kwargs: Any) -> RawHttpResponse:
        authorization = kwargs["headers"]["Authorization"]
        body = json.dumps(
            {
                "data": [{"id": 1, "secret": "not-the-token"}, {"id": 2}],
                "echo": authorization,
                "large": "x" * 5000,
            }
        ).encode()
        return RawHttpResponse(
            status=200,
            headers={"content-type": "application/json"},
            set_cookie_headers=[],
            content=body,
            final_url=kwargs["url"],
            redirects=[],
            attempts=1,
            duration_ms=1,
            queue_ms=0,
            tls_verified=None,
            proxy_used=False,
        )

    service.http.execute = fake_execute  # type: ignore[method-assign]
    config = {
        "schemaVersion": 1,
        "projectKey": "demo-dev",
        "authProfiles": {
            "default": {
                "origin": "http://127.0.0.1:8765",
                "type": "bearer",
                "bearer": {"headerName": "Authorization", "prefix": "Bearer "},
            }
        },
    }
    try:
        loaded = await service.configure_session(config, confirmation=approve)
        assert loaded["ok"] is True
        stored = await service.set_auth_value("demo-dev", "default", "super-secret-token")
        assert stored["ok"] is True
        result = await service.http_request(
            project_key="demo-dev",
            method="GET",
            url="http://127.0.0.1:8765/v1/items",
            auth_profile_name="default",
            max_result_bytes=4096,
            confirmation=approve,
        )
        assert result["ok"] is True
        encoded = json.dumps(result)
        assert "super-secret-token" not in encoded
        assert result["response"]["body"]["echo"] == "Bearer [REDACTED:18]"
        response_id = result["response_id"]
        assert response_id is not None
        inspected = await service.inspect_response(response_id, path="data", max_result_bytes=4096)
        assert inspected["ok"] is True
        assert inspected["value"] == [{"id": 1, "secret": "not-the-token"}]
    finally:
        service.close()


@pytest.mark.asyncio
async def test_private_target_is_blocked_even_when_confirmed(tmp_path: Path) -> None:
    service = ProberService(tmp_path)
    try:
        loaded = await service.configure_session(
            {"schemaVersion": 1, "projectKey": "demo", "allowedHosts": ["http://192.168.1.10"]},
            confirmation=approve,
        )
        assert loaded["ok"] is True
        result = await service.http_request(
            project_key="demo",
            method="GET",
            url="http://192.168.1.10/status",
            confirmation=approve,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "HOST_NOT_APPROVED"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_profile_configuration_change_invalidates_saved_value(tmp_path: Path) -> None:
    service = ProberService(tmp_path)
    base = {
        "schemaVersion": 1,
        "projectKey": "demo",
        "authProfiles": {
            "token": {
                "origin": "http://127.0.0.1:8765",
                "type": "bearer",
                "bearer": {"headerName": "Authorization", "prefix": "Bearer "},
            }
        },
    }
    try:
        assert (await service.configure_session(base, confirmation=approve))["ok"]
        assert (await service.set_auth_value("demo", "token", "secret"))["ok"]
        changed = {
            **base,
            "authProfiles": {
                "token": {
                    "origin": "http://127.0.0.1:8765",
                    "type": "bearer",
                    "bearer": {"headerName": "Authorization", "prefix": "Token "},
                }
            },
        }
        assert (await service.configure_session(changed, confirmation=approve))["ok"]
        status = await service.get_auth_status("demo", "token")
        profile = status["profiles"][0]
        assert profile["status"] == "invalid"
        assert profile["invalid_reason"] == "auth_config_changed"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_metadata_target_is_hard_blocked(tmp_path: Path) -> None:
    service = ProberService(tmp_path)
    try:
        assert (
            await service.configure_session(
                {
                    "schemaVersion": 1,
                    "projectKey": "demo",
                    "allowedHosts": ["http://169.254.169.254"],
                },
                confirmation=approve,
            )
        )["ok"]
        result = await service.http_request(
            project_key="demo",
            method="GET",
            url="http://169.254.169.254/latest/meta-data",
            confirmation=approve,
        )
        assert result["error"]["code"] == "METADATA_TARGET_BLOCKED"
    finally:
        service.close()


def test_result_budget_final_fallback_is_strict() -> None:
    result = ProberService._fit_result(
        {"ok": True, "request_id": "req", "events": ["x" * 100_000]}, 4096, None
    )
    assert len(json.dumps(result).encode()) <= 4096
    assert result["truncated"] is True
