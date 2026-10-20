"""Correlacion de peticion y bitacora de acceso de la aplicacion web.

Es el equivalente Flask de `ticket_api.observability.middleware`. Comparte la
misma politica de identificador: se conserva el entrante si es valido, se genera
uno si no lo es, y la cabecera de respuesta lleva siempre el valor que se
registro.
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Final

from flask import Flask, Response, g, request
from werkzeug.exceptions import HTTPException

from ticket_web.observability.logging import (
    get_request_id,
    log_access,
    reset_request_id,
    reset_user_id,
    set_request_id,
    set_user_id,
)

REQUEST_ID_HEADER: Final = "X-Request-ID"
REQUEST_ID_MAX_LENGTH: Final = 64
_REQUEST_ID_RE: Final = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

_KEY_REQUEST_ID: Final = "request_id"
_KEY_STARTED: Final = "started_at"
_KEY_LOGGED: Final = "access_logged"
_KEY_CODE: Final = "problem_code"
_KEY_MESSAGE: Final = "operational_message"
_KEY_TOKENS: Final = "context_tokens"


def normalize_request_id(raw: str | None) -> str:
    """Conserva un `request_id` entrante valido o genera uno nuevo."""
    if raw is not None:
        candidate = raw.strip()
        if _REQUEST_ID_RE.match(candidate):
            return candidate
    return str(uuid.uuid4())


def set_problem_code(code: str, *, mensaje: str | None = None) -> None:
    """Deja el codigo de negocio y el motivo operativo para la linea de acceso.

    Lo usan los manejadores que traducen lo que devuelve la API: sin el codigo,
    la web registraria un 4xx sin causa y habria que cruzarlo a mano con el
    backend para saber por que fallo.

    El `mensaje` va redactado por quien lo escribe. La excepcion original de
    httpx nunca se pasa aqui: puede contener hosts internos o cabeceras.
    """
    setattr(g, _KEY_CODE, code)
    setattr(g, _KEY_MESSAGE, mensaje)


def current_request_id() -> str:
    """Devuelve el `request_id` de la peticion en curso.

    Funciona tambien fuera de una peticion Flask (tareas en segundo plano,
    comandos de linea). Sin contexto no se lanza: el cliente HTTP necesita un
    identificador que enviar a la API de todas formas, y generarlo aqui es
    preferible a propagar el fallo hasta la vista.
    """
    try:
        value: Any = getattr(g, _KEY_REQUEST_ID, None)
    except RuntimeError:
        value = None
    if isinstance(value, str):
        return value

    desde_contexto = get_request_id()
    if desde_contexto:
        return desde_contexto
    return normalize_request_id(None)


def register_request_context(app: Flask, *, app_name: str) -> None:
    """Engancha la correlacion y la bitacora a los ciclos de vida de Flask."""

    @app.before_request
    def _begin() -> None:
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        setattr(g, _KEY_REQUEST_ID, request_id)
        setattr(g, _KEY_STARTED, time.perf_counter())
        setattr(g, _KEY_LOGGED, False)
        setattr(g, _KEY_CODE, None)
        setattr(g, _KEY_MESSAGE, None)
        # Sin guardar los tokens, un servidor de un solo hilo arrastraria el
        # `request_id` de una peticion a la siguiente.
        setattr(
            g,
            _KEY_TOKENS,
            (set_request_id(request_id), set_user_id(None)),
        )
        request.environ["ticketplus.request_id"] = request_id

    @app.after_request
    def _finish(response: Response) -> Response:
        _log_once(app_name=app_name, response=response)
        response.headers[REQUEST_ID_HEADER] = current_request_id()
        return response

    @app.errorhandler(Exception)
    def _on_exception(exc: Exception) -> tuple[Response, int, dict[str, str]]:
        from flask import jsonify

        from ticket_web.observability.logging import get_request_id

        _log_once(
            app_name=app_name,
            response=None,
            status=500,
            level="ERROR",
            message="error no controlado en la aplicacion web",
        )
        return (
            jsonify(
                {
                    "code": "internal_error",
                    "title": "Error interno",
                    "status": 500,
                    "detail": "Ocurrio un error interno.",
                    "request_id": get_request_id(),
                }
            ),
            500,
            {REQUEST_ID_HEADER: current_request_id()},
        )

    @app.errorhandler(HTTPException)
    def _on_http_error(exc: HTTPException) -> tuple[Response, int]:
        """Keep the real status of HTTP-level errors (404, 405, ...) in a flat body.

        Without this handler the generic `Exception` one swallows every
        `HTTPException` - Flask resolves handlers walking the exception's MRO -
        and a missing URL would answer `500 internal_error`, hiding real faults
        behind false server errors. Redirects (status < 400, raised by
        `abort(redirect(...))`) pass through untouched so a redirect response is
        never wrapped in JSON.
        """
        from flask import jsonify, make_response

        if exc.code is not None:
            status = exc.code
        elif exc.response is not None:
            status = exc.response.status_code
        else:
            status = 500
        if status < 400:
            return make_response(exc.get_response(request.environ)), status

        name = exc.name or "Internal Server Error"
        _log_once(
            app_name=app_name,
            response=None,
            status=status,
            level="ERROR" if status >= 500 else "INFO",
        )
        return (
            jsonify(
                {
                    "code": name.lower().replace(" ", "_"),
                    "title": name,
                    "status": status,
                    "detail": exc.description or name,
                    "request_id": current_request_id(),
                }
            ),
            status,
        )

    @app.teardown_request
    def _teardown(exc: BaseException | None) -> None:
        tokens = getattr(g, _KEY_TOKENS, None)
        if tokens is None:
            return
        request_token, user_token = tokens
        reset_user_id(user_token)
        reset_request_id(request_token)


def _log_once(
    *,
    app_name: str,
    response: Response | None,
    status: int | None = None,
    level: str = "INFO",
    message: str | None = None,
) -> None:
    """Registra la linea de acceso una sola vez por peticion.

    Flask puede pasar por `after_request` y por `errorhandler` en el mismo
    fallo; sin este guardia la misma peticion apareceria dos veces en la
    bitacora y las metricas quedarian infladas.
    """
    if getattr(g, _KEY_LOGGED, False):
        return
    setattr(g, _KEY_LOGGED, True)
    code: str | None = getattr(g, _KEY_CODE, None)
    operativo: str | None = getattr(g, _KEY_MESSAGE, None)

    started = getattr(g, _KEY_STARTED, None)
    duration_ms = (time.perf_counter() - started) * 1000 if isinstance(started, float) else 0.0
    log_access(
        app=app_name,
        method=request.method,
        route=request.url_rule.rule if request.url_rule else request.path,
        path=request.path,
        status=status if status is not None else (response.status_code if response else 500),
        duration_ms=duration_ms,
        request_id=current_request_id(),
        level=level,
        code=code,
        message=message or operativo,
    )
