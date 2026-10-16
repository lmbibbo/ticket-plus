"""3.3 - La migracion inicial aplicada sobre una base PostgreSQL vacia.

Comprueba el camino que la tarea enuncia a mano, `alembic upgrade head` y
`alembic downgrade base`, pero dentro de la suite para que no dependa de que
alguien recuerde ejecutarlo. Se invoca la API de Alembic en el mismo proceso en
vez de un subproceso: el DSN de pruebas se le pasa explicitamente y no hace falta
tocar el entorno ni esperar a que un `alembic` externo termine.

Lo que protege de verdad esta suite es `test_el_esquema_migrado_no_difiere_de_los_modelos`.
Un esquema que se puede aplicar no dice nada de que sea el que quiere el modelo:
`autogenerate` escribe lo que ve, y si alguien edita el modelo sin escribir la
migracion, ambas siguen funcionando por separado. Comparar las dos cosas es lo
unico que detecta esa situacion.

**Estas pruebas borran tablas.** Por eso pasan antes por `comprobar_destino`, la
misma barrera que el resto de la suite: nunca apuntan a `postgres` ni a la base de
desarrollo. El orden importa, no es decorativo.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint, Engine, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from ticket_api.config import get_settings
from ticket_api.db.base import Base
from ticket_api.tests.conftest import comprobar_destino, url_de_pruebas

#: `backend/alembic.ini`. Se resuelve desde este archivo y no desde el directorio
#: de trabajo, porque `%here` es relativo al `.ini` y pytest puede correr desde
#: cualquier lado.
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

#: Tablas que deja la revision de identidad en `public`. Se comparan por nombre
#: exacto y no por "hay dos tablas": una revision que se olvidara de tirar una
#: tabla intermedia deberia notarse aqui.
TABLAS_DE_IDENTIDAD = frozenset({"auth_sessions", "users", "alembic_version"})

#: Revision de identidad. Se usa para comprobar que 3.3 sigue siendo cierto
#: cuando `head` ya tenga mas migraciones encima: las pruebas de esta suite
#: aplican `head`, asi que sin este ancla, 4.1 habria dejado de comprobar la
#: migracion que la fijo.
REVISION_DE_IDENTIDAD = "e34227427793"

#: Tablas de `head`. Cada revision nueva se anade aqui, y el conjunto exacto hace
#: que una tabla que sobra o que falta se note en la primera prueba que corre.
TABLAS_EN_HEAD = TABLAS_DE_IDENTIDAD | {
    "events",
    "zones",
    "zone_sequences",
    "orders",
    "tickets",
}


def _config() -> Config:
    """Configuracion de Alembic apuntando a la base de pruebas.

    El DSN se fija aqui y no se deja que se lea del entorno a proposito: si
    apuntara al DSN de desarrollo, esta suite aplicaria `downgrade` sobre los
    datos de desarrollo y pasaria sin que nadie se entere.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url_de_pruebas())
    return config


def _motor() -> Engine:
    """Motor nuevo, sin pool compartido con el resto de la suite.

    Las migraciones abren y cierran conexiones propias, y un pool de la sesion de
    pytest podria retener una conexion con el esquema viejo: el fallo apareceria
    una vez de cada varias, que es la forma mas mala de fallar.
    """
    return create_engine(url_de_pruebas(), poolclass=NullPool, future=True)


def _vaciar() -> None:
    """Deja el esquema `public` como recien creado, sin tablas."""
    motor = _motor()
    try:
        with motor.begin() as conexion:
            conexion.execute(text("DROP SCHEMA public CASCADE"))
            conexion.execute(text("CREATE SCHEMA public"))
    finally:
        motor.dispose()


@pytest.fixture
def migraciones() -> Iterator[Config]:
    """Base vacia antes y despues de cada prueba.

    El `finally` importa: una prueba que falla a mitad de un `upgrade` no puede
    dejar el esquema a medio hacer para las siguientes.
    """
    comprobar_destino(get_settings())
    config = _config()
    _vaciar()
    try:
        yield config
    finally:
        _vaciar()


