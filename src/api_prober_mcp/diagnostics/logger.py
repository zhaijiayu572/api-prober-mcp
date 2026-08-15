"""JSONL diagnostics with conservative redaction and bounded queries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from api_prober_mcp.storage import RuntimePaths


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if secret:
                result = result.replace(secret, f"[REDACTED:{len(secret)}]")
        return result
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item, secrets) for key, item in value.items()}
    return value


class Diagnostics:
    def __init__(
        self, paths: RuntimePaths, session_id: str, secrets: list[str] | None = None
    ) -> None:
        self.paths = paths
        self.session_id = session_id
        self._secrets = secrets or []

    def add_secret_values(self, values: list[str]) -> None:
        self._secrets.extend(value for value in values if value and value not in self._secrets)

    def event(
        self,
        level: str,
        event_type: str,
        *,
        request_id: str | None = None,
        project_key: str | None = None,
        auth_profile_name: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.paths.ensure_directories()
        now = datetime.now(UTC)
        payload = {
            "timestamp": now.isoformat().replace("+00:00", "Z"),
            "level": level,
            "event_type": event_type,
            "session_id": self.session_id,
            "related_request_id": request_id,
            "project_key": project_key,
            "auth_profile_name": auth_profile_name,
            "details": _redact(details or {}, self._secrets),
        }
        target = self.paths.log_path(now.date().isoformat(), self.session_id)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def query(
        self,
        *,
        request_id: str | None = None,
        session_id: str | None = None,
        project_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        level: str | None = None,
        event_types: list[str] | None = None,
        limit: int = 100,
        max_result_bytes: int = 20_480,
    ) -> tuple[list[dict[str, Any]], bool]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be 1..1000")
        start = _parse_time(since) if since else datetime.now(UTC) - timedelta(hours=1)
        end = _parse_time(until) if until else datetime.now(UTC)
        if start > end:
            raise ValueError("since must not be after until")
        events: list[dict[str, Any]] = []
        if not self.paths.logs.exists():
            return events, False
        for file in sorted(self.paths.logs.glob("*/*.jsonl")):
            if file.is_symlink():
                continue
            try:
                with file.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        item = json.loads(line)
                        timestamp = _parse_time(str(item.get("timestamp", "")))
                        if not start <= timestamp <= end:
                            continue
                        if request_id and item.get("related_request_id") != request_id:
                            continue
                        if session_id and item.get("session_id") != session_id:
                            continue
                        if project_key and item.get("project_key") != project_key:
                            continue
                        if level and item.get("level") != level:
                            continue
                        if event_types and item.get("event_type") not in event_types:
                            continue
                        events.append(_redact(item, self._secrets))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        events.sort(key=lambda item: str(item.get("timestamp", "")))
        output: list[dict[str, Any]] = []
        truncated = False
        for item in events[:limit]:
            candidate = output + [item]
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > max_result_bytes:
                truncated = True
                break
            output.append(item)
        if len(events) > len(output):
            truncated = True
        return output, truncated


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)
