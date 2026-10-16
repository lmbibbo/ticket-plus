"""Blueprint de sistema: salud, portada y sondas de correlacion.

Las rutas que ya vivian en `main` se mudan aqui sin cambiar su comportamiento.
Lo que el blueprint aporta es el sitio donde colgar la proteccion por rol de las
areas reales, que es 6.2.
"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify, render_template

from ticket_web.api_client import API_CLIENT_KEY, ApiLike

#: Ruta de la API que responde 404 a proposito. La usa la sonda del demo de F1
#: para comprobar que un `problem+json` llega a la web con su codigo de negocio.
API_PROBE_PATH = "/api/v1/no-existe"

system_bp = Blueprint("system", __name__)


def _api() -> ApiLike:
    """Cliente de la API registrado en la extension de la aplicacion."""
    cliente: ApiLike = current_app.extensions[API_CLIENT_KEY]
    return cliente


@system_bp.get("/health")
def health() -> tuple[dict[str, str], int]:
    """Comprobacion de vida propia de la web, sin depender de la API."""
    return {"status": "ok", "app": current_app.config["APP_NAME"]}, 200


@system_bp.get("/")
def index() -> str:
    """Portada. La navegacion por rol llega en 6.2."""
    return render_template("base.html", app_name=current_app.config["APP_NAME"])


@system_bp.get("/api-echo")
def api_echo() -> Response:
    """Sonda de la demo de F1: reenvia a la API y ensena los dos lados de la
    correlacion.

    `web_request_id` es el identificador de la peticion del navegador y
    `api_request_id` es el que la API devolvio tras el salto. Que coincidan es
    la prueba de que el identificador se propago y no se regenero.
    """
    from ticket_web.observability.middleware import REQUEST_ID_HEADER, current_request_id

    respuesta = _api().get("/api/v1/ping")
    return jsonify(
        {
            "web_request_id": current_request_id(),
            "api_request_id": respuesta.headers.get(REQUEST_ID_HEADER),
            "api_status": respuesta.status,
            "api_payload": respuesta.data,
        }
    )


@system_bp.get("/api-probe")
def api_probe() -> Response:
    """Sonda que fuerza un 404 de la API para ver como llega el error.

    No captura la excepcion a proposito: el manejador registrado en `main` la
    traduce, que es el camino que hara cualquier vista real.
    """
    respuesta = _api().get(API_PROBE_PATH)
    return jsonify({"api_status": respuesta.status, "api_payload": respuesta.data})