"""Response redaction, parsing, cache serialization, and result budgeting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ProcessedResponse:
    detected_type: str
    content_type: str | None
    value: Any
    size_bytes: int
    sha256: str | None
    redactions: list[str]
    truncations: list[dict[str, Any]]


def detect_and_process(
    content: bytes,
    content_type: str | None,
    *,
    sensitive_paths: list[str],
    secret_values: list[str],
) -> ProcessedResponse:
    lowered = (content_type or "").lower()
    if "json" in lowered or content.lstrip().startswith((b"{", b"[")):
        try:
            parsed = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        else:
            redactions: list[str] = []
            sanitized = redact_json(parsed, sensitive_paths, secret_values, redactions)
            return ProcessedResponse(
                "json", content_type, sanitized, len(content), None, redactions, []
            )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return ProcessedResponse(
            "binary", content_type, None, len(content), hashlib.sha256(content).hexdigest(), [], []
        )
    redacted = redact_text(text, secret_values)
    redactions = ["exact_secret"] if redacted != text else []
    return ProcessedResponse("text", content_type, redacted, len(content), None, redactions, [])


def redact_text(value: str, secrets: list[str]) -> str:
    for secret in sorted({secret for secret in secrets if secret}, key=len, reverse=True):
        value = value.replace(secret, f"[REDACTED:{len(secret)}]")
    return value


def redact_json(
    value: Any,
    sensitive_paths: list[str],
    secrets: list[str],
    redactions: list[str],
    path: str = "",
) -> Any:
    if path in sensitive_paths:
        redactions.append(path)
        return _redacted(value)
    if isinstance(value, str):
        redacted = redact_text(value, secrets)
        if redacted != value:
            redactions.append(path or "exact_secret")
        return redacted
    if isinstance(value, list):
        return [
            redact_json(
                item,
                sensitive_paths,
                secrets,
                redactions,
                f"{path}.{index}" if path else str(index),
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            str(key): redact_json(
                item, sensitive_paths, secrets, redactions, f"{path}.{key}" if path else str(key)
            )
            for key, item in value.items()
        }
    return value


def _redacted(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
    return {"redacted": True, "type": type(value).__name__, "length": len(encoded)}


def sample_value(
    value: Any,
    arrays: list[dict[str, Any]],
    truncations: list[dict[str, Any]],
    path: str = "",
    depth: int = 0,
) -> Any:
    if depth > 20:
        truncations.append({"path": path, "reason": "max_depth"})
        return "[TRUNCATED:MAX_DEPTH]"
    if isinstance(value, str):
        if len(value) > 256:
            truncations.append(
                {"path": path, "reason": "string_length", "original_length": len(value)}
            )
            return value[:256] + "…"
        return value
    if isinstance(value, list):
        if len(value) <= 1:
            return [
                sample_value(
                    item, arrays, truncations, f"{path}.{index}" if path else str(index), depth + 1
                )
                for index, item in enumerate(value)
            ]
        index = 0
        if any(isinstance(item, dict) for item in value):
            index = max(
                range(len(value)),
                key=lambda idx: len(value[idx]) if isinstance(value[idx], dict) else -1,
            )
        arrays.append({"path": path, "original_length": len(value), "sample_index": index})
        return [
            sample_value(
                value[index],
                arrays,
                truncations,
                f"{path}.{index}" if path else str(index),
                depth + 1,
            )
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > 100:
            truncations.append(
                {"path": path, "reason": "object_keys", "original_length": len(items)}
            )
            items = items[:100]
        return {
            key: sample_value(
                item, arrays, truncations, f"{path}.{key}" if path else str(key), depth + 1
            )
            for key, item in items
        }
    return value


def restrict_text(value: str, limit: int | None, offset: int) -> tuple[str, bool]:
    start = max(0, offset)
    size = limit if limit is not None else 4096
    branch = value[start : start + size]
    return branch, start + len(branch) < len(value)


def get_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current
