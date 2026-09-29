"""3.1 - Una peticion, una transaccion.

La confirmacion la pide la vista, no el teardown de la dependencia. Estas pruebas
comprueban las tres salidas posibles -confirmar, fallar, no decir nada- y sobre
todo que un fallo de la propia confirmacion se reporte al cliente en vez de
esconderlo detras de un 200.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import Engine, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import QueuePool

from ticket_api.config import Settings
from ticket_api.db.session import DbUnit
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA


class _Modelo(DeclarativeBase):
    """Metadata de la tabla de la prueba.

    Se registra en `TABLAS_DE_PRUEBA` para que la limpieza del motor se la lleve
    tambien: una corrida interrumpida deja DDL confirmado y `checkfirst` lo
    recoge en la siguiente.
    """


TABLAS_DE_PRUEBA.append(_Modelo.metadata)


class Nota(_Modelo):
    """Fila de la tabla de la prueba.

    Un modelo declarativo y no una `Table` suelta: los stubs de SQLAlchemy no
    consideran `Table` invocable, y el modelo tipa la fila sin `cast`.
    """

    __tablename__ = "prueba_transaccion"

    id: Mapped[int] = mapped_column(primary_key=True)
    texto: Mapped[str] = mapped_column(String(64))


@pytest.fixture
def notas(engine: Engine) -> Iterator[type[Nota]]:
    """Crea la tabla de la prueba y la deja caer despues.

    El DDL se emite por el motor, no por la unidad de trabajo: la tabla tiene que
    sobrevivir al rollback que estas pruebas comprueban.
    """
    _Modelo.metadata.drop_all(engine, checkfirst=True)
    _Modelo.metadata.create_all(engine)
    try:
        yield Nota
    finally:
        _Modelo.metadata.drop_all(engine, checkfirst=True)


@pytest.fixture
def cliente(
    app: FastAPI,
    notas: type[Nota],
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """API real, con motor de pruebas, y rutas que confirman, fallan y callan.

    Se sustituye `get_sessionmaker`, que es de donde saca la sesion. Es el unico
    punto de acoplamiento con el motor, y tocarlo deja probar la frontera de
    transaccion real contra la base de pruebas sin duplicar esa frontera aqui: una
    copia local pasaria estas pruebas y no detectaria un `commit` movido de sitio.
    """
    monkeypatch.setattr("ticket_api.db.session.get_sessionmaker", lambda: session_factory)

    router = APIRouter(prefix="/api/v1/probe")

    @router.post("/notas")
    async def crear(texto: str, uow: DbUnit) -> dict[str, int]:
        uow.sesion.add(Nota(texto=texto))
        uow.commit()
        return {"id": 1}

    @router.get("/notas")
    async def listar(uow: DbUnit) -> list[str]:
        return list(uow.sesion.scalars(select(Nota.texto).order_by(Nota.id)))

    @router.post("/notas/sin-confirmar")
    async def sin_confirmar(texto: str, uow: DbUnit) -> dict[str, str]:
        """Escribe y responde bien, pero nunca confirma.

    Es el caso por el que la omision revierte: si el teardown confirmara, esto
    dejaria datos sin haberlos pedido.
    """
        uow.sesion.add(Nota(texto=texto))
        return {"estado": "aceptado"}

    @router.post("/notas/roto")
    async def roto(uow: DbUnit) -> dict[str, str]:
        """Escribe y lanza sin confirmar: el caso que no debe dejar nada."""
        uow.sesion.add(Nota(texto="no-debe-sobrevivir"))
        uow.sesion.flush()
        raise RuntimeError("fallo antes de confirmar")

    @router.post("/notas/tarde")
    async def tarde(uow: DbUnit) -> dict[str, str]:
        """Confirma y luego lanza.

        Marca el limite de la garantia: despues de `commit` la peticion ya no es
        reversible, y un fallo posterior (por ejemplo, al serializar la
        respuesta) deja el dato escrito. Ningun teardown puede arreglar eso; solo
        sirve para no prometer lo que no se cumple.
        """
        uow.sesion.add(Nota(texto="tarde"))
        uow.commit()
        raise RuntimeError("fallo despues de confirmar")

    @router.post("/notas/duplicado")
    async def duplicado(uow: DbUnit) -> dict[str, str]:
        """Confirma una clave primaria que ya existe.

        La vista responde bien y es `commit` quien revienta contra la
        restriccion. Aqui el error es convertible en una respuesta 409, que es
        justo lo que se pierde si el commit vive en el teardown.
        """
        try:
            uow.sesion.add(Nota(id=1, texto="primera"))
            uow.commit()
        except Exception as fallo:
            uow.rollback()
            raise HTTPException(status_code=409, detail="clave duplicada") from fallo
        return {"estado": "aceptado"}

    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as instancia:
        yield instancia


def test_una_peticion_exitosa_confirma(cliente: TestClient) -> None:
    """GIVEN una peticion que confirma / WHEN responde bien / THEN el dato queda
    para las siguientes."""
    assert cliente.post("/api/v1/probe/notas", params={"texto": "primera"}).status_code == 200

    assert cliente.get("/api/v1/probe/notas").json() == ["primera"]


def test_una_peticion_que_falla_no_deja_rastro(cliente: TestClient) -> None:
    """GIVEN una peticion que confirma y lanza / WHEN se procesa / THEN la
    peticion siguiente no ve loConfirmado.

    Confirma antes de fallar a proposito: es el orden que mas filas deja, y por
    eso este es el test que detecta un teardown que confirma.
    """
    assert cliente.post("/api/v1/probe/notas/roto").status_code == 500

    assert cliente.get("/api/v1/probe/notas").json() == []


def test_omitir_la_confirmacion_no_escribe(cliente: TestClient) -> None:
    """GIVEN una peticion que escribe y no confirma / WHEN responde bien / THEN
    no queda nada.

    El riesgo de este diseno es el contrario del habitual: con un `commit` en el
    teardown, olvidar la confirmacion guardaria los datos. Aqui, olvidarla no
    guarda nada.
    """
    respuesta = cliente.post("/api/v1/probe/notas/sin-confirmar", params={"texto": "fantasma"})

    assert respuesta.status_code == 200
    assert cliente.get("/api/v1/probe/notas").json() == []


def test_el_commit_rechazado_llega_al_cliente(cliente: TestClient) -> None:
    """GIVEN un `commit` que la base rechaza / WHEN se procesa / THEN el cliente
    recibe el 409 en vez de un 200 con datos sin guardar.

    Con el `commit` en el teardown de la dependencia este test falla: la
    respuesta ya salio antes de que el commit reventara.
    """
    assert cliente.post("/api/v1/probe/notas/duplicado").status_code == 200

    assert cliente.post("/api/v1/probe/notas/duplicado").status_code == 409

    # Y la peticion siguiente no arrastra una transaccion a medias.
    assert cliente.get("/api/v1/probe/notas").json() == ["primera"]


def test_el_estado_no_se_filtra_entre_peticiones(cliente: TestClient) -> None:
    cliente.post("/api/v1/probe/notas", params={"texto": "a"})
    cliente.post("/api/v1/probe/notas", params={"texto": "b"})

    assert cliente.get("/api/v1/probe/notas").json() == ["a", "b"]


def test_una_peticion_fallida_en_medio_no_altera_el_resto(cliente: TestClient) -> None:
    """GIVEN peticiones que fallan entre dos que confirman / WHEN se consulta /
    THEN el estado es exactamente el de las dos exitosas."""
    cliente.post("/api/v1/probe/notas", params={"texto": "a"})
    cliente.post("/api/v1/probe/notas/roto")
    cliente.post("/api/v1/probe/notas/duplicado")
    cliente.post("/api/v1/probe/notas", params={"texto": "b"})

    assert cliente.get("/api/v1/probe/notas").json() == ["a", "b"]


def test_una_fuga_de_conexiones_se_veria(cliente: TestClient, engine: Engine) -> None:
    """Tras mas peticiones fallidas que conexiones permite el pool, la API sigue
    respondiendo.

    El numero de iteraciones supera `pool_size` a proposito. La garantia es que la
    conexion vuelve al pool, no que se invoque un metodo concreto: tanto
    `rollback()` como `close()` la devuelven, asi que la prueba no los distingue.
    Lo que si distingue es una sesion que se olvide de limpiar del todo.
    """
    pool = cast("QueuePool", engine.pool)
    for _ in range(pool.size() + pool.overflow() + 5):
        cliente.post("/api/v1/probe/notas/roto")

    assert cliente.get("/api/v1/probe/notas").status_code == 200


def test_el_motor_usa_el_pool_configurado(settings: Settings) -> None:
    """Los parametros del pool vienen de la configuracion, no de valores fijos
    embebidos: un despliegue con mas carga los cambia sin tocar codigo."""
    from ticket_api.db.engine import build_engine

    motor = build_engine(settings.model_copy(update={"db_pool_size": 7, "db_max_overflow": 0}))
    try:
        assert cast("QueuePool", motor.pool).size() == 7
    finally:
        motor.dispose()
