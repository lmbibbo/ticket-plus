"""2.3 - Contrato de errores `application/problem+json`."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE, FieldError, ProblemException


class _Compra(BaseModel):
    zone_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=10)


def _esquema_valido(cuerpo: dict[str, Any], status_esperado: int) -> None:
    """Comprueba que un cuerpo cumple el esquema comun de `problem+json`."""
    for campo in ("type", "title", "status", "detail", "code", "instance"):
        assert campo in cuerpo, f"falta {campo} en {cuerpo}"
    assert cuerpo["status"] == status_esperado


@pytest.fixture
def rutas_negocio(app: FastAPI) -> FastAPI:
    """Rutas de error con las formas que el diseno nombra."""
    router = APIRouter(prefix="/api/v1")

    @router.post("/orders")
    async def crear_orden(compra: _Compra) -> dict[str, str]:
        return {"ok": "1"}

    @router.get("/events/no-publicable")
    async def evento_no_publicable() -> dict[str, str]:
        raise ProblemException(
            status=409,
            code=ErrorCode.EVENT_NOT_PUBLISHABLE,
            title="Evento no publicable",
            detail="El evento no tiene zonas con capacidad.",
        )

    @router.get("/orders/idempotencia")
    async def conflicto_idempotencia() -> dict[str, str]:
        raise ProblemException(
            status=409,
            code=ErrorCode.IDEMPOTENCY_CONFLICT,
            title="Conflicto de idempotencia",
            detail="La clave se uso con datos distintos.",
        )

    @router.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("detalle interno que no debe salir: host=db01 user=admin")

    app.include_router(router)
    return app


@pytest.mark.parametrize(
    ("ruta", "metodo", "status_esperado", "code_esperado"),
    [
        ("/api/v1/events/no-publicable", "GET", 409, "event_not_publishable"),
        ("/api/v1/orders/idempotencia", "GET", 409, "idempotency_conflict"),
        ("/api/v1/no-existe", "GET", 404, "not_found"),
    ],
)
def test_error_de_negocio_tiene_el_esquema_unificado(
    rutas_negocio: FastAPI,
    ruta: str,
    metodo: str,
    status_esperado: int,
    code_esperado: str,
) -> None:
    """GIVEN un error de negocio / WHEN se devuelve / THEN el esquema es el
    mismo en todos los casos y el content-type es problem+json."""
    with TestClient(rutas_negocio, raise_server_exceptions=False) as cliente:
        respuesta = cliente.request(metodo, ruta)

    assert respuesta.status_code == status_esperado
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)

    cuerpo: dict[str, Any] = respuesta.json()
    _esquema_valido(cuerpo, status_esperado)
    assert cuerpo["code"] == code_esperado
    assert cuerpo["instance"] == ruta
    assert cuerpo["type"].endswith(f"/{code_esperado}")


def test_validacion_pydantic_devuelve_errores_por_campo(rutas_negocio: FastAPI) -> None:
    """GIVEN un cuerpo que no valida / WHEN se procesa / THEN cada campo
    invalido viene con su ruta y su mensaje."""
    with TestClient(rutas_negocio, raise_server_exceptions=False) as cliente:
        respuesta = cliente.post("/api/v1/orders", json={"zone_id": 0, "quantity": 99})

    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "validation_error"

    campos = {error["field"] for error in cuerpo["errors"]}
    assert campos == {"zone_id", "quantity"}
    for error in cuerpo["errors"]:
        assert error["message"]
        assert error["message"] != " "


def test_error_de_validacion_no_filtra_detalle_interno(rutas_negocio: FastAPI) -> None:
    with TestClient(rutas_negocio, raise_server_exceptions=False) as cliente:
        respuesta = cliente.post("/api/v1/orders", json={"zone_id": "no-es-un-numero"})

    cuerpo = respuesta.json()
    assert respuesta.status_code == 422
    texto = str(cuerpo)
    assert "Traceback" not in texto
    assert "pydantic_core" not in texto
    assert "ticket_api." not in texto


def test_5xx_no_revela_nada_del_servidor(rutas_negocio: FastAPI) -> None:
    """GIVEN una excepcion no controlada / WHEN se responde / THEN el detalle
    es generico y el motivo real no sale en el cuerpo."""
    with TestClient(rutas_negocio, raise_server_exceptions=False) as cliente:
        respuesta = cliente.get("/api/v1/boom")

    assert respuesta.status_code == 500
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "internal_error"
    assert cuerpo["detail"] == "Ocurrio un error interno."
    texto = str(cuerpo)
    for secreto in ("db01", "admin", "detalle interno", "RuntimeError", "Traceback"):
        assert secreto not in texto, f"se filtro {secreto!r} al cuerpo del error"


def test_5xx_conserva_la_correlacion(rutas_negocio: FastAPI) -> None:
    """Un 5xx tambien lleva `X-Request-ID`: sin el, el error no se puede
    correlacionar con la bitacora."""
    from ticket_api.observability.middleware import REQUEST_ID_HEADER

    entrante = "correlacion-500"
    with TestClient(rutas_negocio, raise_server_exceptions=False) as cliente:
        respuesta = cliente.get("/api/v1/boom", headers={REQUEST_ID_HEADER: entrante})

    assert respuesta.status_code == 500
    assert respuesta.headers[REQUEST_ID_HEADER] == entrante


def test_codigos_de_negocio_son_estables() -> None:
    """Los codigos que el diseno nombra existen y no cambiaron de valor: Flask
    decide que formulario mostrar a partir de ellos."""
    assert ErrorCode.INVENTORY_UNAVAILABLE.value == "inventory_unavailable"
    assert ErrorCode.IDEMPOTENCY_CONFLICT.value == "idempotency_conflict"
    assert ErrorCode.EVENT_NOT_PUBLISHABLE.value == "event_not_publishable"
    # Un StrEnum duplicado seria un alias silencioso del mismo valor.
    assert len({c.value for c in ErrorCode}) == len(list(ErrorCode))


@pytest.fixture
def app_con_error(app: FastAPI) -> FastAPI:
    """Anade a la API real un error de dominio con errores por campo."""
    router = APIRouter(prefix="/api/v2")

    @router.get("/x")
    async def x() -> dict[str, str]:
        raise ProblemException(
            status=422,
            code=ErrorCode.VALIDATION_ERROR,
            title="Datos invalidos",
            detail="Revisa los campos.",
            errors=[FieldError(field="email", message="ya existe")],
        )

    app.include_router(router)
    return app


def test_problem_excepcion_transporta_errores_de_campo(app_con_error: FastAPI) -> None:
    """Una excepcion de dominio puede adjuntar errores por campo; la respuesta
    los conserva."""
    with TestClient(app_con_error, raise_server_exceptions=False) as cliente:
        respuesta = cliente.get("/api/v2/x")

    assert respuesta.status_code == 422
    assert respuesta.json()["errors"] == [{"field": "email", "message": "ya existe"}]
