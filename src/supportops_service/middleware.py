"""Pure-ASGI middleware: request correlation and response hardening."""

from __future__ import annotations

import re
import uuid
from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER: Final = "X-Request-ID"
_VALID_ID: Final = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def get_request_id(scope: Scope) -> str:
    state: dict[str, Any] = scope.get("state") or {}
    return state.get("request_id") or str(uuid.uuid4())


class RequestContextMiddleware:
    """Accept a well-formed inbound ``X-Request-ID`` or generate one; always return it.

    ``/v1`` responses additionally get ``Cache-Control: no-store``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                inbound = value.decode("latin-1")
                break
        request_id = inbound if inbound and _VALID_ID.fullmatch(inbound) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        no_store = scope.get("path", "").startswith("/v1")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() != REQUEST_ID_HEADER.lower().encode()
                ]
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                if no_store:
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)
