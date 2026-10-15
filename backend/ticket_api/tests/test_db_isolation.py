"""1.5 - PostgreSQL real, con aislamiento por prueba.

La base de pruebas es una base dedicada, nunca la de desarrollo. El aislamiento
se consigue con una transaccion por prueba que se revierte al terminar, apoyandose
en que PostgreSQL tiene el DDL dentro de la transaccion: la tabla que crea la
prueba desaparece con ella y no hace falta ningun paso de limpieza aparte.

El motor se comparte durante toda la sesion de pytest y se cierra al final:
abrir una conexion por prueba multiplicaria el tiempo de la suite.
"""

from __future__ import annotations

import pytest
from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    inspect,
    select,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateTable, DropTable

from ticket_api.config import get_settings
from ticket_api.tests.conftest import (
    BASES_PROHIBIDAS,
    SUFIJO_OBLIGATORIO,
    TABLAS_DE_PRUEBA,
    clave_efimera,
    comprobar_destino,
    url_de_pruebas,
)

_ESQUEMA = MetaData()
TABLAS_DE_PRUEBA.append(_ESQUEMA)
_MARCA = Table(
    "prueba_aislamiento",
    _ESQUEMA,
    Column("id", Integer, primary_key=True),
    Column("valor", String(64), nullable=False),
)


def _crear_marca(sesion: Session, valor: str) -> int:
    """Crea la tabla e inserta una fila dentro de la transaccion abierta.

    El DDL se emite por la sesion y no con `MetaData.create_all(motor)`: eso
    ultimo abre su propia conexion y confirma el `CREATE TABLE`, con lo que el
    `rollback` de la prueba ya no deshace nada y el aislamiento es una fiction.
    """
    sesion.execute(CreateTable(_MARCA, if_not_exists=True))
    return int(
        sesion.execute(_MARCA.insert().values(valor=valor).returning(_MARCA.c.id)).scalar_one()
    )


def test_url_de_pruebas_apunta_a_una_base_dedicada() -> None:
    """GIVEN la configuracion / WHEN se lee el destino de pruebas / THEN es una
    base propia, distinta de la de desarrollo y de las del sistema."""
    objetivo = comprobar_destino(get_settings())

    assert objetivo.name not in BASES_PROHIBIDAS
    assert objetivo.name.endswith(SUFIJO_OBLIGATORIO)


def test_sin_url_de_pruebas_el_error_es_explicito() -> None:
    """Si falta `TEST_DATABASE_URL` el mensaje dice que falta, en vez de dejar
    que reviente la creacion del motor."""
    from ticket_api.config import Settings as Configuracion

    # `_env_file=None` evita que se herede el `TEST_DATABASE_URL` del `.env` del
    # repositorio: este caso prueba justamente que falta.
    sin_pruebas = Configuracion(
        _env_file=None,
        database_url="postgresql://x@localhost:5432/ticketplus_dev",
        jwt_private_key_b64=clave_efimera(),
    )

    with pytest.raises(AssertionError, match="TEST_DATABASE_URL no esta definida"):
        url_de_pruebas(sin_pruebas)


def test_sin_clave_de_firma_la_aplicacion_no_arranca() -> None:
    """La clave de Ed25519 es obligatoria y no tiene default.

    Con una clave fija en el codigo, cualquier despliegue comparte firma: un
    token emitido contra uno valdria en todos, y tras una filtracion no habria
    forma de rotarla. Que la aplicacion se niegue a arrancar es preferible a
    arrancar firmando con una clave que no es de nadie.
    """
    from pydantic import ValidationError

    from ticket_api.config import Settings as Configuracion

    with pytest.raises(ValidationError, match="jwt_private_key_b64"):
        # Se omite a proposito el campo requerido para ver que falla.
        Configuracion(
            _env_file=None, database_url="postgresql://x@localhost:5432/ticketplus_dev"
        )


def test_conexion_a_postgres_responde(engine: Engine) -> None:
    """GIVEN la instancia de PostgreSQL / WHEN se abre una conexion / THEN el
    servidor responde."""
    from sqlalchemy import text

    with engine.connect() as conexion:
        assert conexion.execute(text("SELECT 1")).scalar_one() == 1
        assert conexion.execute(text("SHOW server_version")).scalar_one()


def test_una_prueba_ve_su_propia_escritura(sesion: Session) -> None:
    """Sin esto, un `rollback` global que se comiera las escrituras haria pasar
    la de aislamiento sin comprobar nada."""
    identificador = _crear_marca(sesion, "visible-para-esta-prueba")

    assert identificador > 0
    assert sesion.execute(select(_MARCA.c.valor)).scalar_one() == "visible-para-esta-prueba"


def test_el_rollback_no_filtra_entre_pruebas(
    session_factory: sessionmaker[Session],
) -> None:
    """GIVEN una prueba que escribe y termina sin confirmar / WHEN empieza la
    siguiente / THEN esta no ve ni la tabla ni la fila.

    Las dos sesiones viven en la misma prueba a proposito: el resultado no puede
    depender del orden en que pytest decida ejecutar los casos.
    """
    primera = session_factory()
    try:
        _crear_marca(primera, "no-debe-sobrevivir")
        assert primera.execute(select(_MARCA.c.valor)).scalar_one() == "no-debe-sobrevivir"
    finally:
        primera.rollback()
        primera.close()

    segunda = session_factory()
    try:
        assert not inspect(segunda.get_bind()).has_table(_MARCA.name), (
            "una prueba vio la tabla de la anterior: el aislamiento no funciona"
        )
    finally:
        segunda.rollback()
        segunda.close()


def test_el_rollback_tambien_deshace_el_ddl(sesion: Session) -> None:
    """El aislamiento se apoya en que PostgreSQL mete el DDL dentro de la
    transaccion. Si una migracion creara su tabla fuera, este esquema no
    aislaria nada y el fallo apareceria como datos fantasma mas adelante.

    La inspeccion va sobre `sesion.connection()` y no sobre el motor a proposito:
    el motor prestaria otra conexion, que no ve lo no confirmado, y la
    comprobacion seria imposible de satisfacer.
    """
    sesion.execute(CreateTable(_MARCA, if_not_exists=True))
    assert inspect(sesion.connection()).has_table(_MARCA.name)

    sesion.execute(DropTable(_MARCA, if_exists=True))
    assert not inspect(sesion.connection()).has_table(_MARCA.name)
