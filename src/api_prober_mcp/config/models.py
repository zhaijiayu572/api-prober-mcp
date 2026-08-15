"""Strict configuration models and canonical URL/path helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from api_prober_mcp.errors import ProberError

SCHEMA_VERSION = 1
DEFAULT_PORTS = {"http": 80, "https": 443}
METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
HEADER_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
DANGEROUS_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-authorization",
    "proxy-connection",
    "upgrade",
    "keep-alive",
    "te",
    "trailer",
}


@dataclass(frozen=True, slots=True)
class Origin:
    scheme: str
    host: str
    port: int

    @property
    def text(self) -> str:
        default = DEFAULT_PORTS[self.scheme]
        host = f"[{self.host}]" if ":" in self.host else self.host
        suffix = "" if self.port == default else f":{self.port}"
        return f"{self.scheme}://{host}{suffix}"


def _contains_controls(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def normalize_origin(value: str) -> str:
    """Return a canonical exact origin or raise a validation error."""
    if not isinstance(value, str) or not value or len(value) > 2048 or _contains_controls(value):
        raise ValueError("origin must be a non-empty control-character-free string")
    if "\\" in value:
        raise ValueError("origin must not contain a backslash")
    parsed = urlsplit(value)
    if parsed.scheme not in DEFAULT_PORTS or not parsed.hostname:
        raise ValueError("origin must use http or https and include a host")
    if (
        parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("origin must contain only scheme, host, and optional port")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port or DEFAULT_PORTS[parsed.scheme]
    except ValueError as exc:
        raise ValueError("origin has an invalid host or port") from exc
    return Origin(parsed.scheme, host, port).text


def parse_origin(value: str) -> Origin:
    canonical = normalize_origin(value)
    parsed = urlsplit(canonical)
    assert parsed.hostname is not None
    return Origin(parsed.scheme, parsed.hostname, parsed.port or DEFAULT_PORTS[parsed.scheme])


def normalize_path(value: str) -> str:
    """Normalize only safe URL paths used for endpoint-policy matching."""
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 2048:
        raise ValueError("path must start with / and be at most 2048 characters")
    if _contains_controls(value) or "\\" in value or re.search(r"%(?:2f|5c)", value, re.I):
        raise ValueError("path contains a forbidden character or encoded separator")
    pieces: list[str] = []
    for part in value.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if pieces:
                pieces.pop()
            continue
        pieces.append(part)
    return "/" + "/".join(pieces)


def is_loopback_origin(origin: str) -> bool:
    parsed = parse_origin(origin)
    return parsed.host in {"localhost", "127.0.0.1", "::1"}


def _header_name(value: str) -> str:
    if not isinstance(value, str) or not HEADER_RE.fullmatch(value) or len(value) > 128:
        raise ValueError("header name is invalid")
    return value


def _simple_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("JSON path must be 1..256 characters")
    if any(
        not part or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", part) for part in value.split(".")
    ):
        raise ValueError("JSON path must be a simple dot path")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


class Limits(StrictModel):
    global_concurrency: int = Field(default=8, alias="globalConcurrency", ge=1, le=8)
    per_origin_concurrency: int = Field(default=4, alias="perOriginConcurrency", ge=1, le=4)
    default_timeout_seconds: float = Field(default=30, alias="defaultTimeoutSeconds", ge=1, le=300)
    max_timeout_seconds: float = Field(default=180, alias="maxTimeoutSeconds", ge=1, le=300)
    default_result_bytes: int = Field(
        default=20_480, alias="defaultResultBytes", ge=4096, le=1_048_576
    )
    max_result_bytes: int = Field(default=102_400, alias="maxResultBytes", ge=4096, le=1_048_576)
    max_response_bytes: int = Field(
        default=10_485_760, alias="maxResponseBytes", ge=4096, le=10_485_760
    )
    max_session_cache_bytes: int = Field(
        default=104_857_600, alias="maxSessionCacheBytes", ge=1_048_576, le=104_857_600
    )

    @model_validator(mode="after")
    def _limits_are_ordered(self) -> Limits:
        if self.max_timeout_seconds < self.default_timeout_seconds:
            raise ValueError("maxTimeoutSeconds must not be less than defaultTimeoutSeconds")
        if self.max_result_bytes < self.default_result_bytes:
            raise ValueError("maxResultBytes must not be less than defaultResultBytes")
        return self


class LoggingConfig(StrictModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    retention_days: int = Field(default=7, alias="retentionDays", ge=1, le=90)
    max_bytes: int = Field(default=52_428_800, alias="maxBytes", ge=1_048_576, le=1_073_741_824)


class ProxyConfig(StrictModel):
    url: str

    @field_validator("url")
    @classmethod
    def _valid_proxy(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("proxy URL must be http(s), host-only, and credential-free")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("proxy URL must not contain path, query, or fragment")
        return value


class GlobalResponseConfig(StrictModel):
    allowed_headers: list[str] = Field(default_factory=list, alias="allowedHeaders", max_length=50)

    @field_validator("allowed_headers")
    @classmethod
    def _headers(cls, values: list[str]) -> list[str]:
        return sorted({_header_name(value).lower() for value in values})


class DangerousOverrides(StrictModel):
    metadata_hosts: list[str] = Field(default_factory=list, alias="metadataHosts", max_length=10)

    @field_validator("metadata_hosts")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        return [normalize_origin(value) for value in values]


class GlobalConfig(StrictModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts", max_length=100)
    limits: Limits = Field(default_factory=Limits)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    proxy: ProxyConfig | None = None
    response: GlobalResponseConfig = Field(default_factory=GlobalResponseConfig)
    dangerous_overrides: DangerousOverrides = Field(
        default_factory=DangerousOverrides, alias="dangerousOverrides"
    )

    @field_validator("allowed_hosts")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        return sorted(set(normalize_origin(value) for value in values))


class ProjectDefaults(StrictModel):
    default_timeout_seconds: float | None = Field(
        default=None, alias="defaultTimeoutSeconds", ge=1, le=300
    )
    max_timeout_seconds: float | None = Field(default=None, alias="maxTimeoutSeconds", ge=1, le=300)
    default_result_bytes: int | None = Field(
        default=None, alias="defaultResultBytes", ge=4096, le=1_048_576
    )
    max_result_bytes: int | None = Field(
        default=None, alias="maxResultBytes", ge=4096, le=1_048_576
    )


class TlsRule(StrictModel):
    origin: str
    verify: bool

    @field_validator("origin")
    @classmethod
    def _https_origin(cls, value: str) -> str:
        origin = normalize_origin(value)
        if not origin.startswith("https://"):
            raise ValueError("TLS rule origin must be https")
        return origin


class BearerConfig(StrictModel):
    header_name: str = Field(default="Authorization", alias="headerName")
    prefix: str = Field(default="Bearer ", max_length=256)

    _name = field_validator("header_name")(_header_name)


class HeaderConfig(StrictModel):
    name: str
    prefix: str = Field(default="", max_length=256)

    _name = field_validator("name")(_header_name)


class CsrfHeader(StrictModel):
    cookie_name: str = Field(alias="cookieName", min_length=1, max_length=128)
    header_name: str = Field(alias="headerName")
    decode: Literal["none", "url"] = "none"

    _name = field_validator("header_name")(_header_name)

    @field_validator("cookie_name")
    @classmethod
    def _cookie_name(cls, value: str) -> str:
        if not COOKIE_NAME_RE.fullmatch(value):
            raise ValueError("cookie name is invalid")
        return value


class CookieConfig(StrictModel):
    default_path: str = Field(default="/", alias="defaultPath", min_length=1, max_length=512)
    csrf_headers: list[CsrfHeader] = Field(default_factory=list, alias="csrfHeaders", max_length=10)

    @field_validator("default_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return normalize_path(value)


class InvalidBodyRule(StrictModel):
    path: str
    equals: str | float | int | bool | None

    _path = field_validator("path")(_simple_path)


class InvalidWhen(StrictModel):
    status_codes: list[int] = Field(
        default_factory=lambda: [401], alias="statusCodes", max_length=20
    )
    body_rules: list[InvalidBodyRule] = Field(
        default_factory=list, alias="bodyRules", max_length=20
    )

    @field_validator("status_codes")
    @classmethod
    def _status_codes(cls, values: list[int]) -> list[int]:
        if any(value < 100 or value > 599 for value in values):
            raise ValueError("status code must be 100..599")
        return values


class AuthProfile(StrictModel):
    origin: str
    type: Literal["bearer", "header", "cookie"]
    bearer: BearerConfig | None = None
    header: HeaderConfig | None = None
    cookie: CookieConfig | None = None
    invalid_when: InvalidWhen = Field(default_factory=InvalidWhen, alias="invalidWhen")

    @field_validator("origin")
    @classmethod
    def _origin(cls, value: str) -> str:
        return normalize_origin(value)

    @model_validator(mode="after")
    def _only_one_auth_type(self) -> AuthProfile:
        sections = {"bearer": self.bearer, "header": self.header, "cookie": self.cookie}
        if sections[self.type] is None or any(
            value is not None for key, value in sections.items() if key != self.type
        ):
            raise ValueError("profile must define exactly the object matching type")
        header_name = self.managed_header_name
        if header_name and header_name.lower() in DANGEROUS_HEADERS:
            raise ValueError("authentication profile cannot manage a dangerous header")
        return self

    @property
    def managed_header_name(self) -> str | None:
        if self.type == "bearer" and self.bearer:
            return self.bearer.header_name
        if self.type == "header" and self.header:
            return self.header.name
        return None

    def config_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
        )
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


class EndpointRule(StrictModel):
    method: str
    origin: str
    path: str
    skip_confirmation: bool = Field(default=False, alias="skipConfirmation")
    timeout_seconds: float | None = Field(default=None, alias="timeoutSeconds", ge=1, le=300)
    max_result_bytes: int | None = Field(
        default=None, alias="maxResultBytes", ge=4096, le=1_048_576
    )

    @field_validator("method")
    @classmethod
    def _method(cls, value: str) -> str:
        value = value.upper()
        if value not in METHODS:
            raise ValueError("unsupported HTTP method")
        return value

    @field_validator("origin")
    @classmethod
    def _origin(cls, value: str) -> str:
        return normalize_origin(value)

    @field_validator("path")
    @classmethod
    def _template(cls, value: str) -> str:
        normalized = normalize_path(value)
        for part in normalized.split("/")[1:]:
            if part == "**" or re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_-]*\}", part) or part:
                continue
            raise ValueError("invalid path template")
        if "**" in normalized and not normalized.endswith("/**"):
            raise ValueError("** is only supported as a final path segment")
        return normalized

    def match_specificity(self, method: str, origin: str, path: str) -> int | None:
        if self.method != method or self.origin != origin:
            return None
        pattern_parts = self.path.strip("/").split("/") if self.path != "/" else []
        actual_parts = path.strip("/").split("/") if path != "/" else []
        if self.path.endswith("/**"):
            prefix = pattern_parts[:-1]
            if actual_parts[: len(prefix)] != prefix:
                return None
        elif len(pattern_parts) != len(actual_parts):
            return None
        score = 0
        for index, part in enumerate(pattern_parts):
            if part == "**":
                score += 1
                break
            if index >= len(actual_parts):
                return None
            if part.startswith("{"):
                score += 2
            elif part == actual_parts[index]:
                score += 4
            else:
                return None
        return score * 1000 + len(pattern_parts)


class ProjectResponseConfig(StrictModel):
    allowed_headers: list[str] = Field(default_factory=list, alias="allowedHeaders", max_length=50)
    sensitive_paths: list[str] = Field(default_factory=list, alias="sensitivePaths", max_length=50)

    @field_validator("allowed_headers")
    @classmethod
    def _headers(cls, values: list[str]) -> list[str]:
        return sorted({_header_name(value).lower() for value in values})

    @field_validator("sensitive_paths")
    @classmethod
    def _paths(cls, values: list[str]) -> list[str]:
        return [_simple_path(value) for value in values]


class ProjectConfig(StrictModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    project_key: str = Field(alias="projectKey", min_length=1, max_length=64)
    allowed_hosts: list[str] = Field(default_factory=list, alias="allowedHosts", max_length=100)
    defaults: ProjectDefaults = Field(default_factory=ProjectDefaults)
    tls: list[TlsRule] = Field(default_factory=list, max_length=100)
    auth_profiles: dict[str, AuthProfile] = Field(
        default_factory=dict, alias="authProfiles", max_length=100
    )
    endpoint_rules: list[EndpointRule] = Field(
        default_factory=list, alias="endpointRules", max_length=500
    )
    response: ProjectResponseConfig = Field(default_factory=ProjectResponseConfig)

    @field_validator("project_key")
    @classmethod
    def _project_key(cls, value: str) -> str:
        if not NAME_RE.fullmatch(value):
            raise ValueError(
                "projectKey may contain only ASCII letters, digits, dot, underscore, and hyphen"
            )
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def _origins(cls, values: list[str]) -> list[str]:
        return sorted(set(normalize_origin(value) for value in values))

    @model_validator(mode="after")
    def _references_are_valid(self) -> ProjectConfig:
        allowed = set(self.allowed_hosts)
        for tls in self.tls:
            if tls.origin not in allowed:
                raise ValueError("TLS rule origin must be in allowedHosts")
        for name, profile in self.auth_profiles.items():
            if not NAME_RE.fullmatch(name):
                raise ValueError("auth profile name is invalid")
            if profile.origin not in allowed and not is_loopback_origin(profile.origin):
                raise ValueError("auth profile origin must be in allowedHosts or loopback")
        seen: set[tuple[str, str, str]] = set()
        for rule in self.endpoint_rules:
            if rule.origin not in allowed and not is_loopback_origin(rule.origin):
                raise ValueError("endpoint rule origin must be in allowedHosts or loopback")
            identity = (rule.method, rule.origin, rule.path)
            if identity in seen:
                raise ValueError("duplicate endpoint rule")
            seen.add(identity)
        if (
            self.defaults.max_timeout_seconds is not None
            and self.defaults.default_timeout_seconds is not None
        ):
            if self.defaults.max_timeout_seconds < self.defaults.default_timeout_seconds:
                raise ValueError(
                    "project maxTimeoutSeconds must not be less than defaultTimeoutSeconds"
                )
        if (
            self.defaults.max_result_bytes is not None
            and self.defaults.default_result_bytes is not None
        ):
            if self.defaults.max_result_bytes < self.defaults.default_result_bytes:
                raise ValueError("project maxResultBytes must not be less than defaultResultBytes")
        return self

    def config_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"

    def matching_rule(self, method: str, origin: str, path: str) -> EndpointRule | None:
        matches = [
            (rule.match_specificity(method, origin, path), rule) for rule in self.endpoint_rules
        ]
        viable = [(score, rule) for score, rule in matches if score is not None]
        if not viable:
            return None
        viable.sort(key=lambda item: item[0], reverse=True)
        if len(viable) > 1 and viable[0][0] == viable[1][0]:
            raise ProberError("CONFIG_INVALID", "Endpoint rules have ambiguous specificity.")
        return viable[0][1]


def parse_global_config(value: object) -> GlobalConfig:
    try:
        return GlobalConfig.model_validate(value)
    except ValidationError as exc:
        raise ProberError(
            "CONFIG_INVALID",
            "Global configuration is invalid.",
            {"errors": exc.errors(include_url=False)},
        ) from exc


def parse_project_config(value: object) -> ProjectConfig:
    try:
        return ProjectConfig.model_validate(value)
    except ValidationError as exc:
        raise ProberError(
            "CONFIG_INVALID",
            "Project configuration is invalid.",
            {"errors": exc.errors(include_url=False)},
        ) from exc
