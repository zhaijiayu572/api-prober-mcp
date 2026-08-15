"""Credential records, secure storage, injection, and cookie handling."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from filelock import FileLock

from api_prober_mcp.config.models import COOKIE_NAME_RE, AuthProfile
from api_prober_mcp.errors import ProberError
from api_prober_mcp.storage import RuntimePaths, atomic_write_json, load_json

_COOKIE_PAIR_RE = re.compile(r"^\s*([^=;\s]+)=([^;]*)\s*$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _profile_id(project_key: str, name: str, origin: str) -> str:
    raw = f"{project_key}\0{name}\0{origin}".encode()
    return hashlib.sha256(raw).hexdigest()


def _jwt_exp(value: str) -> str | None:
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return datetime.fromtimestamp(exp, UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return None


def parse_cookie_bundle(value: str, default_path: str) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 16 * 1024:
        raise ProberError("AUTH_INPUT_INVALID", "Cookie input must be 1..16 KiB.")
    if value.lower().startswith("cookie:"):
        value = value[7:].lstrip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProberError("AUTH_INPUT_INVALID", "Cookie input contains control characters.")
    cookies: list[dict[str, Any]] = []
    for part in value.split(";"):
        match = _COOKIE_PAIR_RE.fullmatch(part)
        if not match:
            raise ProberError("AUTH_INPUT_INVALID", "Cookie input contains an invalid item.")
        name, cookie_value = match.groups()
        if not COOKIE_NAME_RE.fullmatch(name):
            raise ProberError("AUTH_INPUT_INVALID", "Cookie input contains an invalid cookie name.")
        cookies.append(
            {
                "name": name,
                "value": cookie_value,
                "path": default_path,
                "secure": False,
                "expires_at": None,
            }
        )
    if (
        not cookies
        or len(cookies) > 50
        or len({cookie["name"] for cookie in cookies}) != len(cookies)
    ):
        raise ProberError(
            "AUTH_INPUT_INVALID", "Cookie input must contain 1..50 uniquely named cookies."
        )
    return cookies


class AuthStore:
    """Stores profiles without allowing callers to retrieve secret values."""

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    def _path(self, project_key: str, name: str, profile: AuthProfile) -> Path:
        return self.paths.profile_path(_profile_id(project_key, name, profile.origin))

    def _lock(self, project_key: str, name: str, profile: AuthProfile) -> FileLock:
        return FileLock(
            str(self.paths.profile_lock_path(_profile_id(project_key, name, profile.origin)))
        )

    def set_value(
        self, project_key: str, name: str, profile: AuthProfile, value: str
    ) -> dict[str, Any]:
        self.paths.ensure_directories()
        if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 16 * 1024:
            raise ProberError(
                "AUTH_INPUT_INVALID", "Authentication input must be a non-empty value up to 16 KiB."
            )
        if any(
            ord(character) < 32 and character not in "\t" or ord(character) == 127
            for character in value
        ):
            raise ProberError(
                "AUTH_INPUT_INVALID", "Authentication input contains control characters."
            )
        now = utc_now()
        record: dict[str, Any] = {
            "schema_version": 1,
            "project_key": project_key,
            "auth_profile_name": name,
            "origin": profile.origin,
            "auth_type": profile.type,
            "auth_config_hash": profile.config_hash(),
            "status": "valid",
            "created_at": now,
            "last_used_at": None,
            "invalid_reason": None,
            "expires_at": _jwt_exp(value) if profile.type in {"bearer", "header"} else None,
        }
        if profile.type == "cookie":
            assert profile.cookie is not None
            record["cookies"] = parse_cookie_bundle(value, profile.cookie.default_path)
        else:
            record["value"] = value
        with self._lock(project_key, name, profile):
            atomic_write_json(self._path(project_key, name, profile), record)
        return self.public_record(record)

    def load(self, project_key: str, name: str, profile: AuthProfile) -> dict[str, Any] | None:
        path = self._path(project_key, name, profile)
        if not path.exists():
            return None
        self.paths.require_safe_file(path)
        with self._lock(project_key, name, profile):
            record = load_json(path)
        if (
            record.get("auth_config_hash") != profile.config_hash()
            or record.get("auth_type") != profile.type
        ):
            record["status"] = "invalid"
            record["invalid_reason"] = "auth_config_changed"
            self._write(project_key, name, profile, record)
        expires_at = record.get("expires_at")
        if (
            isinstance(expires_at, str)
            and expires_at < utc_now()
            and record.get("status") == "valid"
        ):
            record["status"] = "expired"
            self._write(project_key, name, profile, record)
        return record

    def _write(
        self, project_key: str, name: str, profile: AuthProfile, record: dict[str, Any]
    ) -> None:
        with self._lock(project_key, name, profile):
            atomic_write_json(self._path(project_key, name, profile), record)

    def delete(self, project_key: str, name: str, profile: AuthProfile) -> bool:
        path = self._path(project_key, name, profile)
        if not path.exists():
            return False
        self.paths.require_safe_file(path)
        with self._lock(project_key, name, profile):
            path.unlink(missing_ok=True)
        return True

    def public_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = {
            "auth_profile_name": record["auth_profile_name"],
            "origin": record["origin"],
            "auth_type": record["auth_type"],
            "status": record["status"],
            "stored_value_count": len(record.get("cookies", []))
            if record["auth_type"] == "cookie"
            else 1,
            "created_at": record.get("created_at"),
            "last_used_at": record.get("last_used_at"),
            "expires_at": record.get("expires_at"),
        }
        if record["auth_type"] == "cookie":
            result["cookie_names"] = [cookie["name"] for cookie in record.get("cookies", [])]
        if record.get("invalid_reason"):
            result["invalid_reason"] = record["invalid_reason"]
        return result

    def inject(
        self, project_key: str, name: str, profile: AuthProfile, request_path: str, scheme: str
    ) -> tuple[dict[str, str], list[str]]:
        record = self.load(project_key, name, profile)
        if record is None:
            raise ProberError(
                "AUTH_REQUIRED",
                "Authentication profile has no locally stored value.",
                next_action="Call set_auth for this profile.",
            )
        if record.get("status") == "invalid":
            raise ProberError(
                "AUTH_INVALID",
                "Authentication profile is invalid.",
                {"reason": record.get("invalid_reason")},
                "Call set_auth again.",
            )
        if record.get("status") == "expired":
            raise ProberError(
                "AUTH_EXPIRED",
                "Authentication profile is expired.",
                next_action="Call set_auth again.",
            )
        headers: dict[str, str] = {}
        secret_values: list[str] = []
        if profile.type == "bearer":
            assert profile.bearer is not None
            value = str(record["value"])
            headers[profile.bearer.header_name] = profile.bearer.prefix + value
            secret_values.append(value)
        elif profile.type == "header":
            assert profile.header is not None
            value = str(record["value"])
            headers[profile.header.name] = profile.header.prefix + value
            secret_values.append(value)
        else:
            assert profile.cookie is not None
            outgoing: list[str] = []
            by_name: dict[str, str] = {}
            for cookie in record.get("cookies", []):
                if not request_path.startswith(str(cookie.get("path", "/"))):
                    continue
                if cookie.get("secure") and scheme != "https":
                    continue
                cookie_value = str(cookie["value"])
                outgoing.append(f"{cookie['name']}={cookie_value}")
                by_name[str(cookie["name"])] = cookie_value
                secret_values.append(cookie_value)
            if outgoing:
                headers["Cookie"] = "; ".join(outgoing)
            for csrf in profile.cookie.csrf_headers:
                if csrf.cookie_name in by_name:
                    source = by_name[csrf.cookie_name]
                    headers[csrf.header_name] = unquote(source) if csrf.decode == "url" else source
                    secret_values.append(source)
        record["last_used_at"] = utc_now()
        self._write(project_key, name, profile, record)
        return headers, secret_values

    def mark_invalid(self, project_key: str, name: str, profile: AuthProfile, reason: str) -> None:
        record = self.load(project_key, name, profile)
        if record is None:
            return
        record["status"] = "invalid"
        record["invalid_reason"] = reason
        self._write(project_key, name, profile, record)

    def update_set_cookie(
        self, project_key: str, name: str, profile: AuthProfile, set_cookie_headers: list[str]
    ) -> list[dict[str, str]]:
        if profile.type != "cookie" or not set_cookie_headers:
            return []
        record = self.load(project_key, name, profile)
        if record is None:
            return []
        cookies = {str(cookie["name"]): cookie for cookie in record.get("cookies", [])}
        updates: list[dict[str, str]] = []
        for raw in set_cookie_headers:
            parsed = SimpleCookie()
            try:
                parsed.load(raw)
            except (CookieError, ValueError):
                continue
            for morsel in parsed.values():
                name = morsel.key
                domain = morsel["domain"]
                if domain:
                    continue
                path = morsel["path"] or "/"
                max_age = morsel["max-age"]
                expired = max_age == "0"
                if not expired and morsel["expires"]:
                    try:
                        expired = parsedate_to_datetime(morsel["expires"]).astimezone(
                            UTC
                        ) <= datetime.now(UTC)
                    except (TypeError, ValueError):
                        pass
                if expired:
                    if name in cookies:
                        del cookies[name]
                        updates.append({"name": name, "action": "deleted"})
                    continue
                action = "updated" if name in cookies else "added"
                cookies[name] = {
                    "name": name,
                    "value": morsel.value,
                    "path": path,
                    "secure": bool(morsel["secure"]),
                    "expires_at": morsel["expires"] or None,
                }
                updates.append({"name": name, "action": action})
        if len(cookies) > 50:
            raise ProberError("AUTH_INVALID", "Cookie jar exceeded its 50-cookie limit.")
        record["cookies"] = list(cookies.values())
        self._write(project_key, name, profile, record)
        return updates
