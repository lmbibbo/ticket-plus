"""2.4 - La web registra con los mismos campos y propaga `X-Request-ID`.

La prueba de exito recorre la cadena entera en proceso: navegador simulado ->
Flask -> cliente httpx -> FastAPI. Ninguna parte esta doblada, porque el
requisito es precisamente que el identificador sobreviva al salto.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from flask import Flask
from werkzeug.test import TestResponse

from conftest import ManyAccessLogs
from ticket_web.api_client import ApiProblemError, _problem_to_raise
from ticket_web.main import REQUEST_ID_HEADER


def _json(respuesta: TestResponse) -> dict[str, Any]:
    """Cuerpo JSON de la respuesta, comprobado como objeto.

    `Response.json` devuelve `Any | None`; sin esta comprobacion un cuerpo que
    no sea objeto fallaria mas tarde, en un subscript, con un error que no
    senala la causa real.
    """
    cuerpo = respuesta.json
    assert isinstance(cuerpo, dict), f"la respuesta no es un objeto JSON: {cuerpo!r}"
    return cuerpo


def test_404_de_la_api_llega_a_la_web_con_su_codigo(web_app: Flask) -> None:
    """GIVEN que FastAPI responde un 404 problem+json / WHEN la web lo recibe /
    THEN la vista obtiene el codigo de negocio intacto, no un error generico."""
    from ticket_api.errors.codes import ErrorCode

    from ticket_web.main import API_PROBE_PATH

    assert API_PROBE_PATH == "/api/v1/no-existe"
    cliente = web_app.test_client()
    respuesta = cliente.get("/api-probe")

    assert respuesta.status_code == 404
    cuerpo = _json(respuesta)
    assert cuerpo["error"]["code"] == ErrorCode.NOT_FOUND.value
    assert cuerpo["error"]["status"] == 404


def test_fallo_entre_web_y_api_conserva_la_correlacion(
    web_settings: Any, capturar_ambos: ManyAccessLogs
) -> None:
    """GIVEN una API que no responde / WHEN la web registra el fallo / THEN
    conserva el identificador y el mensaje no lleva datos internos."""
    from ticket_web.api_client import ApiFailure, ApiUnavailableError
    from ticket_web.main import create_app

    class _Caido:
        def get(self, path: str, **_: Any) -> Any:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE)

        def post(self, path: str, **_: Any) -> Any:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE)

        def patch(self, path: str, **_: Any) -> Any:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE)

        def delete(self, path: str, **_: Any) -> Any:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE)

        def close(self) -> None:
            return None

    app = create_app(web_settings, api_client=_Caido())

    with capturar_ambos as lineas:
        cliente = app.test_client()
        respuesta = cliente.get("/api-probe", headers={REQUEST_ID_HEADER: "caida-001"})

    assert respuesta.status_code == 503
    assert _json(respuesta)["error"]["code"] == "api_unreachable"

    web = lineas["web"][0]
    assert web["request_id"] == "caida-001", "el fallo entre saltos perdio la correlacion"
    assert web["status"] == 503
    assert "httpx" not in json.dumps(lineas, ensure_ascii=False)
    assert "Traceback" not in json.dumps(lineas, ensure_ascii=False)


def test_identificador_del_navegador_llega_intacto_a_la_api(web_app: Flask) -> None:
    """GIVEN un identificador valido en la peticion web / WHEN la web llama a la
    API / THEN FastAPI devuelve ese mismo identificador, no uno nuevo."""
    entrante = "8f14e45f-ea0d-4a2b-9c1d-2f3a4b5c6d7e"

    cliente = web_app.test_client()
    respuesta = cliente.get("/api-echo", headers={REQUEST_ID_HEADER: entrante})

    assert respuesta.status_code == 200
    cuerpo = _json(respuesta)
    assert cuerpo["web_request_id"] == entrante
    assert cuerpo["api_request_id"] == entrante, "la API regenero el identificador"


def test_sin_identificador_se_genera_uno_y_se_comparte(web_app: Flask) -> None:
    """GIVEN una peticion sin identificador / WHEN cruza los dos saltos / THEN
    los dos generates son el mismo, y no queda vacio."""
    cliente = web_app.test_client()
    respuesta = cliente.get("/api-echo")

    cuerpo = _json(respuesta)
    assert cuerpo["web_request_id"]
    assert cuerpo["api_request_id"] == cuerpo["web_request_id"]


def test_un_identificador_invalido_se_sustituye(web_app: Flask) -> None:
    """Un identificador con formato hostil se sustituye por uno del patron
    UUID, para que su valor no pueda inyectar contenido en la bitacora."""
    hostil = "id con espacios y simbolos #%& " * 10
    cliente = web_app.test_client()
    respuesta = cliente.get("/api-echo", headers={REQUEST_ID_HEADER: hostil})

    cuerpo = _json(respuesta)
    generado = cuerpo["web_request_id"]
    assert generado != hostil
    assert len(generado) <= 64
    assert generado == cuerpo["api_request_id"]


def test_un_identificador_con_saltos_nunca_se_reenvia() -> None:
    """Un salto de linea en la cabecera inyectaria una linea falsa en la
    bitacora. No se comprueba por HTTP porque Werkzeug ya rechaza la peticion
    antes de que llegue a la aplicacion; se comprueba la normalizacion."""
    from ticket_web.observability.middleware import normalize_request_id

    generado = normalize_request_id("abc\ndef")
    assert "\n" not in generado
    assert generado != "abc\ndef"
    assert len(generado) == 36, "debe ser un UUID, no una variante truncada"


def test_una_sola_peticion_deja_dos_lineas_correlacionadas(
    web_app: Flask, capturar_ambos: ManyAccessLogs
) -> None:
    """La demo de F1: dos lineas, una por salto, con el mismo identificador y
    campos de la lista permitida."""
    entrante = "demo-f1-0000-0000"

    with capturar_ambos as lineas:
        cliente = web_app.test_client()
        cliente.get("/api-echo", headers={REQUEST_ID_HEADER: entrante})

    assert len(lineas["web"]) == 1, lineas["web"]
    assert len(lineas["api"]) == 1, lineas["api"]

    web, api = lineas["web"][0], lineas["api"][0]
    assert web["request_id"] == api["request_id"] == entrante
    assert web["app"] == "web-test"
    assert api["app"] == "backend"
    assert web["status"] == 200
    assert api["status"] == 200
    assert web["method"] == api["method"] == "GET"
    assert web["route"] == "/api-echo"
    assert api["route"] == "/api/v1/ping"

    for linea in (web, api):
        assert linea["duration_ms"] >= 0
        assert json.loads(json.dumps(linea)) == linea, "la linea no es JSON valido"


def test_la_web_tampoco_registra_datos_sensibles(
    web_app: Flask, capturar_ambos: ManyAccessLogs
) -> None:
    """La redaccion de la web cubre los mismos riesgos que la del backend."""
    with capturar_ambos as lineas:
        cliente = web_app.test_client()
        cliente.get(
            "/api-echo",
            headers={
                REQUEST_ID_HEADER: "redaccion-web",
                "cookie": "ticketplus_session=abc123",
                "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.cuerpo.firma",
                "x-carga-qr": "TICKPLUS:QR:" + "Q" * 64,
            },
        )

    salida = json.dumps(lineas, ensure_ascii=False)
    for secreto in ("abc123", "eyJhbGciOiJIUzI1NiJ9", "ticketplus_session=", "Q" * 64):
        assert secreto not in salida, f"la web registro {secreto!r}"

    web = lineas["web"][0]
    assert web["request_id"] == "redaccion-web", "la redaccion se comio la correlacion"


def test_problema_con_errores_por_campo_conserva_los_campos() -> None:
    """La web necesita `errors` con `field` y `message` para pintar el
    formulario; si la API no los manda, el formulario no puede saber que
    corregir."""
    crudo = httpx.Response(
        422,
        headers={"content-type": "application/problem+json"},
        json={
            "status": 422,
            "code": "validation_error",
            "title": "Datos invalidos",
            "detail": "Revisa los campos.",
            "errors": [{"field": "email", "message": "ya existe"}],
        },
    )

    with pytest.raises(ApiProblemError) as info:
        raise _problem_to_raise(crudo)

    assert info.value.code == "validation_error"
    assert info.value.errors == [{"field": "email", "message": "ya existe"}]


def test_health_de_la_web_no_depende_de_la_api(web_app: Flask) -> None:
    """`/health` responde sin tocar la API: si no, un apagado de la API
    reiniciaria la web en el orquestador."""
    cliente = web_app.test_client()
    respuesta = cliente.get("/health")

    assert respuesta.status_code == 200
    assert _json(respuesta) == {"status": "ok", "app": "web-test"}
