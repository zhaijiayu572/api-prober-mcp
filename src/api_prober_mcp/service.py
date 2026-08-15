"""Application service backing the seven MCP tools."""

from __future__ import annotations

import base64
import json
import secrets
import shutil
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from api_prober_mcp.auth import AuthStore
from api_prober_mcp.config.models import (
    SAFE_METHODS,
    AuthProfile,
    GlobalConfig,
    ProjectConfig,
    normalize_origin,
    parse_global_config,
    parse_origin,
    parse_project_config,
)
from api_prober_mcp.diagnostics import Diagnostics
from api_prober_mcp.errors import ProberError
from api_prober_mcp.http import HttpExecutor, validate_headers, validate_url
from api_prober_mcp.response.processor import (
    detect_and_process,
    get_path,
    restrict_text,
    sample_value,
)
from api_prober_mcp.storage import RuntimePaths, load_json

Confirmation = Callable[[str, str], Awaitable[bool]]


async def deny_confirmation(_: str, __: str) -> bool:
    return False


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ProberService:
    """Session-scoped implementation that makes unsafe actions opt-in."""

    def __init__(self, home: Path | None = None, *, session_id: str | None = None) -> None:
        self.paths = RuntimePaths(home)
        self.session_id = session_id or f"session_{secrets.token_urlsafe(18)}"
        self.global_config, self.global_config_error = self._load_global_config()
        self.auth_store = AuthStore(self.paths)
        self.http = HttpExecutor(self.global_config)
        self.diagnostics = Diagnostics(self.paths, self.session_id)
        self.project: ProjectConfig | None = None
        self.project_hash: str | None = None
        self.approved_origins: set[str] = set()
        self._confirmed_insecure_http_origins: set[str] = set()
        self._confirmed_insecure_tls_origins: set[str] = set()
        self._confirmed_proxy_auth_origins: set[str] = set()
        self._responses: dict[str, dict[str, Any]] = {}
        self._response_lru: list[str] = []
        self._response_bytes = 0
        self.diagnostics.event(
            "info",
            "server_started",
            details={"global_config_valid": self.global_config_error is None},
        )

    def _load_global_config(self) -> tuple[GlobalConfig, ProberError | None]:
        try:
            self.paths.ensure_directories()
            path = self.paths.config_path()
            if not path.exists():
                return parse_global_config({"schemaVersion": 1}), None
            self.paths.require_safe_file(path)
            return parse_global_config(load_json(path)), None
        except ProberError as exc:
            if exc.code == "CONFIG_INVALID":
                return parse_global_config({"schemaVersion": 1}), exc
            return parse_global_config({"schemaVersion": 1}), exc

    def _request_id(self) -> str:
        return f"req_{secrets.token_urlsafe(9)}"

    def _ok(self, request_id: str, **payload: Any) -> dict[str, Any]:
        return {"ok": True, "request_id": request_id, **payload}

    def _error(self, request_id: str, error: ProberError) -> dict[str, Any]:
        self.diagnostics.event(
            "warning",
            "tool_error",
            request_id=request_id,
            project_key=self.project.project_key if self.project else None,
            details={"code": error.code, "message": error.message, "details": error.details},
        )
        return {"ok": False, "request_id": request_id, "error": error.as_dict()}

    async def configure_session(
        self, config: object, *, confirmation: Confirmation = deny_confirmation
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            self._ensure_global_config_valid()
            project = parse_project_config(config)
            config_hash = project.config_hash()
            approved = {
                origin for origin in project.allowed_hosts if self._automatically_allowed(origin)
            }
            for origin in project.allowed_hosts:
                if origin in approved:
                    continue
                if origin in self.global_config.allowed_hosts:
                    approved.add(origin)
                    continue
                if not await confirmation(
                    "host",
                    f"Allow this MCP session to access {origin} for project {project.project_key}?",
                ):
                    raise ProberError(
                        "HOST_CONFIRMATION_DECLINED",
                        "User declined a project origin.",
                        {"origin": origin},
                    )
                approved.add(origin)
            if self.project_hash != config_hash:
                self.approved_origins.clear()
                self._confirmed_insecure_http_origins.clear()
                self._confirmed_insecure_tls_origins.clear()
                self._confirmed_proxy_auth_origins.clear()
            self.project = project
            self.project_hash = config_hash
            self.approved_origins = approved
            self.diagnostics.event(
                "info",
                "session_configured",
                request_id=request_id,
                project_key=project.project_key,
                details={"config_hash": config_hash, "approved_origins": sorted(approved)},
            )
            return self._ok(
                request_id,
                project_key=project.project_key,
                config_hash=config_hash,
                approved_origins=sorted(approved),
                auth_profiles=sorted(project.auth_profiles),
                loaded_at=utc_now(),
                global_config_hash=self._global_config_hash(),
            )
        except ProberError as exc:
            return self._error(request_id, exc)

    async def set_auth_value(
        self,
        project_key: str,
        auth_profile_name: str,
        value: str,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            project = self._require_project(project_key)
            profile = self._profile(project, auth_profile_name)
            record = self.auth_store.set_value(
                project.project_key, auth_profile_name, profile, value
            )
            self.diagnostics.add_secret_values([value])
            self.diagnostics.event(
                "info",
                "auth_stored",
                request_id=request_id,
                project_key=project_key,
                auth_profile_name=auth_profile_name,
                details={
                    "auth_type": profile.type,
                    "stored_value_count": record["stored_value_count"],
                    "cookie_names": record.get("cookie_names", []),
                },
            )
            return self._ok(request_id, project_key=project_key, **record)
        except ProberError as exc:
            return self._error(request_id, exc)

    async def get_auth_status(
        self, project_key: str, auth_profile_name: str | None = None
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            project = self._require_project(project_key)
            if auth_profile_name is not None and auth_profile_name not in project.auth_profiles:
                raise ProberError(
                    "AUTH_PROFILE_NOT_FOUND", "Authentication profile is not configured."
                )
            names = [auth_profile_name] if auth_profile_name else sorted(project.auth_profiles)
            profiles: list[dict[str, Any]] = []
            for name in names:
                assert name is not None
                profile = project.auth_profiles[name]
                record = self.auth_store.load(project.project_key, name, profile)
                if record is None:
                    profiles.append(
                        {
                            "auth_profile_name": name,
                            "origin": profile.origin,
                            "auth_type": profile.type,
                            "status": "missing",
                            "stored_value_count": 0,
                            "created_at": None,
                            "last_used_at": None,
                            "expires_at": None,
                        }
                    )
                else:
                    profiles.append(self.auth_store.public_record(record))
            return self._ok(request_id, profiles=profiles)
        except ProberError as exc:
            return self._error(request_id, exc)

    async def delete_auth_profile(
        self,
        project_key: str,
        auth_profile_name: str,
        *,
        confirmation: Confirmation = deny_confirmation,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            project = self._require_project(project_key)
            profile = self._profile(project, auth_profile_name)
            if not await confirmation(
                "delete_auth",
                f"Delete locally stored profile {auth_profile_name} for {profile.origin}?",
            ):
                raise ProberError(
                    "AUTH_INPUT_CANCELLED", "User declined deleting the authentication profile."
                )
            deleted = self.auth_store.delete(project_key, auth_profile_name, profile)
            self.diagnostics.event(
                "info",
                "auth_deleted",
                request_id=request_id,
                project_key=project_key,
                auth_profile_name=auth_profile_name,
                details={"already_missing": not deleted},
            )
            return self._ok(
                request_id,
                project_key=project_key,
                auth_profile_name=auth_profile_name,
                already_missing=not deleted,
            )
        except ProberError as exc:
            return self._error(request_id, exc)

    async def http_request(
        self,
        *,
        project_key: str,
        method: str,
        url: str,
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
        confirmation: Confirmation = deny_confirmation,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            project = self._require_project(project_key)
            method = method.upper()
            canonical_url, origin, path = validate_url(url)
            if origin not in self.approved_origins and not self._automatically_allowed(origin):
                if not await confirmation("host", f"Allow request to {origin} for this session?"):
                    raise ProberError(
                        "HOST_CONFIRMATION_DECLINED", "User declined the target origin."
                    )
                self.approved_origins.add(origin)
            if query:
                canonical_url = self._append_query(canonical_url, query)
            rule = project.matching_rule(method, origin, path)
            if method not in SAFE_METHODS and not (rule and rule.skip_confirmation):
                if not await confirmation("method", f"Allow {method} request to {origin}{path}?"):
                    raise ProberError(
                        "METHOD_CONFIRMATION_DECLINED",
                        "User declined the non-read-only HTTP request.",
                    )
            profile: AuthProfile | None = None
            injected_headers: dict[str, str] = {}
            secret_values: list[str] = []
            if auth_profile_name is not None:
                profile = self._profile(project, auth_profile_name)
                if profile.origin != origin:
                    raise ProberError(
                        "REQUEST_INVALID",
                        "Authentication profile origin does not match request origin.",
                    )
                if (
                    origin.startswith("http://")
                    and not self._automatically_allowed(origin)
                    and origin not in self._confirmed_insecure_http_origins
                ):
                    if not await confirmation(
                        "insecure_http",
                        f"Allow sending authentication to insecure HTTP origin {origin}?",
                    ):
                        raise ProberError(
                            "INSECURE_HTTP_DECLINED", "User declined sending credentials over HTTP."
                        )
                    self._confirmed_insecure_http_origins.add(origin)
                if (
                    self._tls_verification_disabled(project, origin)
                    and origin not in self._confirmed_insecure_tls_origins
                ):
                    if not await confirmation(
                        "insecure_tls",
                        f"Allow TLS certificate verification to be disabled for {origin}?",
                    ):
                        raise ProberError(
                            "INSECURE_TLS_DECLINED", "User declined unverified TLS for this origin."
                        )
                    self._confirmed_insecure_tls_origins.add(origin)
                if (
                    self.global_config.proxy is not None
                    and not self._automatically_allowed(origin)
                    and origin not in self._confirmed_proxy_auth_origins
                ):
                    if not await confirmation(
                        "proxy",
                        f"Allow sending authentication for {origin} through the configured proxy?",
                    ):
                        raise ProberError(
                            "PROXY_CONFIRMATION_DECLINED",
                            "User declined sending credentials through the proxy.",
                        )
                    self._confirmed_proxy_auth_origins.add(origin)
                injected_headers, secret_values = self.auth_store.inject(
                    project_key, auth_profile_name, profile, path, origin.split(":", 1)[0]
                )
                self.diagnostics.add_secret_values(secret_values)
            managed = {name.lower() for name in injected_headers}
            supplied_headers = validate_headers(headers, managed)
            body, body_headers = self._request_body(
                json_body, form_body, raw_body, raw_body_encoding, content_type
            )
            if any(name.lower() == "content-type" for name in supplied_headers) and body_headers:
                raise ProberError(
                    "REQUEST_INVALID", "Content-Type is managed by the selected request body."
                )
            request_headers = {**supplied_headers, **body_headers, **injected_headers}
            resolved_timeout = self._timeout(project, rule, timeout_seconds)
            result_budget = self._result_budget(project, rule, max_result_bytes)
            if debug:
                if not await confirmation(
                    "debug", "Allow a redacted, metadata-only debug capture for this request?"
                ):
                    raise ProberError("DEBUG_CONFIRMATION_DECLINED", "User declined debug capture.")
            raw = await self.http.execute(
                method=method,
                url=canonical_url,
                project=project,
                approved_origins=self.approved_origins,
                headers=request_headers,
                content=body,
                timeout_seconds=resolved_timeout,
                auth_profile=profile,
                confirmation=confirmation,
                proxy_allowed=True,
            )
            if profile is not None and auth_profile_name is not None:
                cookie_updates = self.auth_store.update_set_cookie(
                    project_key, auth_profile_name, profile, raw.set_cookie_headers
                )
                if cookie_updates:
                    self.diagnostics.event(
                        "info",
                        "cookie_updated",
                        request_id=request_id,
                        project_key=project_key,
                        auth_profile_name=auth_profile_name,
                        details={"updates": cookie_updates},
                    )
            extra_paths = list(project.response.sensitive_paths) + list(sensitive_paths or [])
            processed = detect_and_process(
                raw.content,
                raw.headers.get("content-type"),
                sensitive_paths=extra_paths,
                secret_values=secret_values,
            )
            if (
                profile is not None
                and auth_profile_name is not None
                and self._matches_invalid(profile, raw.status, processed.value)
            ):
                self.auth_store.mark_invalid(
                    project_key, auth_profile_name, profile, "response_rule_matched"
                )
                self.diagnostics.event(
                    "warning",
                    "auth_marked_invalid",
                    request_id=request_id,
                    project_key=project_key,
                    auth_profile_name=auth_profile_name,
                    details={"matched_status": raw.status},
                )
            response_id = self._cache_response(processed)
            filtered_headers = self._response_headers(raw.headers, project)
            arrays: list[dict[str, Any]] = []
            truncations = list(processed.truncations)
            body_value = processed.value
            if processed.detected_type == "json":
                body_value = sample_value(body_value, arrays, truncations)
            elif processed.detected_type == "text" and isinstance(body_value, str):
                body_value, was_truncated = restrict_text(
                    body_value, min(4096, result_budget // 2), 0
                )
                if was_truncated:
                    truncations.append({"path": "", "reason": "result_budget"})
            result = self._ok(
                request_id,
                request={
                    "method": method,
                    "origin": origin,
                    "path": rule.path if rule else path,
                    "attempts": raw.attempts,
                    "queue_ms": raw.queue_ms,
                    "duration_ms": raw.duration_ms,
                    "tls_verified": raw.tls_verified,
                    "proxy_used": raw.proxy_used,
                },
                response={
                    "status": raw.status,
                    "detected_type": processed.detected_type,
                    "content_type": processed.content_type,
                    "headers": filtered_headers,
                    "body": body_value,
                    "size_bytes": processed.size_bytes,
                    "sha256": processed.sha256,
                },
                processing={
                    "max_result_bytes": result_budget,
                    "arrays": arrays,
                    "redactions": processed.redactions,
                    "truncations": truncations,
                },
                redirects=raw.redirects,
                response_id=response_id
                if truncations or processed.size_bytes > result_budget
                else None,
                omitted_paths=[]
                if not truncations
                else [item.get("path", "") for item in truncations],
            )
            result = self._fit_result(result, result_budget, response_id)
            self.diagnostics.event(
                "info",
                "http_request",
                request_id=request_id,
                project_key=project_key,
                auth_profile_name=auth_profile_name,
                details={
                    "method": method,
                    "origin": origin,
                    "path": path,
                    "status": raw.status,
                    "response_bytes": processed.size_bytes,
                    "type": processed.detected_type,
                    "redactions": len(processed.redactions),
                    "truncations": len(truncations),
                },
            )
            return result
        except ProberError as exc:
            return self._error(request_id, exc)
        except (TypeError, ValueError) as exc:
            return self._error(
                request_id,
                ProberError("REQUEST_INVALID", "Request input is invalid.", {"reason": str(exc)}),
            )
        except Exception as exc:  # never expose unknown internals
            self.diagnostics.event(
                "error",
                "internal_error",
                request_id=request_id,
                project_key=self.project.project_key if self.project else None,
                details={"exception_type": type(exc).__name__},
            )
            return self._error(
                request_id, ProberError("INTERNAL_ERROR", "Unexpected internal error.")
            )

    async def inspect_response(
        self,
        response_id: str,
        *,
        path: str | None = None,
        offset: int = 0,
        limit: int | None = None,
        max_result_bytes: int | None = None,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            if not isinstance(response_id, str) or response_id not in self._responses:
                raise ProberError(
                    "RESPONSE_NOT_FOUND", "Response ID is not available in this MCP session."
                )
            if offset < 0 or offset > 1_000_000 or (limit is not None and not 1 <= limit <= 10_000):
                raise ProberError("REQUEST_INVALID", "inspect_response offset or limit is invalid.")
            cache_path = self.paths.session_cache_directory(self.session_id) / f"{response_id}.json"
            self.paths.require_safe_file(cache_path)
            cache = load_json(cache_path)
            budget = (
                self._result_budget(self.project, None, max_result_bytes)
                if self.project
                else 20_480
            )
            value = cache["value"]
            if cache["detected_type"] == "json":
                try:
                    value = get_path(value, path)
                except KeyError as exc:
                    raise ProberError(
                        "REQUEST_INVALID", "Requested response path does not exist.", {"path": path}
                    ) from exc
                if isinstance(value, list):
                    end = offset + (limit if limit is not None else 100)
                    value = value[offset:end]
                arrays: list[dict[str, Any]] = []
                truncations: list[dict[str, Any]] = []
                value = sample_value(value, arrays, truncations)
            elif cache["detected_type"] == "text":
                if path is not None:
                    raise ProberError(
                        "REQUEST_INVALID", "Text responses do not support JSON paths."
                    )
                assert isinstance(value, str)
                value, truncated = restrict_text(value, limit, offset)
                arrays = []
                truncations = [{"path": "", "reason": "range"}] if truncated else []
            else:
                value = None
                arrays = []
                truncations = []
            result = self._ok(
                request_id,
                response_id=response_id,
                path=path,
                value=value,
                processing={
                    "max_result_bytes": budget,
                    "arrays": arrays,
                    "redactions": cache["redactions"],
                    "truncations": truncations,
                },
            )
            return self._fit_result(result, budget, response_id)
        except ProberError as exc:
            return self._error(request_id, exc)

    async def get_diagnostics(
        self,
        *,
        request_id_filter: str | None = None,
        session_id: str | None = None,
        project_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        level: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 100,
        max_result_bytes: int | None = None,
    ) -> dict[str, Any]:
        request_id = self._request_id()
        try:
            budget = (
                self._result_budget(self.project, None, max_result_bytes)
                if self.project
                else 20_480
            )
            events, truncated = self.diagnostics.query(
                request_id=request_id_filter,
                session_id=session_id,
                project_key=project_key or (self.project.project_key if self.project else None),
                since=since,
                until=until,
                level=level,
                event_types=event_types,
                limit=limit,
                max_result_bytes=budget,
            )
            return self._ok(request_id, events=events, returned=len(events), truncated=truncated)
        except (ValueError, ProberError) as exc:
            error = (
                exc
                if isinstance(exc, ProberError)
                else ProberError(
                    "DIAGNOSTICS_QUERY_INVALID",
                    "Diagnostics query is invalid.",
                    {"reason": str(exc)},
                )
            )
            return self._error(request_id, error)

    def close(self) -> None:
        self._responses.clear()
        cache_dir = self.paths.cache / self.session_id
        if cache_dir.exists() and not cache_dir.is_symlink():
            shutil.rmtree(cache_dir, ignore_errors=True)

    def _ensure_global_config_valid(self) -> None:
        if self.global_config_error:
            raise self.global_config_error

    def _require_project(self, project_key: str) -> ProjectConfig:
        self._ensure_global_config_valid()
        if self.project is None:
            raise ProberError(
                "CONFIG_NOT_LOADED",
                "No project configuration has been loaded.",
                next_action="Call configure_session with the project configuration.",
            )
        if self.project.project_key != project_key:
            raise ProberError(
                "PROJECT_MISMATCH",
                "project_key does not match the active session configuration.",
                {"expected": self.project.project_key},
            )
        return self.project

    def _profile(self, project: ProjectConfig, name: str) -> AuthProfile:
        profile = project.auth_profiles.get(name)
        if profile is None:
            raise ProberError(
                "AUTH_PROFILE_NOT_FOUND",
                "Authentication profile is not configured.",
                {"auth_profile_name": name},
            )
        return profile

    def _automatically_allowed(self, origin: str) -> bool:
        if origin in self.global_config.allowed_hosts:
            return True
        return parse_origin(normalize_origin(origin)).host in {"localhost", "127.0.0.1", "::1"}

    def _global_config_hash(self) -> str:
        encoded = json.dumps(
            self.global_config.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        import hashlib

        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def _append_query(self, url: str, query: dict[str, Any]) -> str:
        if not isinstance(query, dict) or len(query) > 100:
            raise ProberError("REQUEST_INVALID", "query must contain at most 100 fields.")
        pairs: list[tuple[str, str]] = []
        for key, value in query.items():
            if not isinstance(key, str) or not key or len(key) > 256:
                raise ProberError("REQUEST_INVALID", "Query key is invalid.")
            values = value if isinstance(value, list) else [value]
            if len(values) > 50:
                raise ProberError("REQUEST_INVALID", "A query key has too many values.")
            for item in values:
                if not isinstance(item, (str, int, float, bool)):
                    raise ProberError(
                        "REQUEST_INVALID", "Query values must be scalar or arrays of scalars."
                    )
                pairs.append((key, str(item).lower() if isinstance(item, bool) else str(item)))
        separator = "&" if "?" in url else "?"
        result = url + separator + urlencode(pairs)
        if len(result) > 4096:
            raise ProberError("REQUEST_INVALID", "URL plus query exceeds 4096 characters.")
        return result

    def _request_body(
        self,
        json_body: Any | None,
        form_body: dict[str, Any] | None,
        raw_body: str | None,
        raw_body_encoding: str,
        content_type: str | None,
    ) -> tuple[bytes | None, dict[str, str]]:
        selected = sum(item is not None for item in (json_body, form_body, raw_body))
        if selected > 1:
            raise ProberError(
                "REQUEST_INVALID", "json_body, form_body, and raw_body are mutually exclusive."
            )
        if json_body is not None:
            encoded = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            return self._checked_body(encoded), {"Content-Type": "application/json"}
        if form_body is not None:
            if not isinstance(form_body, dict) or len(form_body) > 100:
                raise ProberError(
                    "REQUEST_INVALID", "form_body must be an object with at most 100 fields."
                )
            pairs: list[tuple[str, str]] = []
            for key, value in form_body.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
                        raise ProberError(
                            "REQUEST_INVALID", "Form values must be scalar or arrays of scalars."
                        )
                    pairs.append((key, str(item)))
            return self._checked_body(urlencode(pairs).encode()), {
                "Content-Type": "application/x-www-form-urlencoded"
            }
        if raw_body is not None:
            if (
                not content_type
                or not isinstance(content_type, str)
                or len(content_type) > 256
                or any(character in content_type for character in "\r\n")
            ):
                raise ProberError(
                    "REQUEST_INVALID", "raw_body requires a valid explicit content_type."
                )
            if raw_body_encoding == "utf8":
                encoded = raw_body.encode("utf-8")
            elif raw_body_encoding == "base64":
                try:
                    encoded = base64.b64decode(raw_body, validate=True)
                except ValueError as exc:
                    raise ProberError("REQUEST_INVALID", "raw_body is not valid base64.") from exc
            else:
                raise ProberError("REQUEST_INVALID", "raw_body_encoding must be utf8 or base64.")
            return self._checked_body(encoded), {"Content-Type": content_type}
        return None, {}

    @staticmethod
    def _checked_body(value: bytes) -> bytes:
        if len(value) > 1_048_576:
            raise ProberError("REQUEST_INVALID", "Request body exceeds the 1 MiB input limit.")
        return value

    def _timeout(self, project: ProjectConfig, rule: Any, requested: float | None) -> float:
        value = (
            requested
            if requested is not None
            else (
                rule.timeout_seconds
                if rule and rule.timeout_seconds is not None
                else project.defaults.default_timeout_seconds
                or self.global_config.limits.default_timeout_seconds
            )
        )
        maximum = min(
            project.defaults.max_timeout_seconds or self.global_config.limits.max_timeout_seconds,
            self.global_config.limits.max_timeout_seconds,
            300,
        )
        if not isinstance(value, (int, float)) or value < 1 or value > maximum:
            raise ProberError(
                "REQUEST_INVALID",
                "timeout_seconds exceeds the configured maximum.",
                {"maximum": maximum},
            )
        return float(value)

    def _result_budget(
        self, project: ProjectConfig | None, rule: Any, requested: int | None
    ) -> int:
        default = (
            project.defaults.default_result_bytes
            if project and project.defaults.default_result_bytes is not None
            else self.global_config.limits.default_result_bytes
        )
        value = (
            requested
            if requested is not None
            else (rule.max_result_bytes if rule and rule.max_result_bytes is not None else default)
        )
        project_max = (
            project.defaults.max_result_bytes
            if project and project.defaults.max_result_bytes is not None
            else self.global_config.limits.max_result_bytes
        )
        maximum = min(project_max, self.global_config.limits.max_result_bytes, 1_048_576)
        if not isinstance(value, int) or value < 4096 or value > maximum:
            raise ProberError(
                "RESULT_BUDGET_INVALID",
                "max_result_bytes is outside the configured range.",
                {"minimum": 4096, "maximum": maximum},
            )
        return value

    @staticmethod
    def _tls_verification_disabled(project: ProjectConfig, origin: str) -> bool:
        return any(rule.origin == origin and not rule.verify for rule in project.tls)

    def _response_headers(self, headers: dict[str, str], project: ProjectConfig) -> dict[str, str]:
        allowed = {"content-type", "content-length", "content-disposition", "link", "retry-after"}
        allowed |= set(self.global_config.response.allowed_headers)
        allowed |= set(project.response.allowed_headers)
        result: dict[str, str] = {}
        for name, value in headers.items():
            lower = name.lower()
            if lower == "set-cookie":
                continue
            if (
                lower in allowed
                or lower.startswith("ratelimit-")
                or lower.startswith("x-ratelimit-")
                or lower.startswith("x-page-")
            ):
                result[name] = value[:512]
        return result

    def _matches_invalid(self, profile: AuthProfile, status: int, value: Any) -> bool:
        if status in profile.invalid_when.status_codes:
            return True
        for rule in profile.invalid_when.body_rules:
            try:
                candidate = get_path(value, rule.path)
            except KeyError:
                continue
            if candidate == rule.equals:
                return True
        return False

    def _cache_response(self, processed: Any) -> str:
        record = {
            "detected_type": processed.detected_type,
            "content_type": processed.content_type,
            "value": processed.value,
            "size_bytes": processed.size_bytes,
            "sha256": processed.sha256,
            "redactions": processed.redactions,
        }
        serialized = json.dumps(record, ensure_ascii=False).encode("utf-8")
        response_id = f"resp_{secrets.token_urlsafe(12)}"
        maximum = self.global_config.limits.max_session_cache_bytes
        cache_directory = self.paths.session_cache_directory(self.session_id)
        while self._response_lru and self._response_bytes + len(serialized) > maximum:
            old = self._response_lru.pop(0)
            old_record = self._responses.pop(old, None)
            if old_record:
                self._response_bytes -= int(old_record["cache_bytes"])
                (cache_directory / f"{old}.json").unlink(missing_ok=True)
        if len(serialized) <= maximum:
            cache_path = cache_directory / f"{response_id}.json"
            from api_prober_mcp.storage import atomic_write_json

            atomic_write_json(cache_path, record)
            self._responses[response_id] = {"cache_bytes": len(serialized)}
            self._response_lru.append(response_id)
            self._response_bytes += len(serialized)
        return response_id

    @staticmethod
    def _fit_result(result: dict[str, Any], budget: int, response_id: str | None) -> dict[str, Any]:
        if len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) <= budget:
            return result
        response = result.get("response")
        if isinstance(response, dict) and response.get("body") is not None:
            response["body"] = "[OMITTED:RESULT_BUDGET]"
        processing = result.get("processing")
        if isinstance(processing, dict):
            processing.setdefault("truncations", []).append(
                {"path": "response.body", "reason": "result_budget"}
            )
        result["response_id"] = response_id
        if len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")) <= budget:
            return result
        # This minimal envelope is the final guard against metadata bypassing a result budget.
        return {
            "ok": bool(result.get("ok", True)),
            "request_id": result.get("request_id"),
            "response_id": response_id,
            "truncated": True,
            "processing": {
                "max_result_bytes": budget,
                "truncations": [{"path": "", "reason": "result_budget"}],
            },
        }
