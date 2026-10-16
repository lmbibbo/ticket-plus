"""2.2 - Bitacora estructurada del backend sin datos sensibles."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ticket_api.observability.logging import (
    ALLOWED_LOG_FIELDS,
    build_record,
    log_access,
    scrub,
)

JWT_FALSO = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLWhlcmU"
QR_FALSO = "TICKPLUS:QR:" + "Q" * 64 + "=="
CODIGO_PAGO = "PAY-" + "4f8a2b" * 12
REFRESH_FALSO = "rt_" + "z9y8x7w6v5t4s3r2q1p0o9n8m7"


class _Carga(BaseModel):
    email: str
    password: str
    card_number: str


def _registrar(lineas: list[dict[str, Any]]) -> str:
    return json.dumps(lineas, ensure_ascii=False)


def test_linea_tiene_los_campos_exigidos(client: TestClient, captured_lines: Any) -> None:
    """Metodo, ruta, estado, duracion y correlacion estan siempre."""
    with captured_lines() as lineas:
        client.get("/api/v1/ping")
    linea = lineas[0]
    for campo in ("timestamp", "app", "method", "route", "status", "duration_ms", "request_id"):
        assert campo in linea, f"falta {campo} en {linea}"
    assert linea["method"] == "GET"
    assert linea["route"] == "/api/v1/ping"
    assert linea["status"] == 200
    assert isinstance(linea["duration_ms"], (int, float))
    assert linea["app"] == "backend"


def test_ruta_usa_la_plantilla_no_el_valor(app: FastAPI, captured_lines: Any) -> None:
    """La ruta registrada es la plantilla, no el identificador: registrar el
    valor daria cardinalidad infinita a la bitacora."""

    @app.get("/api/v1/probe/{identificador}")
    async def probe(identificador: str) -> dict[str, str]:
        return {"ok": identificador}

    with TestClient(app, raise_server_exceptions=False) as c, captured_lines() as lineas:
        c.get("/api/v1/probe/abc-123")

    assert lineas[0]["route"] == "/api/v1/probe/{identificador}"
    assert lineas[0]["path"] == "/api/v1/probe/abc-123"


def test_no_registra_credenciales_de_la_peticion(app: FastAPI, captured_lines: Any) -> None:
    """GIVEN una peticion con contrasena, cookie, bearer, refresh y codigo de
    pago / WHEN se genera la bitacora / THEN ninguno aparece en claro."""
    password = "hunter2-muy-secreto"  # noqa: S105
    cookie = "ticketplus_session=abc123def456"
    codigo = CODIGO_PAGO

    @app.post("/api/v1/probe")
    async def probe(carga: _Carga) -> dict[str, str]:
        return {"ok": "1"}

    with TestClient(app, raise_server_exceptions=False) as c, captured_lines() as lineas:
        c.post(
            "/api/v1/probe",
            json={"email": "a@b.co", "password": password, "card_number": "4111111111111111"},
            headers={
                "Authorization": f"Bearer {JWT_FALSO}",
                "Cookie": cookie,
                "X-Request-ID": "probe-1",
            },
            params={"token": REFRESH_FALSO, "codigo_pago": codigo},
        )

    salida = _registrar(lineas)
    assert lineas, "no se emitio ninguna linea"
    for secreto in (
        password,
        cookie,
        "ticketplus_session",
        JWT_FALSO,
        REFRESH_FALSO,
        codigo,
        "4111111111111111",
        "abc123def456",
    ):
        assert secreto not in salida, f"se filtro {secreto!r} a la bitacora"


def test_no_registra_carga_qr(app: FastAPI, captured_lines: Any) -> None:
    """GIVEN una respuesta con carga QR / WHEN se registra / THEN solo queda
    una referencia no sensible, nunca el valor original."""
    with TestClient(app, raise_server_exceptions=False) as c, captured_lines() as lineas:
        c.get("/api/v1/ping")
        log_access(
            app="backend",
            method="GET",
            route="/api/v1/orders",
            status=201,
            duration_ms=1.0,
            request_id="probe-qr",
            message=f"entrega emitida con carga {QR_FALSO}",
        )

    salida = _registrar(lineas)
    assert QR_FALSO not in salida
    assert "[redactado]" in salida


def test_lista_permitida_descarta_lo_no_permitido() -> None:
    """La garantia no depende de que quien llama tenga cuidado: un campo fuera
    de la lista simplemente no existe en la linea."""
    linea = build_record(
        {
            "method": "POST",
            "status": 200,
            "password": "hunter2",
            "authorization": f"Bearer {JWT_FALSO}",
            "cuerpo_completo": "{\"email\": \"a@b.co\"}",
        },
        app="backend",
    )
    assert set(linea) <= ALLOWED_LOG_FIELDS
    assert "password" not in linea
    assert "authorization" not in linea
    assert "cuerpo_completo" not in linea
    assert linea["method"] == "POST"


def test_scrub_cubre_las_formas_del_diseno() -> None:
    """Cada patron que el diseno nombra como prohibido tiene una prueba."""
    casos = [
        JWT_FALSO,
        f"Bearer {JWT_FALSO}",
        "Cookie: ticketplus_session=abc123",
        "password=mi-secreto",
        '{"password": "mi-secreto"}',
        f"refresh_token={REFRESH_FALSO}",
        QR_FALSO,
        CODIGO_PAGO,
    ]
    for caso in casos:
        redactado = scrub(caso)
        assert "[redactado]" in redactado, f"sin redactar: {caso!r} -> {redactado!r}"


def test_registra_error_con_codigo(client: TestClient, captured_lines: Any) -> None:
    """GIVEN una peticion que termina en 4xx / WHEN se registra / THEN la linea
    lleva el mismo codigo de negocio que devuelve el cuerpo."""
    with captured_lines() as lineas:
        respuesta = client.get("/api/v1/no-existe")

    cuerpo = respuesta.json()
    assert respuesta.status_code == 404
    assert lineas[0]["status"] == 404
    assert lineas[0]["code"] == cuerpo["code"] == "not_found"


def test_captura_recibe_lineas_json_parseables(client: TestClient, captured_lines: Any) -> None:
    with captured_lines() as lineas:
        client.get("/api/v1/ping")
        client.get("/health")
    assert len(lineas) == 2
    assert {linea["route"] for linea in lineas} == {"/api/v1/ping", "/health"}
