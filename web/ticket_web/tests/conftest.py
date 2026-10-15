"""Fixtures de la suite de la web."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import Any

import pytest
from fastapi import FastAPI
from flask import Flask
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from ticket_api.config import get_settings
from ticket_api.db.base import Base
from ticket_api.db.engine import build_sessionmaker
from ticket_api.main import create_app as create_api_app
from ticket_api.observability.logging import ACCESS_LOGGER_NAME as API_LOGGER
from ticket_api.observability.logging import ALLOWED_LOG_FIELDS as BACKEND_CAMPOS
from ticket_api.tests.conftest import comprobar_destino, url_de_pruebas

from conftest import CapturedLogs, ManyAccessLogs
from ticket_web.api_client import ApiClient
from ticket_web.config import Settings
from ticket_web.main import create_app as create_web_app
from ticket_web.observability.logging import ACCESS_LOGGER_NAME as WEB_LOGGER
from ticket_web.observability.logging import ALLOWED_LOG_FIELDS as WEB_CAMPOS
from ticket_web.sesion_api import cabecera_bearer
from ticket_web.tests.asgi_transport import SyncASGITransport


@pytest.fixture
def web_settings() -> Settings:
    return Settings(app_name="web-test", api_base_url="http://api.interno", log_level="WARNING")


@pytest.fixture(scope="session")
def motor_api() -> Iterator[Engine]:
    """Motor con el esquema que la API en proceso necesita para funcionar.

    Las pruebas de la web no pueden usar el `engine` del backend porque vive en
    otro paquete de pruebas y pytest no lo reparte. Este motor existe para crear
    el DDL y para atar la API en proceso a la base de pruebas (ver
    `api_en_base_de_pruebas`): la web no lee ni escribe en la base por si misma,
    lo hace la API que corre dentro del proceso.

    El esquema se deja al terminar. Borrar el DDL compartido al final dejaria
    sin tablas a las pruebas del backend que se ejecuten despues en la misma
    sesion.
    """
    comprobar_destino(get_settings())
    motor = create_engine(url_de_pruebas(), pool_pre_ping=True)
    Base.metadata.create_all(motor, checkfirst=True)
    yield motor
    motor.dispose()


@pytest.fixture(scope="session")
def session_factory_api(motor_api: Engine) -> sessionmaker[Session]:
    """Fabrica de sesiones atada a la base de pruebas."""
    return build_sessionmaker(motor_api)


@pytest.fixture(autouse=True)
def api_en_base_de_pruebas(
    monkeypatch: pytest.MonkeyPatch, session_factory_api: sessionmaker[Session]
) -> None:
    """Apunta la API en proceso a la base de pruebas.

    Sin este parche la API en proceso usa `DATABASE_URL` del entorno -la base
    de desarrollo- y cada prueba de la web escribe sus cuentas ahi. Las suites
    del backend parchean este mismo nombre en cada prueba de API; la web habla
    con la API por ASGI, asi que el parche va aqui, una vez, para toda la suite.
    """
    monkeypatch.setattr(
        "ticket_api.db.session.get_sessionmaker", lambda: session_factory_api
    )


@pytest.fixture
def api_app(motor_api: Engine) -> FastAPI:
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
        auth_headers=cabecera_bearer,
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
