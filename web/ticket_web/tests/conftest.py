"""Fixtures de la suite de la web."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from fastapi import FastAPI
from flask import Flask
from ticket_api.main import create_app as create_api_app
from ticket_api.observability.logging import ACCESS_LOGGER_NAME as API_LOGGER
from ticket_api.observability.logging import ALLOWED_LOG_FIELDS as BACKEND_CAMPOS

from conftest import CapturedLogs, ManyAccessLogs
from ticket_web.api_client import ApiClient
from ticket_web.config import Settings
from ticket_web.main import create_app as create_web_app
from ticket_web.observability.logging import ACCESS_LOGGER_NAME as WEB_LOGGER
from ticket_web.observability.logging import ALLOWED_LOG_FIELDS as WEB_CAMPOS
from ticket_web.tests.asgi_transport import SyncASGITransport


@pytest.fixture
def web_settings() -> Settings:
    return Settings(app_name="web-test", api_base_url="http://api.interno", log_level="WARNING")


@pytest.fixture
def api_app() -> FastAPI:
    """API real, con sus middlewares, para ejercida en proceso."""
    return create_api_app()


@pytest.fixture
def web_app(api_app: FastAPI, web_settings: Settings) -> Iterator[Flask]:
    """Web real cuyo cliente HTTP entra en la API por ASGI.

    No es un doble: la peticion recorre de verdad el cliente, la propagacion de
    cabeceras, el middleware de Flask y el de FastAPI, que es justo lo que la
    correlacion tiene que garantizar.
    """
    client = ApiClient(
        web_settings.api_base_url,
        transport=SyncASGITransport(api_app),
    )
    application = create_web_app(web_settings, api_client=client)
    application.config.update(TESTING=True)
    yield application
    client.close()


@pytest.fixture
def capturar_ambos(
    capture_many: Callable[[list[str]], ManyAccessLogs],
) -> ManyAccessLogs:
    """Las dos bitacoras de acceso en el mismo ambito, con claves legibles.

    `capture_many` indexa por nombre de logger, que aqui son nombres internos;
    las pruebas hablan de "web" y "api", que es como las nombra la demo.
    """
    crudo = capture_many([WEB_LOGGER, API_LOGGER])
    return _Renombrado(crudo, {WEB_LOGGER: "web", API_LOGGER: "api"})


class _Renombrado(AbstractContextManager[dict[str, CapturedLogs]]):
    """Reindexa la captura de un context manager sin consumirla dos veces."""

    def __init__(self, interno: Any, alias: dict[str, str]) -> None:
        self._interno = interno
        self._alias = alias

    def __enter__(self) -> dict[str, CapturedLogs]:
        return {self._alias[clave]: lineas for clave, lineas in self._interno.__enter__().items()}

    def __exit__(self, *exc: object) -> None:
        self._interno.__exit__(*exc)


def test_lista_permitida_es_identica_en_ambos_saltos() -> None:
    """La especificacion exige la misma lista permitida en los dos saltos.

    Comparar en un unico test es lo que impide que se separen: si cada suite
    comprobara la suya, una divergencia pasaria las dos.
    """
    assert BACKEND_CAMPOS == WEB_CAMPOS, "las listas permitidas se han separado"


def test_campos_que_la_spec_prohibe_no_existen() -> None:
    for prohibidos in (
        "password",
        "cookie",
        "authorization",
        "token",
        "card",
        "qr",
        "payload",
        "email",
        "user_agent",
    ):
        assert prohibidos not in BACKEND_CAMPOS
        assert prohibidos not in WEB_CAMPOS
