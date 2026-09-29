"""Fixtures de las pruebas del backend.

La configuracion de la base de pruebas vive aqui y no en un archivo suelto: la
comprobacion de que el DSN apunta a una base segura se ejecuta una vez, al crear
el motor, y todas las suites de base de datos dependen de ella.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.config import Settings, get_settings
from ticket_api.main import create_app
from ticket_api.observability.logging import ACCESS_LOGGER_NAME

#: Bases en las que una prueba no puede escribir ni borrar, pase lo que pase con
#: las variables de entorno.
BASES_PROHIBIDAS: frozenset[str] = frozenset({"postgres", "template0", "template1"})

#: Sufijo obligatorio del nombre de la base de pruebas. Es la segunda barrera
#: despues de la lista negra: si alguien apunta `TEST_DATABASE_URL` a la base de
#: desarrollo, se detecta antes de la primera escritura.
SUFIJO_OBLIGATORIO: str = "_test"

#: Metadatos de las tablas que crean las pruebas. Cada archivo que declare
#: modelos anade el suyo, para que la limpieza del motor se lleve el DDL de
#: todos ellos y una corrida interrumpida no deje columnas a medio hacer.
TABLAS_DE_PRUEBA: list[MetaData] = []


@pytest.fixture
def settings() -> Settings:
    """Configuracion del backend para las pruebas.

    El DSN no se usa todavia: el motor llega en 3.1 y la primera migracion en
    3.3. Se pone uno valido para que cualquier import temprano que lo lea no
    falle por el entorno.
    """
    return Settings(
        database_url="postgresql+psycopg://postgres:postgres@localhost:5432/ticketplus_test",
        test_database_url="postgresql+psycopg://postgres:postgres@localhost:5432/ticketplus_test",
        app_name="backend",
    )


@pytest.fixture
def app(settings: Settings) -> Any:
    """Instancia de la API.

    Se construye antes de capturar logs porque `create_app` reconfigura el
    logger de acceso y se llevaria por delante cualquier manejador de prueba.
    """
    return create_app(settings)


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    """Cliente HTTP contra la API en proceso."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """A que base apunta una cadena de conexion."""

    dsn: str
    name: str

    @classmethod
    def from_dsn(cls, dsn: str) -> DatabaseTarget:
        return cls(dsn=dsn, name=dsn.rsplit("/", 1)[-1].split("?", 1)[0])


def url_de_pruebas(settings: Settings | None = None) -> str:
    """Devuelve el DSN de pruebas o explica por que no se puede seguir.

    Fallar aqui y no al abrir la conexion evita el clasico error de Pydantic en
    el que el mensaje solo dice que el campo no es un entero valido.
    """
    configuracion = settings or get_settings()
    if not configuracion.test_database_url:
        raise AssertionError(
            "TEST_DATABASE_URL no esta definida: las pruebas de base de datos no "
            "pueden ejecutarse sin una base de pruebas propia"
        )
    return configuracion.test_database_url


def comprobar_destino(configuracion: Settings) -> DatabaseTarget:
    """Verifica que el DSN es seguro de usar y devuelve a que base apunta."""
    objetivo = DatabaseTarget.from_dsn(url_de_pruebas(configuracion))
    desarrollo = DatabaseTarget.from_dsn(configuracion.database_url)

    assert objetivo.name, "el DSN de pruebas no indica base"
    assert objetivo.name not in BASES_PROHIBIDAS, f"base prohibida: {objetivo.name}"
    assert objetivo.name.endswith(SUFIJO_OBLIGATORIO), (
        f"la base de pruebas debe terminar en {SUFIJO_OBLIGATORIO}, no en {objetivo.name!r}"
    )
    assert objetivo.name != desarrollo.name, "las pruebas apuntan a la base de desarrollo"
    return objetivo


def _limpiar_ddl(motor: Engine) -> None:
    for metadatos in TABLAS_DE_PRUEBA:
        metadatos.drop_all(motor, checkfirst=True)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Motor compartido; las comprobaciones de destino se hacen una sola vez.

    El DDL sobrante de una ejecucion interrumpida se limpia antes de empezar:
    sin esto, `create_all` de la corrida siguiente encontraria una tabla a medio
    hacer y las pruebas fallarian con un error de columna que no senala la causa.
    """
    comprobar_destino(get_settings())
    motor = create_engine(
        url_de_pruebas(),
        pool_pre_ping=True,
        # Sin este limite, una sesion que no se limpia hace que la prueba se
        # cuelgue esperando una conexion en vez de fallar. Un fallo en 5s dice
        # "fuga de conexiones"; un cuelgue indefinido no dice nada.
        pool_timeout=5,
        future=True,
    )
    _limpiar_ddl(motor)
    try:
        yield motor
    finally:
        _limpiar_ddl(motor)
        motor.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def sesion(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Sesion que se revierte siempre al terminar la prueba.

    Las pruebas no hacen `commit` por costumbre: escriben y leen dentro de la
    transaccion abierta. Dejar el `COMMIT` fuera de la prueba es lo que hace que
    el aislamiento sea una garantia y no una costumbre.
    """
    activa = session_factory()
    try:
        yield activa
    finally:
        activa.rollback()
        activa.close()




@pytest.fixture
def captured_lines(
    capture_access: Callable[[str], Any],
) -> Callable[[], Any]:
    """Devuelve un context manager que captura la bitacora del backend.

    Se pasa por parametro el nombre del logger en vez de leerlo aqui, para que
    renombrar `ACCESS_LOGGER_NAME` no rompa esta suite en silencio.
    """

    @contextmanager
    def _capture() -> Iterator[list[dict[str, Any]]]:
        with capture_access(ACCESS_LOGGER_NAME) as lines:
            yield lines

    return _capture
