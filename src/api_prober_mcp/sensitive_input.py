"""One-time loopback page used to receive credentials outside the agent context."""

from __future__ import annotations

import asyncio
import html
import secrets
from urllib.parse import parse_qs

from api_prober_mcp.errors import ProberError


class SensitiveInputPage:
    """A minimal local-only form with nonce, CSRF, and browser hardening."""

    def __init__(self, title: str, summary: str) -> None:
        self.title = title
        self.summary = summary
        self.nonce = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self._server: asyncio.AbstractServer | None = None
        self._submission: asyncio.Future[str] | None = None
        self._port: int | None = None

    @property
    def url(self) -> str:
        if self._port is None:
            raise RuntimeError("input page has not been started")
        return f"http://127.0.0.1:{self._port}/auth/{self.nonce}"

    async def start(self) -> None:
        self._submission = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockets = self._server.sockets
        assert sockets
        self._port = int(sockets[0].getsockname()[1])

    async def wait(self, timeout_seconds: float = 300) -> str:
        if self._submission is None:
            raise RuntimeError("input page has not been started")
        try:
            return await asyncio.wait_for(asyncio.shield(self._submission), timeout_seconds)
        except TimeoutError as exc:
            raise ProberError(
                "AUTH_INPUT_TIMEOUT", "The local credential input page timed out."
            ) from exc

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._submission and not self._submission.done():
            self._submission.cancel()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = line.decode("latin-1").rstrip("\r\n").split(" ")
            if len(parts) != 3:
                await self._reply(writer, 400, "Bad request")
                return
            method, target, _ = parts
            headers = await self._headers(reader)
            if not self._valid_host(headers.get("host")):
                await self._reply(writer, 403, "Invalid host")
                return
            expected = f"/auth/{self.nonce}"
            if target != expected:
                await self._reply(writer, 404, "Not found")
                return
            if method == "GET":
                await self._reply(
                    writer, 200, self._form(), content_type="text/html; charset=utf-8"
                )
                return
            if method == "POST":
                await self._post(reader, writer, headers)
                return
            await self._reply(writer, 405, "Method not allowed")
        except (UnicodeDecodeError, TimeoutError):
            await self._reply(writer, 400, "Bad request")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _headers(self, reader: asyncio.StreamReader) -> dict[str, str]:
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                return headers
            decoded = line.decode("latin-1").rstrip("\r\n")
            if ":" not in decoded or len(headers) >= 40:
                raise ValueError("invalid headers")
            name, value = decoded.split(":", 1)
            headers[name.lower()] = value.strip()

    def _valid_host(self, host: str | None) -> bool:
        return self._port is not None and host == f"127.0.0.1:{self._port}"

    async def _post(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict[str, str]
    ) -> None:
        content_type = headers.get("content-type", "").split(";", 1)[0].lower()
        origin = headers.get("origin")
        if (
            content_type != "application/x-www-form-urlencoded"
            or origin != f"http://127.0.0.1:{self._port}"
        ):
            await self._reply(writer, 403, "Invalid form submission")
            return
        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= 16 * 1024:
            await self._reply(writer, 413, "Input is too large")
            return
        body = await reader.readexactly(length)
        data = parse_qs(body.decode("utf-8"), strict_parsing=True, max_num_fields=3)
        if data.get("csrf", [None])[0] != self.csrf:
            await self._reply(writer, 403, "Invalid CSRF token")
            return
        value = data.get("value", [""])[0]
        if not value:
            await self._reply(writer, 400, "A value is required")
            return
        if self._submission and not self._submission.done():
            self._submission.set_result(value)
        await self._reply(writer, 200, "Credential saved. You can return to your MCP client.")

    def _form(self) -> str:
        return f"""<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(self.title)}</title></head>
<body><h1>{html.escape(self.title)}</h1><p>{html.escape(self.summary)}</p>
<form method=\"post\" action=\"/auth/{self.nonce}\"><input type=\"hidden\" name=\"csrf\" value=\"{self.csrf}\">
<label>Credential <input name=\"value\" type=\"password\" autocomplete=\"off\" autofocus></label><button type=\"submit\">Save</button></form></body></html>"""

    async def _reply(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        encoded = body.encode("utf-8")
        reason = {
            200: "OK",
            400: "Bad Request",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            413: "Payload Too Large",
        }.get(status, "Error")
        writer.write(
            (
                f"HTTP/1.1 {status} {reason}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(encoded)}\r\n"
                "Cache-Control: no-store\r\n"
                "Referrer-Policy: no-referrer\r\n"
                "Content-Security-Policy: default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'\r\n"
                "X-Content-Type-Options: nosniff\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            + encoded
        )
        await writer.drain()
