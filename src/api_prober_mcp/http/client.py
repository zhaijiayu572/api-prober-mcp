"""Controlled HTTP execution and network policy checks."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from api_prober_mcp.config.models import (
    DANGEROUS_HEADERS,
    METHODS,
    SAFE_METHODS,
    AuthProfile,
    GlobalConfig,
    ProjectConfig,
    normalize_origin,
    normalize_path,
    parse_origin,
)
from api_prober_mcp.errors import ProberError

Confirmation = Callable[[str, str], Awaitable[bool]]


@dataclass(slots=True)
class RawHttpResponse:
    status: int
    headers: dict[str, str]
    set_cookie_headers: list[str]
    content: bytes
    final_url: str
    redirects: list[dict[str, Any]]
    attempts: int
    duration_ms: int
    queue_ms: int
    tls_verified: bool | None
    proxy_used: bool


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def validate_url(value: str) -> tuple[str, str, str]:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or _has_control(value)
        or "\\" in value
    ):
        raise ProberError("REQUEST_INVALID", "URL is invalid.")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ProberError(
            "REQUEST_INVALID", "URL must be a credential-free HTTP(S) URL without a fragment."
        )
    if "%2f" in parsed.path.lower() or "%5c" in parsed.path.lower():
        raise ProberError("REQUEST_INVALID", "URL contains an encoded path separator.")
    try:
        origin = normalize_origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
        path = normalize_path(parsed.path or "/")
    except ValueError as exc:
        raise ProberError("REQUEST_INVALID", "URL is invalid.") from exc
    canonical = urlunsplit((parse_origin(origin).scheme, parsed.netloc, path, parsed.query, ""))
    return canonical, origin, path


def validate_headers(headers: dict[str, str] | None, managed_headers: set[str]) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > 100:
        raise ProberError("REQUEST_INVALID", "headers must be an object with at most 100 entries.")
    result: dict[str, str] = {}
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not isinstance(value, str)
            or len(name) > 128
            or len(value) > 8192
        ):
            raise ProberError("REQUEST_INVALID", "A header name or value exceeds its limit.")
        lower = name.lower()
        if (
            _has_control(name)
            or _has_control(value)
            or lower in DANGEROUS_HEADERS
            or lower in managed_headers
        ):
            raise ProberError(
                "REQUEST_INVALID", "A request header is managed or forbidden.", {"header": name}
            )
        result[name] = value
    return result


def _blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _metadata_ip(address: str) -> bool:
    return address in {"169.254.169.254", "fd00:ec2::254"}


class HttpExecutor:
    def __init__(self, global_config: GlobalConfig) -> None:
        self.global_config = global_config
        self._global = asyncio.Semaphore(global_config.limits.global_concurrency)
        self._per_origin: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(global_config.limits.per_origin_concurrency)
        )

    async def _assert_network_safe(
        self, origin: str, *, allow_loopback: bool, metadata_allowed: bool
    ) -> None:
        parsed = parse_origin(origin)
        try:
            literal_address = str(ipaddress.ip_address(parsed.host))
        except ValueError:
            literal_address = None
        if literal_address is not None:
            addresses = {literal_address}
        else:
            try:
                results = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: socket.getaddrinfo(parsed.host, parsed.port, type=socket.SOCK_STREAM),
                )
            except socket.gaierror as exc:
                raise ProberError(
                    "REQUEST_FAILED", "DNS resolution failed.", {"origin": origin}
                ) from exc
            addresses = {str(item[4][0]) for item in results}
        if not addresses:
            raise ProberError(
                "REQUEST_FAILED", "DNS resolution returned no addresses.", {"origin": origin}
            )
        for address in addresses:
            if _metadata_ip(address) and not metadata_allowed:
                raise ProberError(
                    "METADATA_TARGET_BLOCKED",
                    "Cloud metadata targets are blocked.",
                    {"origin": origin},
                )
            if _blocked_ip(address) and not (
                allow_loopback and ipaddress.ip_address(address).is_loopback
            ):
                raise ProberError(
                    "HOST_NOT_APPROVED",
                    "The target resolves to a blocked address.",
                    {"origin": origin, "address": address},
                )

    async def execute(
        self,
        *,
        method: str,
        url: str,
        project: ProjectConfig,
        approved_origins: set[str],
        headers: dict[str, str],
        content: bytes | None,
        timeout_seconds: float,
        auth_profile: AuthProfile | None,
        confirmation: Confirmation,
        proxy_allowed: bool,
    ) -> RawHttpResponse:
        if method not in METHODS:
            raise ProberError("REQUEST_INVALID", "HTTP method is unsupported.")
        current_url, origin, path = validate_url(url)
        redirects: list[dict[str, Any]] = []
        request_headers = dict(headers)
        request_content = content
        current_method = method
        profile_active = auth_profile is not None
        attempts = 0
        started = time.monotonic()
        queue_started = started
        try:
            await asyncio.wait_for(self._global.acquire(), timeout=timeout_seconds)
            acquired_global = True
        except TimeoutError as exc:
            raise ProberError(
                "REQUEST_TIMEOUT", "Request timed out while waiting for global capacity."
            ) from exc
        origin_semaphore = self._per_origin[origin]
        try:
            remaining = max(0.1, timeout_seconds - (time.monotonic() - started))
            await asyncio.wait_for(origin_semaphore.acquire(), timeout=remaining)
        except TimeoutError as exc:
            self._global.release()
            raise ProberError(
                "REQUEST_TIMEOUT", "Request timed out while waiting for origin capacity."
            ) from exc
        queue_ms = int((time.monotonic() - queue_started) * 1000)
        try:
            while True:
                is_loopback = parse_origin(origin).host in {"localhost", "127.0.0.1", "::1"}
                metadata_allowed = origin in self.global_config.dangerous_overrides.metadata_hosts
                await self._assert_network_safe(
                    origin, allow_loopback=is_loopback, metadata_allowed=metadata_allowed
                )
                remaining = max(0.1, timeout_seconds - (time.monotonic() - started))
                verify = True
                for rule in project.tls:
                    if rule.origin == origin:
                        verify = rule.verify
                proxy = (
                    self.global_config.proxy.url
                    if self.global_config.proxy and not is_loopback
                    else None
                )
                transport = httpx.AsyncHTTPTransport(retries=0, verify=verify, proxy=proxy)
                timeout = httpx.Timeout(remaining)
                try:
                    async with httpx.AsyncClient(
                        transport=transport,
                        trust_env=False,
                        follow_redirects=False,
                        timeout=timeout,
                    ) as client:
                        attempts += 1
                        async with client.stream(
                            current_method,
                            current_url,
                            headers=request_headers,
                            content=request_content,
                        ) as response:
                            chunks: list[bytes] = []
                            downloaded = 0
                            async for chunk in response.aiter_bytes():
                                downloaded += len(chunk)
                                if downloaded > self.global_config.limits.max_response_bytes:
                                    raise ProberError(
                                        "RESPONSE_TOO_LARGE",
                                        "Response exceeded the 10 MiB download limit.",
                                    )
                                chunks.append(chunk)
                            content_bytes = b"".join(chunks)
                            status = response.status_code
                            response_headers = {
                                name: value for name, value in response.headers.items()
                            }
                            set_cookie = response.headers.get_list("set-cookie")
                            location = response.headers.get("location")
                except ProberError:
                    raise
                except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                    if current_method in SAFE_METHODS and attempts < 2:
                        continue
                    raise ProberError(
                        "REQUEST_FAILED", "HTTP connection or protocol request failed."
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise ProberError("REQUEST_TIMEOUT", "HTTP request timed out.") from exc
                if status in {502, 503, 504} and current_method in SAFE_METHODS and attempts < 2:
                    continue
                if status not in {301, 302, 303, 307, 308} or not location:
                    return RawHttpResponse(
                        status=status,
                        headers=response_headers,
                        set_cookie_headers=set_cookie,
                        content=content_bytes,
                        final_url=current_url,
                        redirects=redirects,
                        attempts=attempts,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        queue_ms=queue_ms,
                        tls_verified=verify if origin.startswith("https://") else None,
                        proxy_used=proxy is not None,
                    )
                if len(redirects) >= 5:
                    raise ProberError(
                        "REDIRECT_LIMIT_EXCEEDED", "Redirect limit of 5 was exceeded."
                    )
                next_url = urljoin(current_url, location)
                next_url, next_origin, _ = validate_url(next_url)
                redirects.append(
                    {"status": status, "from": origin, "to": next_origin, "method": current_method}
                )
                if next_origin != origin:
                    if not await confirmation(
                        "redirect",
                        f"Allow redirect from {origin} to {next_origin} without authentication or body?",
                    ):
                        raise ProberError(
                            "HOST_CONFIRMATION_DECLINED", "User declined the cross-origin redirect."
                        )
                    request_headers = {
                        name: value
                        for name, value in request_headers.items()
                        if name.lower() not in {"authorization", "cookie"}
                    }
                    request_content = None
                    profile_active = False
                if status in {301, 302, 303} and current_method not in SAFE_METHODS:
                    current_method = "GET"
                    request_content = None
                elif status in {307, 308} and current_method not in SAFE_METHODS:
                    if not await confirmation(
                        "method", f"Repeat {current_method} request after a {status} redirect?"
                    ):
                        raise ProberError(
                            "METHOD_CONFIRMATION_DECLINED",
                            "User declined to repeat a non-read-only redirected request.",
                        )
                current_url, origin = next_url, next_origin
                if profile_active:
                    # Authentication stays only on same origin; headers were already injected by caller.
                    pass
        finally:
            origin_semaphore.release()
            if acquired_global:
                self._global.release()
