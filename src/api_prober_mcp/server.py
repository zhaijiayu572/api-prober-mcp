"""FastMCP stdio entry point exposing the seven first-release tools."""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Any

from fastmcp import Context, FastMCP

from api_prober_mcp import __version__
from api_prober_mcp.errors import ProberError
from api_prober_mcp.sensitive_input import SensitiveInputPage
from api_prober_mcp.service import ProberService


def build_server(home: Path | None = None) -> FastMCP:
    service = ProberService(home)
    server = FastMCP(
        "API Prober",
        instructions="Controlled, audited HTTP probing. Configure a session before requests.",
    )

    async def confirm(ctx: Context, kind: str, message: str) -> bool:
        try:
            result = await ctx.elicit(
                message,
                str,  # type: ignore[arg-type]
                response_title="Approval",
                response_description=f"Required policy check: {kind}",
            )
        except Exception:
            return False
        return (
            getattr(result, "action", None) == "accept"
            and str(getattr(result, "data", "")).strip().lower() == "approve"
        )

    @server.tool(description="Load and validate a project API Prober configuration.")
    async def configure_session(config: dict[str, Any], ctx: Context) -> dict[str, Any]:
        return await service.configure_session(
            config, confirmation=lambda kind, message: confirm(ctx, kind, message)
        )

    @server.tool(
        description="Open a secure local page to store one configured authentication profile."
    )
    async def set_auth(project_key: str, auth_profile_name: str, ctx: Context) -> dict[str, Any]:
        try:
            project = service._require_project(project_key)
            profile = service._profile(project, auth_profile_name)
            summary = f"{profile.type} authentication for {profile.origin}."
            page = SensitiveInputPage("API Prober credential input", summary)
            await page.start()
            elicitation_id = f"auth_{secrets.token_urlsafe(12)}"
            try:
                result = await ctx.session.elicit_url(
                    "Open the local page to enter the credential. The value never enters the agent context.",
                    page.url,
                    elicitation_id,
                )
                if getattr(result, "action", None) != "accept":
                    return service._error(
                        service._request_id(),
                        ProberError(
                            "AUTH_INPUT_CANCELLED", "User declined or cancelled credential input."
                        ),
                    )
                value = await page.wait()
                await ctx.session.send_elicit_complete(elicitation_id)
                return await service.set_auth_value(project_key, auth_profile_name, value)
            finally:
                await page.close()
        except ProberError as exc:
            return service._error(service._request_id(), exc)
        except Exception:
            return service._error(
                service._request_id(),
                ProberError(
                    "ELICITATION_UNSUPPORTED", "The MCP client does not support URL elicitation."
                ),
            )

    @server.tool(description="Return configured authentication profile state without credentials.")
    async def get_auth_status(
        project_key: str, auth_profile_name: str | None = None
    ) -> dict[str, Any]:
        return await service.get_auth_status(project_key, auth_profile_name)

    @server.tool(
        description="Send a policy-controlled HTTP request and return a redacted response."
    )
    async def http_request(
        project_key: str,
        method: str,
        url: str,
        ctx: Context,
        auth_profile_name: str | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any | None = None,
        form_body: dict[str, Any] | None = None,
        raw_body: str | None = None,
        raw_body_encoding: str = "utf8",
        content_type: str | None = None,
        timeout_seconds: float | None = None,
        max_result_bytes: int | None = None,
        sensitive_paths: list[str] | None = None,
        debug: bool = False,
    ) -> dict[str, Any]:
        return await service.http_request(
            project_key=project_key,
            method=method,
            url=url,
            auth_profile_name=auth_profile_name,
            query=query,
            headers=headers,
            json_body=json_body,
            form_body=form_body,
            raw_body=raw_body,
            raw_body_encoding=raw_body_encoding,
            content_type=content_type,
            timeout_seconds=timeout_seconds,
            max_result_bytes=max_result_bytes,
            sensitive_paths=sensitive_paths,
            debug=debug,
            confirmation=lambda kind, message: confirm(ctx, kind, message),
        )

    @server.tool(description="Inspect a redacted response branch cached in this MCP session.")
    async def inspect_response(
        response_id: str,
        path: str | None = None,
        offset: int = 0,
        limit: int | None = None,
        max_result_bytes: int | None = None,
    ) -> dict[str, Any]:
        return await service.inspect_response(
            response_id,
            path=path,
            offset=offset,
            limit=limit,
            max_result_bytes=max_result_bytes,
        )

    @server.tool(
        description="Delete a local authentication profile after an explicit confirmation."
    )
    async def delete_auth_profile(
        project_key: str, auth_profile_name: str, ctx: Context
    ) -> dict[str, Any]:
        return await service.delete_auth_profile(
            project_key,
            auth_profile_name,
            confirmation=lambda kind, message: confirm(ctx, kind, message),
        )

    @server.tool(description="Query redacted local audit and diagnostic events.")
    async def get_diagnostics(
        request_id: str | None = None,
        session_id: str | None = None,
        project_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        level: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 100,
        max_result_bytes: int | None = None,
    ) -> dict[str, Any]:
        return await service.get_diagnostics(
            request_id_filter=request_id,
            session_id=session_id,
            project_key=project_key,
            since=since,
            until=until,
            level=level,
            event_types=event_types,
            limit=limit,
            max_result_bytes=max_result_bytes,
        )

    return server


def main() -> None:
    if "--version" in sys.argv:
        print(__version__)
        return
    build_server().run(transport="stdio")
