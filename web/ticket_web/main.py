"""Aplicacion web Flask, renderizada en servidor.

La web no es un cliente de dominio: renderiza HTML y delega toda operacion en
FastAPI por HTTP interno. No importa SQLAlchemy ni toca la base de datos.

Aqui solo se ensambla la aplicacion: configuracion, sesion, cliente de la API,
correlacion de peticiones y traduccion de errores. Las vistas viven en
`ticket_web.blueprints`.
"""

from __future__ import annotations

from typing import Final

from flask import Flask, Response, jsonify, request

from ticket_web.api_client import (
    API_CLIENT_KEY,
    ApiClient,
    ApiLike,
    ApiProblemError,
    ApiUnavailableError,
)
from ticket_web.auth import identidad_actual
from ticket_web.blueprints import register_blueprints
from ticket_web.blueprints.system import API_PROBE_PATH
from ticket_web.config import Settings, get_settings
from ticket_web.csrf import registrar_csrf
from ticket_web.observability.logging import configure_logging
from ticket_web.observability.middleware import (
    REQUEST_ID_HEADER,
    current_request_id,
    register_request_context,
    set_problem_code,
)
from ticket_web.sesion_api import cabecera_bearer, necesita_renovacion, renovar_sesion
from ticket_web.session_store import ServerSideSessionInterface

#: Rutas donde la renovacion no aporta nada: la sesion se va a escribir o a borrar
#: entera en la propia vista, asi que rotar antes solo gastaria una llamada a la
#: API. `static` esta porque los estaticos no llevan sesion.
RUTAS_SIN_RENOVACION: Final = frozenset(
    {
        "auth.login_enviar",
        "auth.registro_enviar",
        "auth.salir",
        "static",
    }
)

#: Atributos de configuracion propios de la web, ajenos a las claves que Flask
#: ya define por su cuenta.
APP_CONFIG_KEYS = {
    "APP_NAME": str,
    "SESSION_COOKIE_HTTPONLY": bool,
    "SESSION_COOKIE_SECURE": bool,
    "SESSION_COOKIE_SAMESITE": str,
    "SESSION_COOKIE_NAME": str,
}


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
    app.config.update(
        APP_NAME=resolved.app_name,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=False,
    )
    _configure_sessions(app, resolved)
    _configure_templates(app)

    register_request_context(app, app_name=resolved.app_name)
    registrar_csrf(app)

    client = api_client or ApiClient(
        resolved.api_base_url,
        timeout_seconds=resolved.api_timeout_seconds,
        # La sesion viaja como `Bearer` en cada llamada autenticada. El token
        # se pide aqui, en cada peticion, y no se guarda al arrancar: caduca y
        # se renueva con la vida de la sesion, no con la del proceso.
        auth_headers=cabecera_bearer,
    )
    app.extensions[API_CLIENT_KEY] = client

    register_blueprints(app)
    _register_session_renewal(app)
    _register_error_handlers(app)

    return app


def _register_session_renewal(app: Flask) -> None:
    """Rota el token de acceso caducado antes de que la peticion llegue a la vista.

    Va en `before_request` y no en cada vista porque el token caduca por tiempo, no
    por pantalla: un visitante que lleva diez minutos leyendo la pagina tiene un
    token muerto y no hay ninguna vista concreta a la que culpar. Comprobarlo en
    un solo sitio es lo que evita que cada una olvide hacerlo.
    """

    @app.before_request
    def _renovar_si_falta() -> None:
        if request.endpoint in RUTAS_SIN_RENOVACION:
            return
        if necesita_renovacion():
            renovar_sesion()


def _configure_sessions(app: Flask, settings: Settings) -> None:
    """Fija el contrato de la cookie de sesion y el almacen del servidor.

    Los tres atributos los exige la especificacion y se fijan aqui, no en cada
    vista: `HttpOnly` para que JavaScript no lea la cookie, `Secure` para que no
    viaje en claro y `SameSite=Lax` para que no se envie desde otro sitio. El
    identificador opaco lo emite `ServerSideSessionInterface`.
    """
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=settings.session_cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="ticketplus_session",
    )
    app.session_interface = ServerSideSessionInterface()


def _configure_templates(app: Flask) -> None:
    """Publica en las plantillas lo que la vista necesita sin importar.

    La identidad va como global y no como variable de contexto porque la decision
    de quien se es de la sesion, no de la vista que se este renderizando. El
    token CSRF lo publica `registrar_csrf`, que ademas instala su comprobacion.
    """
    app.jinja_env.globals["identidad_actual"] = identidad_actual


def _register_error_handlers(app: Flask) -> None:
    """Engancha la traduccion de los fallos de la API a respuestas de la web."""

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


__all__ = [
    "API_CLIENT_KEY",
    "API_PROBE_PATH",
    "APP_CONFIG_KEYS",
    "REQUEST_ID_HEADER",
    "RUTAS_SIN_RENOVACION",
    "create_app",
]