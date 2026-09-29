"""Aplicacion web Flask, renderizada en servidor.

La web no es un cliente de dominio: renderiza HTML y delega toda operacion en
FastAPI por HTTP interno. No importa SQLAlchemy ni toca la base de datos.
"""

from __future__ import annotations

from typing import Final

from flask import Flask, Response, jsonify, render_template

from ticket_web.api_client import ApiClient, ApiLike, ApiProblemError, ApiUnavailableError
from ticket_web.config import Settings, get_settings
from ticket_web.observability.logging import configure_logging
from ticket_web.observability.middleware import (
    REQUEST_ID_HEADER,
    current_request_id,
    register_request_context,
    set_problem_code,
)

API_CLIENT_KEY: Final = "api_client"

#: Ruta de la API que responde 404 a proposito. La usa la sonda del demo de F1
#: para comprobar que un `problem+json` llega a la web con su codigo de negocio.
API_PROBE_PATH: Final = "/api/v1/no-existe"


def create_app(
    settings: Settings | None = None,
    *,
    api_client: ApiLike | None = None,
) -> Flask:
    """Construye la instancia de la web."""
    resolved = settings or get_settings()
    configure_logging(resolved.app_name, resolved.log_level)

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    # La cookie de sesion es opaca y la emite 6.1; todavia no hay contenido que
    # proteger, pero la configuracion se fija aqui para que el contrato no
    # cambie cuando se anadan los tokens.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="ticketplus_session",
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=False,
    )

    register_request_context(app, app_name=resolved.app_name)

    client = api_client or ApiClient(
        resolved.api_base_url,
        timeout_seconds=resolved.api_timeout_seconds,
    )
    app.extensions[API_CLIENT_KEY] = client

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        """Comprobacion de vida propia de la web, sin depender de la API."""
        return {"status": "ok", "app": resolved.app_name}, 200

    @app.get("/")
    def index() -> str:
        return render_template("base.html", app_name=resolved.app_name)

    @app.get("/api-echo")
    def api_echo() -> Response:
        """Sonda de la demo de F1: reenvia a la API y enseña los dos lados de
        la correlacion.

        `web_request_id` es el identificador de la peticion del navegador y
        `api_request_id` es el que la API devolvio tras el salto. Que coincidan
        es la prueba de que el identificador se propago y no se regenero.
        """
        response = app.extensions[API_CLIENT_KEY].get("/api/v1/ping")
        return jsonify(
            {
                "web_request_id": current_request_id(),
                "api_request_id": response.headers.get(REQUEST_ID_HEADER),
                "api_status": response.status,
                "api_payload": response.data,
            }
        )

    @app.get("/api-probe")
    def api_probe() -> Response:
        """Sonda que fuerza un 404 de la API para ver como llega el error.

        No captura la excepcion a proposito: el manejador registrado abajo la
        traduce, que es el camino quehara cualquier vista real.
        """
        respuesta = app.extensions[API_CLIENT_KEY].get(API_PROBE_PATH)
        return jsonify({"api_status": respuesta.status, "api_payload": respuesta.data})

    @app.errorhandler(ApiProblemError)
    def _on_api_problem(exc: ApiProblemError) -> tuple[Response, int]:
        """Traduce un `problem+json` de la API a la respuesta de la web.

        Se conserva el codigo de negocio y no solo el texto: la vista decide que
        formulario o mensaje mostrar a partir de el.
        """
        set_problem_code(exc.code)
        problema = exc.problem
        return (
            jsonify(
                {
                    "error": {
                        "code": problema.code,
                        "title": problema.title,
                        "status": problema.status,
                        "detail": problema.detail,
                        "errors": problema.errors,
                    },
                    "request_id": current_request_id(),
                }
            ),
            problema.status,
        )

    @app.errorhandler(ApiUnavailableError)
    def _on_api_unavailable(exc: ApiUnavailableError) -> tuple[Response, int]:
        """Fallo de integracion: 503 con un mensaje redactado por la web.

        El motivo de httpx no sale nunca al usuario; queda solo en la bitacora,
        junto al identificador de correlacion para poder cruzarlo con el backend.
        """
        set_problem_code(
            exc.code,
            mensaje=f"llamada a la API sin exito: {exc.fallo.value}",
        )
        return (
            jsonify(
                {
                    "error": {
                        "code": exc.code,
                        "title": "Servicio no disponible",
                        "status": 503,
                        "detail": "No pudimos completar la operacion. Intenta de nuevo.",
                        "errors": [],
                    },
                    "request_id": current_request_id(),
                }
            ),
            503,
        )

    return app


__all__ = ["API_CLIENT_KEY", "API_PROBE_PATH", "REQUEST_ID_HEADER", "create_app"]
