"""Correlacion de peticion y bitacora de acceso del backend.

Un unico middleware ASGI hace las dos cosas porque ambas comparten el mismo
`request_id`: si fueran dos middleware, el que registra la respuesta podria
leer un identificador distinto del que se devolvio en la cabecera.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Final

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import build_internal_problem
from ticket_api.observability.logging import (
    log_access,
    reset_request_id,
    reset_user_id,
    set_request_id,
    set_user_id,
)

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER: Final = "X-Request-ID"

#: Un `request_id` entrante solo se conserva si es corto y sin caracteres de
#: control. Cualquier otra cosa se descarta y se genera uno: aceptar un valor
#: arbitrario permitiria inyectar saltos de linea o campos en la bitacora.
REQUEST_ID_MAX_LENGTH: Final = 64
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

_STATUS_KEY: Final = "http.response.start"


def normalize_request_id(raw: str | None) -> str:
    """Conserva un `request_id` entrante valido o genera uno nuevo."""
    if raw is not None:
        candidate = raw.strip()
        if _REQUEST_ID_RE.match(candidate):
            return candidate
    return str(uuid.uuid4())


def _header_value(scope: Scope, name: str) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == name.lower():
            decoded: str = value.decode("latin-1")
            return decoded
    return None


class RequestContextMiddleware:
    """Fija el `request_id` de la peticion y registra su respuesta."""

    def __init__(self, app: ASGIApp, *, app_name: str) -> None:
        self._app = app
        self._app_name = app_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = normalize_request_id(_header_value(scope, REQUEST_ID_HEADER))
        started = time.perf_counter()
        status_code = 500
        state: dict[str, Any] = scope.setdefault("state", {})
        state["request_id"] = request_id

        _request_token = set_request_id(request_id)
        token_user = set_user_id(None)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == _STATUS_KEY:
                status_code = int(message["status"])
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                headers.append(
                    (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1"))
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except Exception:
            # Se convierte aqui y no en un manejador de FastAPI: un manejador
            # de `Exception` corre en el `ServerErrorMiddleware`, que es exterior
            # a este middleware, asi que la respuesta se emitiria sin la cabecera
            # de correlacion y el 5xx quedaria imposible de trazar.
            logger.exception("excepcion no controlada", exc_info=True)
            state["problem_code"] = ErrorCode.INTERNAL_ERROR.value
            internal_problem = build_internal_problem(Request(scope, receive))
            await internal_problem(scope, receive, send_wrapper)
        finally:
            self._log(scope, status_code, started, request_id)
            reset_request_id(_request_token)
            reset_user_id(token_user)

    def _log(
        self,
        scope: Scope,
        status_code: int,
        started: float,
        request_id: str,
    ) -> None:
        route = scope.get("route")
        route_path = getattr(route, "path", None) or scope.get("path", "")
        state: dict[str, Any] = scope.get("state", {})
        log_access(
            app=self._app_name,
            method=scope.get("method", ""),
            route=route_path,
            path=scope.get("path", ""),
            status=status_code,
            duration_ms=(time.perf_counter() - started) * 1000,
            request_id=request_id,
            level="ERROR" if status_code >= 500 else "INFO",
            code=state.get("problem_code"),
        )
