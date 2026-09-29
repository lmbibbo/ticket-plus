"""Bitacora HTTP estructurada del backend.

Dos controles independientes y la intencion es que ninguno baste:

1. **Lista permitida.** Solo los campos de `ALLOWED_LOG_FIELDS` llegan al
   registro. El constructor de la linea nunca recibe el objeto `Request`, asi
   que no hay forma de volcar cabeceras, cookies ni cuerpos por descuido.
2. **Redaccion.** Los valores de texto libre pasan por `scrub`, que elimina
   JWT, cabeceras `Authorization`/`Cookie`, pares clave-valor de campos
   sensibles y blobs opacos largos (codigos de pago, cargas QR).

Los campos que la aplicacion genera y valida ella misma (`request_id`, `app`,
`code`, `user_id`, ...) estan exentos del paso de redaccion: si `request_id`
fuera redactado se perderia la correlacion, que es justo lo que el registro
existe para dar.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final

ACCESS_LOGGER_NAME: Final = "ticket_api.access"

#: Campos que pueden aparecer en una linea de bitacora. Cualquier otro se descarta.
ALLOWED_LOG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "timestamp",
        "app",
        "level",
        "method",
        "route",
        "path",
        "status",
        "duration_ms",
        "request_id",
        "user_id",
        "code",
        "message",
    }
)

#: Campos exentos de redaccion: los genera y valida la propia aplicacion.
EXEMPT_FROM_SCRUB: Final[frozenset[str]] = frozenset(
    {
        "timestamp",
        "app",
        "level",
        "method",
        "route",
        "status",
        "duration_ms",
        "request_id",
        "user_id",
        "code",
    }
)

REDACTED: Final = "[redactado]"

_SENSITIVE_KEY = (
    r"password|passwd|secret|token|refresh[_-]?token|access[_-]?token|"
    r"api[_-]?key|authorization|cookie|session[_-]?id|csrf|cvv|cvc|"
    r"card[_-]?number|qr|payload|signature"
)
_SCRUBBERS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # JWT: cabecera base64url seguida de dos segmentos.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), REDACTED),
    # Cabeceras de credenciales.
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    (re.compile(r"(?i)\b(cookie|set-cookie)\s*:\s*[^\r\n]+"), rf"\1: {REDACTED}"),
    # Pares clave-valor de campos sensibles, en query, JSON o cabeceras.
    # La comilla de cierre opcional cubre la forma JSON: {"password": "x"}.
    (
        re.compile(rf"(?i)\b({_SENSITIVE_KEY})\b\"?\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;&}}\]]+)"),
        rf"\1={REDACTED}",
    ),
    # Blob opaco largo: codigos de pago, cargas QR, tokens sin prefijo reconocible.
    (re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}"), REDACTED),
)

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_request_id(value: str | None) -> Token[str | None]:
    """Fija el identificador de correlacion del contexto actual.

    Devuelve el token para deshacerlo con `reset_request_id`.
    """
    return _request_id_var.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    """Restaura el identificador de correlacion anterior."""
    _request_id_var.reset(token)


def get_request_id() -> str | None:
    """Devuelve el identificador de correlacion del contexto actual."""
    return _request_id_var.get()


def set_user_id(value: str | None) -> Token[str | None]:
    """Fija el usuario autenticado del contexto actual, si lo hay."""
    return _user_id_var.set(value)


def reset_user_id(token: Token[str | None]) -> None:
    """Restaura el usuario autenticado anterior."""
    _user_id_var.reset(token)


def get_user_id() -> str | None:
    """Devuelve el usuario autenticado del contexto actual, si lo hay."""
    return _user_id_var.get()


def scrub(value: str) -> str:
    """Elimina de un texto libre los fragmentos con forma de dato sensible."""
    for pattern, replacement in _SCRUBBERS:
        value = pattern.sub(replacement, value)
    return value


def _clean_value(key: str, value: Any) -> Any:
    if value is None or key in EXEMPT_FROM_SCRUB:
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, int | float | bool):
        return value
    return scrub(json.dumps(value, default=str, ensure_ascii=False))


def build_record(fields: dict[str, Any], *, app: str) -> dict[str, Any]:
    """Construye una linea de bitacora descartando todo campo no permitido.

    `timestamp` y `app` los anade siempre la funcion y no pueden ser
    sobrescritos por quien la llama.
    """
    record: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "app": app,
    }
    for key, value in fields.items():
        if key not in ALLOWED_LOG_FIELDS or key in record:
            continue
        if value is None:
            continue
        record[key] = _clean_value(key, value)
    return record


class JsonLineFormatter(logging.Formatter):
    """Formatea un `LogRecord` como una unica linea JSON."""

    def __init__(self, app: str) -> None:
        super().__init__()
        self._app = app

    def format(self, record: logging.LogRecord) -> str:
        # `log_access` entrega la linea ya filtrada por la lista permitida.
        if getattr(record, "prebuilt", False):
            return record.getMessage()
        line = build_record(
            {
                "level": record.levelname,
                "message": record.getMessage(),
                "request_id": getattr(record, "request_id", None),
            },
            app=self._app,
        )
        return json.dumps(line, ensure_ascii=False, default=str)


def get_access_logger() -> logging.Logger:
    """Devuelve el logger de la bitacora de acceso del backend."""
    return logging.getLogger(ACCESS_LOGGER_NAME)


def configure_logging(app: str, level: str) -> None:
    """Configura la salida JSON en la salida estandar.

    No toca los loggers de uvicorn: la bitacora de acceso es nuestra y tiene su
    propio formato para que la correlacion no dependa del nivel de detalle de
    las librerias.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter(app))

    logger = get_access_logger()
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False


def log_access(
    *,
    app: str,
    method: str,
    route: str,
    status: int,
    duration_ms: float,
    request_id: str,
    level: str = "INFO",
    user_id: str | None = None,
    code: str | None = None,
    message: str | None = None,
    path: str | None = None,
) -> None:
    """Emite una linea de acceso ya filtrada por la lista permitida."""
    line = build_record(
        {
            "level": level,
            "method": method,
            "route": route,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 3),
            "request_id": request_id,
            "user_id": user_id or get_user_id(),
            "code": code,
            "message": message,
        },
        app=app,
    )
    logger = get_access_logger()
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        json.dumps(line, ensure_ascii=False, default=str),
        extra={"prebuilt": True},
    )