def _tablas(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_head_deja_la_base_con_el_esquema_completo(migraciones: Config) -> None:
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        assert _tablas(motor) == TABLAS_EN_HEAD
        with motor.connect() as conexion:
            assert conexion.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
    finally:
        motor.dispose()


def test_la_revision_de_identidad_sigue_siendo_una_migracion_aplicable(
    migraciones: Config,
) -> None:
    """3.3 no se deja de comprobar por que `head` haya crecido.

    Aplicar `head` y mirar las tablas no dira nada de la primera migracion: si
    esa revision estuviera mal, las siguientes la arrastrarian y el esquema final
    podria salir bien. Se sube solo a la revision de identidad y se mira que deja
    exactamente sus tres tablas.
    """
    command.upgrade(migraciones, REVISION_DE_IDENTIDAD)

    motor = _motor()
    try:
        assert _tablas(motor) == TABLAS_DE_IDENTIDAD
    finally:
        motor.dispose()


def test_downgrade_base_vuelve_a_dejar_la_base_vacia(migraciones: Config) -> None:
    """El `downgrade` importa: es lo que ejecuta el plan de rollback de 8.4."""
    command.upgrade(migraciones, "head")
    command.downgrade(migraciones, "base")

    motor = _motor()
    try:
        assert _tablas(motor) == {"alembic_version"}
    finally:
        motor.dispose()


def test_bajar_a_identidad_deja_solo_sus_tablas(migraciones: Config) -> None:
    """El `downgrade` de la migracion de eventos tambien esta verificado.

    Bajar a la revision de identidad tiene que devolver la base al estado en que
    estaba, sin dejar `zones` colgando de un `events` que ya no esta y sin
    Borrarse las tablas en orden equivocado. Es el camino que se ejecuta cuando
    una revision se deshace, y el orden de los `drop_table` lo hace funcionar o
    fallar segun como se escribio.
    """
    command.upgrade(migraciones, "head")
    command.downgrade(migraciones, REVISION_DE_IDENTIDAD)

    motor = _motor()
    try:
        assert _tablas(motor) == TABLAS_DE_IDENTIDAD
    finally:
        motor.dispose()


def test_el_esquema_migrado_no_difiere_de_los_modelos(migraciones: Config) -> None:
    """Detecta la deriva entre la migracion y los modelos.

    `compare_metadata` hace el `autogenerate` en memoria y no escribe nada: si
    devuelve algo, el esquema que hay en la base ya no es el que describen los
    modelos, y la siguiente `revision --autogenerate` vendria con diferencias.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        with motor.connect() as conexion:
            contexto = MigrationContext.configure(
                connection=conexion,
                opts={
                    "compare_type": True,
                    # Sin esto, `autogenerate` no mira los `server_default` y una
                    # migracion que se olvidara de uno pasaria esta prueba. El
                    # caso real es `zone_sequences.next_number`, que la venta
                    # inicializa por SQL: sin el valor por defecto en la base,
                    # ese INSERT falla con NOT NULL en plena compra.
                    "compare_server_default": True,
                    "target_metadata": Base.metadata,
                },
            )
            diferencias = compare_metadata(contexto, Base.metadata)
    finally:
        motor.dispose()

    assert diferencias == [], f"la migracion no coincide con los modelos: {diferencias}"


def test_las_restricciones_de_identidad_llegan_a_la_base(migraciones: Config) -> None:
    """Las restricciones viajan en la migracion, no solo en el modelo.

    Es la diferencia entre una unicidad que el ORM respeta y una que la base
    impone. Sin esta comprobacion, una migracion que se olvidara de un
    `UniqueConstraint` seguiria pasando todas las pruebas de Python.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        inspector = inspect(motor)

        def _unicas(tabla: str) -> dict[Any, Any]:
            return {
                uq["name"]: uq["column_names"] for uq in inspector.get_unique_constraints(tabla)
            }

        def _foraneas(tabla: str, referida: str) -> list[Any]:
            propias = inspector.get_foreign_keys(tabla)
            return [fk for fk in propias if fk["referred_table"] == referida]

        assert _unicas("users")["uq_users_email"] == ["email"]
        assert _unicas("auth_sessions")["uq_auth_sessions_refresh_token_hash"] == [
            "refresh_token_hash"
        ]

        checks = {c["name"] for c in inspector.get_check_constraints("users")}
        assert {"ck_users_role", "ck_users_status", "ck_users_email_normalizado"} <= checks

        # `CASCADE` y `SET NULL` son las dos mitades de la regla de borrado: al
        # borrar una cuenta se van sus sesiones, y al borrar una sesion no se
        # lleva por delante a la que la sustituyo. Ninguna se deduce de la otra.
        hacia_usuarios = _foraneas("auth_sessions", "users")
        assert hacia_usuarios and hacia_usuarios[0]["options"].get("ondelete") == "CASCADE"

        hacia_sesiones = _foraneas("auth_sessions", "auth_sessions")
        assert hacia_sesiones and hacia_sesiones[0]["options"].get("ondelete") == "SET NULL"

        indices = {ind["name"] for ind in inspector.get_indexes("auth_sessions")}
        assert {"ix_auth_sessions_family_id", "ix_auth_sessions_user_id"} <= indices
        assert "ix_users_role_status" in {ind["name"] for ind in inspector.get_indexes("users")}
    finally:
        motor.dispose()


def test_los_checks_declarados_en_los_modelos_llegan_a_la_base(
    migraciones: Config,
) -> None:
    """Cada `CHECK` del modelo tiene que existir en la base migrada.

    `compare_metadata` no compara `CHECK`, de modo que una migracion que se
    olvidara de uno pasaria la prueba de deriva y todas las de Python: el modelo
    seguiria importandose y el `CHECK` simplemente no estaria. Aqui se comparan
    los conjuntos de nombres en los dos sentidos, para que falte uno o sobre
    uno.

    Lo que hace cada `CHECK` -que un precio negativo no entre, que una zona no se
    repita- lo comprueban las pruebas de `test_models_events.py` por el nombre
    del constraint. Aqui solo se comprueba que viaja.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        inspector = inspect(motor)
        for tabla in TABLAS_EN_HEAD - {"alembic_version"}:
            declarados = {
                constraint.name
                for constraint in Base.metadata.tables[tabla].constraints
                if isinstance(constraint, CheckConstraint)
            }
            en_la_base = {c["name"] for c in inspector.get_check_constraints(tabla)}

            assert declarados == en_la_base, f"los CHECK de {tabla} no coinciden"
    finally:
        motor.dispose()


def test_los_indices_de_eventos_llegan_a_la_base(migraciones: Config) -> None:
    """Los tres indices de 4.1, con su unicidad y sus columnas.

    El indice de zonas es el unico que protege una invariante -"un evento no
    puede repetir zona"- asi que se comprueba que es unico de verdad y no solo
    que existe con ese nombre.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        inspector = inspect(motor)
        eventos = {i["name"]: i for i in inspector.get_indexes("events")}
        zonas = {i["name"]: i for i in inspector.get_indexes("zones")}
        entradas = {i["name"]: i for i in inspector.get_indexes("tickets")}

        assert eventos["ix_events_owner_id_status"]["column_names"] == ["owner_id", "status"]
        assert eventos["ix_events_status_starts_at"]["column_names"] == ["status", "starts_at"]
        assert zonas["uq_zones_event_nombre"]["unique"] is True
        assert zonas["uq_zones_event_nombre"]["column_names"][0] == "event_id"
        # "Mis entradas" del cliente y las entradas de un pedido.
        assert entradas["ix_tickets_client_id"]["column_names"] == ["client_id"]
        assert entradas["ix_tickets_order_id"]["column_names"] == ["order_id"]
    finally:
        motor.dispose()


def test_las_claves_de_ventas_llegan_a_la_base(migraciones: Config) -> None:
    """Las dos unicidades que sostienen la venta, con su nombre y sus columnas.

    Son las dos reglas que 5.1 no puede dejar en manos del dominio: dos peticiones
    con la misma clave a la vez, y dos entradas con el mismo numero en la misma
    zona. Se comprueba que existen *y* que la base las hace cumplir, porque un
    `UNIQUE` con las columnas cambiadas pasaria la comprobacion de nombres y
    dejaria pasar la venta sin proteccion.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        inspector = inspect(motor)
        claves = {
            c["name"]: c["column_names"]
            for c in inspector.get_unique_constraints("orders")
            + inspector.get_unique_constraints("tickets")
        }

        assert claves["uq_orders_client_idempotency_key"] == ["client_id", "idempotency_key"]
        assert claves["uq_tickets_zone_id_number"] == ["zone_id", "number"]

        with motor.begin() as conexion:
            conexion.execute(text("INSERT INTO users (email, password_hash, role, status) "
                                  "VALUES ('a@ejemplo.com', 'x', 'client', 'active')"))
            cliente = conexion.scalar(text("SELECT id FROM users WHERE email = 'a@ejemplo.com'"))
            conexion.execute(
                text("INSERT INTO users (email, password_hash, role, status) "
                     "VALUES ('p@ejemplo.com', 'x', 'event-owner', 'active')")
            )
            proveedor = conexion.scalar(
                text("SELECT id FROM users WHERE email = 'p@ejemplo.com'")
            )
            conexion.execute(
                text("INSERT INTO events (owner_id, name, status, starts_at, ends_at) "
                     "VALUES (:dueno, 'Concierto', 'published', "
                     "'2030-01-01 20:00+00', '2030-01-01 23:00+00')"),
                {"dueno": proveedor},
            )
            evento = conexion.scalar(text("SELECT id FROM events WHERE name = 'Concierto'"))
            conexion.execute(
                text("INSERT INTO orders (client_id, event_id, status, idempotency_key, "
                     "request_hash, total) "
                     "VALUES (:cliente, :evento, 'pending', 'compra-1', :hash, 10.00)"),
                {"cliente": cliente, "evento": evento, "hash": "a" * 64},
            )
            with pytest.raises(IntegrityError, match="uq_orders_client_idempotency_key"):
                conexion.execute(
                    text("INSERT INTO orders (client_id, event_id, status, idempotency_key, "
                         "request_hash, total) "
                         "VALUES (:cliente, :evento, 'pending', 'compra-1', :hash, 10.00)"),
                    {"cliente": cliente, "evento": evento, "hash": "a" * 64},
                )
    finally:
        motor.dispose()


def test_las_llaves_foraneas_de_ventas_no_borran_en_cascada(migraciones: Config) -> None:
    """Las cinco `RESTRICT` de 5.1, con su accion real y no solo su nombre.

    Un pedido pertenece a un evento y a un cliente, y sus entradas a un pedido, a
    una zona y a un cliente. Si alguna de las cinco fuera `CASCADE`, borrar un
    evento con pedidos -o un cliente con entradas- se llevaria por delante el
    historial de lo vendido sin que nadie lo pidiera. Que la accion sea `RESTRICT`
    y no `NO ACTION` importa tambien: con `NO ACTION` podria aplazar el borrado
    hasta el fin de la transaccion y el dominio tendria que acordarse de mirar.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        inspector = inspect(motor)
        acciones = {
            (tabla, regla["name"]): str(regla["options"].get("ondelete", ""))
            for tabla in ("orders", "tickets")
            for regla in inspector.get_foreign_keys(tabla)
        }

        esperadas = {
            ("orders", "orders_client_id_fkey"): "RESTRICT",
            ("orders", "orders_event_id_fkey"): "RESTRICT",
            ("tickets", "tickets_client_id_fkey"): "RESTRICT",
            ("tickets", "tickets_order_id_fkey"): "RESTRICT",
            ("tickets", "tickets_zone_id_fkey"): "RESTRICT",
        }
        assert acciones == esperadas
    finally:
        motor.dispose()


def test_la_secuencia_se_puede_crear_por_sql_sin_darle_numero(
    migraciones: Config,
) -> None:
    """`next_number` llega a la base con `server_default`, no solo con `default`.

    Es el `INSERT` que hara 5.4 al inicializar la secuencia dentro de la
    transaccion de venta. Si el valor por defecto viviera solo en Python, ese
    INSERT -que va por SQL, con la zona ya bloqueada- fallaria con NOT NULL y la
    compra se caeria.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        with motor.begin() as conexion:
            dueno: uuid.UUID = conexion.execute(
                text(
                    "INSERT INTO users (email, password_hash, role, status) "
                    "VALUES ('prov@ejemplo.com', 'hash', 'event-owner', 'active') RETURNING id"
                )
            ).scalar_one()
            evento: uuid.UUID = conexion.execute(
                text(
                    "INSERT INTO events (owner_id, name, starts_at, ends_at, status) "
                    "VALUES (:dueno, 'Evento', '2027-01-01 18:00+00', '2027-01-01 23:00+00', "
                    "'draft') RETURNING id"
                ),
                {"dueno": dueno},
            ).scalar_one()
            zona: uuid.UUID = conexion.execute(
                text(
                    "INSERT INTO zones (event_id, name, capacity, price) "
                    "VALUES (:evento, 'General', 100, 25.00) RETURNING id"
                ),
                {"evento": evento},
            ).scalar_one()
            siguiente: int = conexion.execute(
                text("INSERT INTO zone_sequences (zone_id) VALUES (:zona) RETURNING next_number"),
                {"zona": zona},
            ).scalar_one()
    finally:
        motor.dispose()

    assert siguiente == 1


def test_los_identificadores_tienen_valor_por_defecto_en_la_base(migraciones: Config) -> None:
    """Un `INSERT` que no pasa por el ORM no debe fallar con NOT NULL.

    Es el motivo del `server_default`: una cuenta creada por SQL directo -un
    script de mantenimiento, una carga, una migracion- tiene que funcionar sin
    que el dominio fabrique el UUID.
    """
    command.upgrade(migraciones, "head")

    motor = _motor()
    try:
        with motor.begin() as conexion:
            fila = conexion.execute(
                text(
                    "INSERT INTO users (email, password_hash, role, status) "
                    "VALUES ('ana@ejemplo.com', 'hash', 'client', 'active') "
                    "RETURNING id, created_at"
                )
            ).one()
    finally:
        motor.dispose()

    assert isinstance(fila.id, uuid.UUID)
    assert fila.created_at is not None


def test_un_ciclo_de_upgrade_y_downgrade_se_repite(migraciones: Config) -> None:
    """Un ciclo completo se puede repetir sin quedar a medias.

    Es lo que se hace en cada despliegue y lo que ensaya 8.4: si el segundo
    `upgrade` falla, el problema aparece en produccion y no en la suite.
    """
    for _ in range(2):
        command.upgrade(migraciones, "head")
        command.downgrade(migraciones, "base")

    command.upgrade(migraciones, "head")
    motor = _motor()
    try:
        assert _tablas(motor) == TABLAS_EN_HEAD
    finally:
        motor.dispose()
