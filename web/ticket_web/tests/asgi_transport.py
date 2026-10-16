"""Puente de una app ASGI a un transporte síncrono de httpx.

`httpx.ASGITransport` es asíncrono y no puede usarse con un `Client` síncrono,
que es lo que necesita Flask. Este adaptador mantiene el cliente real y solo
sustituye el transporte, de modo que la petición sigue recorriendo el cliente,
las cabeceras, el middleware de Flask y el de FastAPI.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

_SCOPE_KEYS_HTTP = "http"


class SyncASGITransport(httpx.BaseTransport):
    """Ejecuta una app ASGI desde un transporte de httpx síncrono."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        sent: list[dict[str, Any]] = []
        request_sent = False

        scope: dict[str, Any] = {
            "type": _SCOPE_KEYS_HTTP,
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(name.lower(), value) for name, value in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
        }

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        asyncio.run(self._app(scope, receive, send))

        start = next(m for m in sent if m["type"] == "http.response.start")
        chunks = [m.get("body", b"") for m in sent if m["type"] == "http.response.body"]
        return httpx.Response(
            status_code=int(start["status"]),
            headers=[
                (name.decode("latin-1"), value.decode("latin-1"))
                for name, value in start["headers"]
            ],
            content=b"".join(chunks),
            request=request,
        )
