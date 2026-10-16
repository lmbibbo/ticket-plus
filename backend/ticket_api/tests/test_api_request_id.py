"""2.1 - Correlacion de peticion en FastAPI."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi.testclient import TestClient

from ticket_api.observability.middleware import REQUEST_ID_HEADER

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def test_identificador_valido_se_preserva(client: TestClient) -> None:
    """GIVEN un request_id entrante valido / WHEN la API lo recibe
    / THEN lo devuelve sin cambiarlo."""
    entrante = "corr-abc-123"
    respuesta = client.get("/api/v1/ping", headers={REQUEST_ID_HEADER: entrante})

    assert respuesta.status_code == 200
    assert respuesta.headers[REQUEST_ID_HEADER] == entrante


def test_identificador_ausente_se_genera(client: TestClient) -> None:
    """GIVEN una peticion sin request_id / WHEN llega a la API
    / THEN genera un UUID y lo devuelve en la respuesta."""
    respuesta = client.get("/api/v1/ping")

    generado = respuesta.headers[REQUEST_ID_HEADER]
    assert UUID_RE.match(generado), f"no es un UUID: {generado!r}"
    # Dos peticiones sin identificador no pueden compartirlo.
    otro = client.get("/api/v1/ping").headers[REQUEST_ID_HEADER]
    assert otro != generado


def test_identificador_invalido_se_sustituye(client: TestClient) -> None:
    """Un valor que no cumple el formato no se propaga a la cabecera ni a la
    bitacora: es la via para inyectar saltos de linea o campos en los logs."""
    for sospechoso in (
        "con espacios y salto\nlinea",
        "a" * 200,
        "token; drop",
        "",
    ):
        respuesta = client.get("/api/v1/ping", headers={REQUEST_ID_HEADER: sospechoso})
        emitido = respuesta.headers[REQUEST_ID_HEADER]
        assert UUID_RE.match(emitido), f"se acepto {sospechoso!r} -> {emitido!r}"


def test_identificador_se_preserva_tambien_en_error(
    client: TestClient,
    captured_lines: Any,
) -> None:
    """La respuesta de error lleva el mismo identificador que la de exito: el
    middleware envuelve tambien la ruta de error."""
    entrante = str(uuid.uuid4())
    with captured_lines() as lineas:
        respuesta = client.get("/api/v1/no-existe", headers={REQUEST_ID_HEADER: entrante})

    assert respuesta.status_code == 404
    assert respuesta.headers[REQUEST_ID_HEADER] == entrante
    assert [linea["request_id"] for linea in lineas] == [entrante]
