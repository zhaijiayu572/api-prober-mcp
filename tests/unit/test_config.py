from __future__ import annotations

from api_prober_mcp.config.models import parse_project_config
from api_prober_mcp.errors import ProberError


def test_project_config_is_strict_and_normalizes_origin() -> None:
    config = parse_project_config(
        {
            "schemaVersion": 1,
            "projectKey": "demo-dev",
            "allowedHosts": ["http://LOCALHOST:80"],
            "authProfiles": {
                "token": {
                    "origin": "http://localhost",
                    "type": "bearer",
                    "bearer": {"headerName": "Authorization"},
                }
            },
        }
    )
    assert config.allowed_hosts == ["http://localhost"]
    assert config.auth_profiles["token"].origin == "http://localhost"


def test_auth_config_rejects_dangerous_header() -> None:
    try:
        parse_project_config(
            {
                "schemaVersion": 1,
                "projectKey": "demo",
                "authProfiles": {
                    "token": {
                        "origin": "http://127.0.0.1",
                        "type": "header",
                        "header": {"name": "Host"},
                    }
                },
            }
        )
    except ProberError as error:
        assert error.code == "CONFIG_INVALID"
    else:
        raise AssertionError("dangerous authentication header was accepted")


def test_server_exposes_exactly_seven_first_release_tools(tmp_path) -> None:
    import asyncio

    from api_prober_mcp.server import build_server

    async def names() -> list[str]:
        server = build_server(tmp_path)
        return sorted(tool.name for tool in await server.list_tools())

    assert asyncio.run(names()) == [
        "configure_session",
        "delete_auth_profile",
        "get_auth_status",
        "get_diagnostics",
        "http_request",
        "inspect_response",
        "set_auth",
    ]


def test_stored_response_cache_is_private_and_symlinks_are_rejected(tmp_path) -> None:
    import asyncio
    import json
    import stat
    from typing import Any

    from api_prober_mcp.http.client import RawHttpResponse
    from api_prober_mcp.service import ProberService

    async def run() -> None:
        async def approve(_: str, __: str) -> bool:
            return True

        service = ProberService(tmp_path)

        async def fake_execute(**kwargs: Any) -> RawHttpResponse:
            return RawHttpResponse(
                status=200,
                headers={"content-type": "application/json"},
                set_cookie_headers=[],
                content=json.dumps({"large": "x" * 1000}).encode(),
                final_url=kwargs["url"],
                redirects=[],
                attempts=1,
                duration_ms=1,
                queue_ms=0,
                tls_verified=None,
                proxy_used=False,
            )

        service.http.execute = fake_execute  # type: ignore[method-assign]
        try:
            assert (
                await service.configure_session(
                    {"schemaVersion": 1, "projectKey": "demo"}, confirmation=approve
                )
            )["ok"]
            result = await service.http_request(
                project_key="demo",
                method="GET",
                url="http://127.0.0.1:8765/data",
                confirmation=approve,
            )
            response_id = result["response_id"]
            assert response_id is not None
            cache_path = (
                service.paths.session_cache_directory(service.session_id) / f"{response_id}.json"
            )
            assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
            cache_path.unlink()
            cache_path.symlink_to(tmp_path / "outside.json")
            rejected = await service.inspect_response(response_id)
            assert rejected["error"]["code"] == "STORAGE_PERMISSION_INVALID"
        finally:
            service.close()

    asyncio.run(run())
