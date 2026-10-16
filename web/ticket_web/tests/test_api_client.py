"""2.5 - Cliente web hacia FastAPI: exito, 4xx, 5xx y timeout.

Se prueba contra `httpx.MockTransport` en lugar de un doble de `ApiClient`:
así se ejercitan de verdad la construccion de la peticion, las cabeceras, la
traduccion de respuestas y el pool, que es justo lo que la tarea pide verificar.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ticket_web.api_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ApiClient,
    ApiFailure,
    ApiProblemError,
    ApiUnavailableError,
)

BASE_URL = "http://api.interno"


def _problem(status: int, code: str, **extra: Any) -> httpx.Response:
    cuerpo = {
        "type": f"https://ticketplus.test/errors/{code}",
        "title": "Error",
        "status": status,
        "detail": "detalle del backend",
        "code": code,
        "instance": "/api/v1/x",
        **extra,
    }
    return httpx.Response(
        status,
        headers={"content-type": "application/problem+json"},
        json=cuerpo,
    )


def _cliente(handler: Any) -> ApiClient:
    return ApiClient(BASE_URL, transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- exito


def test_exito_devuelve_datos_y_cabeceras() -> None:
    """GIVEN una respuesta 2xx / WHEN se llama / THEN llegan los datos y las
    cabeceras, incluida la de correlacion que devuelve la API."""
    visto: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["metodo"] = request.method
        visto["ruta"] = request.url.path
        visto["request_id"] = request.headers.get("X-Request-ID")
        return httpx.Response(
            200,
            headers={"X-Request-ID": "desde-la-api"},
            json={"items": [1, 2, 3]},
        )

    with _cliente(handler) as cliente:
        respuesta = cliente.get("/api/v1/eventos", request_id="mi-correlacion")

    assert respuesta.status == 200
    assert respuesta.data == {"items": [1, 2, 3]}
    assert respuesta.headers["X-Request-ID"] == "desde-la-api"
    assert visto["metodo"] == "GET"
    assert visto["ruta"] == "/api/v1/eventos"
    assert visto["request_id"] == "mi-correlacion", "no se propago la correlacion"


def test_se_construye_un_solo_cliente_y_se_cierra_una_vez(monkeypatch: Any) -> None:
    """El pool es el motivo de usar un cliente unico: abrir una conexion por
    peticion multiplicaria el coste de cada salto.

    Con `MockTransport` no hay conexiones reales, asi que el invariante que se
    puede comprobar es el que lo hace posible: un unico `httpx.Client` para toda
    la vida del `ApiClient`, cerrado una sola vez.
    """
    construidos: list[httpx.Client] = []
    cerrados: list[int] = []
    original = httpx.Client

    class _Contador(original):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            construidos.append(self)

        def close(self) -> None:
            cerrados.append(id(self))
            super().close()

    monkeypatch.setattr(httpx, "Client", _Contador)

    vacio = httpx.MockTransport(lambda _: httpx.Response(200))
    with ApiClient(BASE_URL, transport=vacio) as cliente:
        for _ in range(5):
            assert cliente.get("/api/v1/ping").status == 200

    assert len(construidos) == 1, "se abrio mas de un cliente: se pierde el pool"
    assert cerrados == [id(construidos[0])]


def test_cuerpo_vacio_no_es_error() -> None:
    """Un 204 sin cuerpo es un exito valido, no un fallo de decodificacion."""
    with _cliente(lambda _: httpx.Response(204)) as cliente:
        assert cliente.delete("/api/v1/x").data is None


# ---------------------------------------------------------------- 4xx


def test_4xx_con_problem_json_llega_como_error_de_negocio() -> None:
    """GIVEN un 409 problem+json / WHEN se recibe / THEN la vista recibe el
    codigo de negocio, no un 500 opaco."""
    with (
        _cliente(lambda _: _problem(409, "idempotency_conflict")) as cliente,
        pytest.raises(ApiProblemError) as info,
    ):
        cliente.post("/api/v1/orders")

    assert info.value.code == "idempotency_conflict"
    assert info.value.status == 409
    assert info.value.problem.detail == "detalle del backend"


def test_4xx_con_errores_por_campo_se_mapean_al_formulario() -> None:
    """El caso que la spec nombra: un correo duplicado tiene que poder pintarse
    como error del campo, no como un error general."""
    respuesta = _problem(
        422,
        "validation_error",
        errors=[
            {"field": "email", "message": "ya existe"},
            {"field": "password", "message": "muy corta"},
        ],
    )
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiProblemError) as info:
        cliente.post("/api/v1/auth/register", json={})

    problema = info.value.problem
    assert problema.es_error_de_campos
    assert problema.field_errors() == {
        "email": "ya existe",
        "password": "muy corta",
    }


def test_errores_repetidos_por_campo_conservan_el_primero() -> None:
    respuesta = _problem(
        422,
        "validation_error",
        errors=[
            {"field": "email", "message": "formato invalido"},
            {"field": "email", "message": "ya existe"},
        ],
    )
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiProblemError) as info:
        cliente.post("/api/v1/auth/register")

    assert info.value.problem.field_errors() == {"email": "formato invalido"}


def test_fragmento_htmx_nombra_el_campo_que_falla() -> None:
    """Un fragmento no tiene formulario: sin nombrar el campo, el mensaje
    'detalle del backend' no dice que corregir."""
    respuesta = _problem(
        422,
        "validation_error",
        errors=[
            {"field": "email", "message": "ya existe"},
            {"field": "password", "message": "muy corta"},
        ],
    )
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiProblemError) as info:
        cliente.post("/api/v1/auth/register")

    assert info.value.problem.mensaje_para_fragmento() == "email: ya existe"


def test_error_de_campos_vacio_usa_el_detalle() -> None:
    """Sin errores por campo, el fragmento cae al `detail` en vez de inventar
    texto o devolver una cadena vacia."""
    respuesta = _problem(422, "validation_error", errors=[])
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiProblemError) as info:
        cliente.post("/api/v1/auth/register")

    assert info.value.problem.mensaje_para_fragmento() == "detalle del backend"


def test_4xx_sin_problem_json_es_fallo_ilegible() -> None:
    """Un 4xx con otro content-type no es un error de negocio conocido: la web
    no debe inventarse un codigo ni mostrar un cuerpo que no entiende."""
    respuesta = httpx.Response(400, headers={"content-type": "text/html"}, text="<h1>400</h1>")
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiUnavailableError) as info:
        cliente.get("/api/v1/x")

    assert info.value.fallo is ApiFailure.UNREADABLE
    assert info.value.status == 400


# ---------------------------------------------------------------- 5xx


def test_5xx_se_trata_como_fallo_recuperable() -> None:
    """GIVEN un 500 / WHEN llega a la web / THEN es recuperable, aunque venga
    como problem+json bien formado.

    El codigo del backend queda disponible para la bitacora, pero el mensaje que
    ve el usuario lo redacta la web: el `detail` de un 500 puede(filterar
    nombres de host o consultas SQL.
    """
    respuesta = _problem(500, "internal_error", detail="host=db01 user=admin")
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiUnavailableError) as info:
        cliente.get("/api/v1/x")

    assert info.value.fallo is ApiFailure.UNREACHABLE
    assert info.value.status == 500
    assert "db01" not in str(info.value), "el motivo del servidor se filtro a la web"


def test_5xx_no_se_confunde_con_un_error_corregible() -> None:
    """Con errores por campo y todo, un 5xx sigue siendo irrecuperable para el
    usuario: no hay nada que pueda arreglar."""
    respuesta = _problem(500, "internal_error", errors=[{"field": "email", "message": "x"}])
    with _cliente(lambda _: respuesta) as cliente, pytest.raises(ApiUnavailableError):
        cliente.post("/api/v1/auth/register")


def test_5xx_sin_cuerpo_tampoco_revienta() -> None:
    """Un 502 de un proxy sin cuerpo no debe escaparse como excepcion de httpx."""
    with (
        _cliente(lambda _: httpx.Response(502)) as cliente,
        pytest.raises(ApiUnavailableError) as info,
    ):
            cliente.get("/api/v1/x")

    assert info.value.status == 502


# ---------------------------------------------------------------- timeout


def test_timeout_es_recuperable_y_distinguible() -> None:
    """GIVEN un timeout / WHEN se llama / THEN la vista puede ofrecer
    reintentar, y sabe que la API no esta caida sino lenta."""
    def lento(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("tardo demasiado", request=request)

    with _cliente(lento) as cliente, pytest.raises(ApiUnavailableError) as info:
        cliente.get("/api/v1/x")

    assert info.value.fallo is ApiFailure.TIMEOUT
    assert info.value.code == "api_timeout"
    assert info.value.status is None


def test_caida_y_timeout_no_se_confunden() -> None:
    """Ofrecer 'reintentar' ante una caida hace perder el tiempo del usuario."""
    def caido(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with _cliente(caido) as cliente, pytest.raises(ApiUnavailableError) as info:
        cliente.get("/api/v1/x")

    assert info.value.fallo is ApiFailure.UNREACHABLE
    assert info.value.code == "api_unreachable"


def test_el_timeout_por_defecto_esta_acotado() -> None:
    """Sin timeout, una API colgada deja la peticion web colgada y el usuario
    mirando un spinner sin explicacion."""
    assert 0 < DEFAULT_TIMEOUT_SECONDS <= 10


def test_el_mensaje_del_fallo_no_filtra_internos() -> None:
    """`str(exc)` acaba en el log; si lleva el motivo de httpx, escribe hosts y
    credenciales en la bitacora."""
    def lento(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "timeout al conectar a http://10.0.0.5:5432 con token Bearer abc.def",
            request=request,
        )

    with _cliente(lento) as cliente, pytest.raises(ApiUnavailableError) as info:
        cliente.get("/api/v1/x")

    texto = json.dumps({"msg": str(info.value), "code": info.value.code})
    assert "10.0.0.5" not in texto
    assert "Bearer" not in texto
    assert "5432" not in texto
