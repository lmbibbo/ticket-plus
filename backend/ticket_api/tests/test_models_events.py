"""4.1 - Restricciones e indices de `events`, `zones` y `zone_sequences`.

La mayoria comprueba comportamiento a traves de la base, no la forma del modelo:
insertar dos zonas que se parecen y ver que la base lo rechaza. Un indice o un
`CHECK` retirado del esquema se detecta aqui aunque el modelo siga importandose
sin error.

Las dos invariantes que la tarea enuncia por nombre tienen su propia clase:

**Un evento no puede repetir zona.** Se prueba con `General` y `general`, y no
con dos cadenas identicas, porque son el caso que un `UNIQUE (event_id, name)`
dejaria pasar. Con mayusculas distintas el dominio creeria estar anadiendo una
zona nueva, el catalogo mostraria dos zonas y la disponibilidad se repartiria
entre las dos. El precio y la capacidad se comprueban tambien: dos zonas con el
mismo nombre y capacidades distintas no son "la misma zona", son dos filas.

**Una zona tiene una sola secuencia.** Se comprueba de dos maneras, porque son
distintas: que la base no admite una segunda fila para la misma zona, y que la
columna es la clave primaria y no una clave foranea mas. La segunda es la que
importa: con una `UNIQUE` la segunda fila entraria y solo fallaria al numerar,
con un pedido a medias.

Los indices se comprueban por columnas y no por rendimiento. Medir que PostgreSQL
usa un indice es fragil -depende de la estimacion de cardinalidad, del volumen y
del planificador-; lo que se protege es que exista y cubra lo que se busca.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticket_api.db.base import Base
from ticket_api.models import AuthSession, Event, EventStatus, Role, User, Zone, ZoneSequence
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

_HASH_EJEMPLO = "$argon2id$v=19$m=65536,t=3,p=4$sal$hash"

#: Un dia de evento que no depende de la hora a la que corra la prueba.
INICIO = datetime(2027, 6, 1, 18, 0, tzinfo=UTC)
FIN = datetime(2027, 6, 1, 23, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Crea y destruye las tablas reales del modelo.

    Se crea el esquema de `Base` entero, no solo las tablas de este modulo, y se
    tira al terminar. Al reusar `Base.metadata` se dependen del orden de las
    pruebas, y el orden no es una garantia.
    """
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


def _proveedor(sesion: Session, email: str = "proveedor@ejemplo.com") -> User:
    proveedor = User(
        email=email,
        password_hash=_HASH_EJEMPLO,
        role=Role.EVENT_OWNER.value,
    )
    sesion.add(proveedor)
    sesion.flush()
    return proveedor


def _evento(
    sesion: Session,
    owner: User,
    nombre: str = "Concierto de prueba",
    status: str = EventStatus.DRAFT.value,
) -> Event:
    evento = Event(
        owner=owner,
        name=nombre,
        starts_at=INICIO,
        ends_at=FIN,
        status=status,
    )
    sesion.add(evento)
    sesion.flush()
    return evento


def _proveedor_efimero() -> User:
    """Cuenta sin persistir, para las pruebas que solo miran la construccion."""
    return User(
        email="sin-guardar@ejemplo.com",
        password_hash=_HASH_EJEMPLO,
        role=Role.EVENT_OWNER.value,
    )


def _zona(
    sesion: Session,
    evento: Event,
    nombre: str = "General",
    capacity: int = 100,
    price: Decimal | str = "25.00",
) -> Zone:
    zona = Zone(event=evento, name=nombre, capacity=capacity, price=price)
    sesion.add(zona)
    sesion.flush()
    return zona


class TestUnEventoNoPuedeRepetirZona:
    def test_no_admite_dos_zonas_con_el_mismo_nombre(self, sesion: Session) -> None:
        evento = _evento(sesion, _proveedor(sesion))
        _zona(sesion, evento, nombre="General")

        sesion.add(Zone(event=evento, name="General", capacity=50, price="10.00"))
        with pytest.raises(IntegrityError, match="uq_zones_event_nombre"):
            sesion.flush()
        sesion.rollback()

    @pytest.mark.parametrize("repetida", ["general", "GENERAL", "  General  ", "General  "])
    def test_no_admite_la_misma_zona_con_otra_grafia(
        self, sesion: Session, repetida: str
    ) -> None:
        """El caso que un `UNIQUE (event_id, name)` de columnas dejaria pasar.

        Es el que se cuela de verdad: el proveedor teclea "general" sin querer
        cambiar las mayusculas, el dominio lo da por bueno y aparecen dos zonas
        con el mismo nombre y capacidades distintas.
        """
        evento = _evento(sesion, _proveedor(sesion))
        _zona(sesion, evento, nombre="General")

        sesion.add(Zone(event=evento, name=repetida, capacity=50, price="10.00"))
        with pytest.raises(IntegrityError, match="uq_zones_event_nombre"):
            sesion.flush()
        sesion.rollback()

    def test_el_espacio_sobrante_no_crea_una_zona_nueva(self, sesion: Session) -> None:
        """Los espacios del medio se aplanan antes de escribir, no despues.

        `btrim` de PostgreSQL quita los de los extremos y deja los del medio, de
        modo que sin el `@validates` del modelo "General" y "General  " serian dos
        cadenas distintas para el indice y dos zonas en el catalogo.
        """
        evento = _evento(sesion, _proveedor(sesion))
        creada = _zona(sesion, evento, nombre="General")

        sesion.add(Zone(event=evento, name="General  ", capacity=50, price="10.00"))
        with pytest.raises(IntegrityError, match="uq_zones_event_nombre"):
            sesion.flush()
        sesion.rollback()

        # Y el nombre guardado es el que se escribio, con su grafia.
        assert creada.name == "General"

    def test_la_misma_zona_en_eventos_distintos_son_dos_zonas(self, sesion: Session) -> None:
        """La unicidad es por evento, no global.

        Dos recitales distintos pueden tener los dos una zona "General"; lo que
        no puede repetirse es dentro del mismo evento.
        """
        proveedor = _proveedor(sesion)
        primero = _evento(sesion, proveedor, nombre="Recital en Madrid")
        segundo = _evento(sesion, proveedor, nombre="Recital en Sevilla")

        _zona(sesion, primero, nombre="General")
        _zona(sesion, segundo, nombre="General")

        assert sesion.scalar(select(func.count()).select_from(Zone)) == 2

    def test_el_nombre_se_guarda_con_su_grafia(self, sesion: Session) -> None:
        """`VIP` se muestra como `VIP`. Comparar sin mayusculas no obliga a
        mostrarla en minusculas: de eso se encarga la expresion del indice."""
        evento = _evento(sesion, _proveedor(sesion))

        zona = _zona(sesion, evento, nombre="  Zona   VIP  ")

        assert zona.name == "Zona VIP"


class TestUnaZonaTieneUnaSolaSecuencia:
    def test_la_secuencia_empieza_en_uno(self, sesion: Session) -> None:
        """La numeracion de entradas empieza en 1, segun la especificacion.

        Un `next_number` de 0 asignaria a la primera compra el numero 0, que no
        existe en la lista de entradas de la zona.
        """
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))

        secuencia = ZoneSequence(zone=zona)
        sesion.add(secuencia)
        sesion.commit()

        assert secuencia.next_number == 1

    def test_la_base_pone_el_primer_numero_sin_que_se_lo_digan(
        self, sesion: Session, engine: Engine
    ) -> None:
        """Un `INSERT` que no pasa por el ORM tiene que funcionar.

        Es lo que hara 5.4 al inicializar la secuencia dentro de la transaccion
        de venta: si `next_number` no tuviera `server_default`, ese insert
        fallaria con NOT NULL en plena compra.
        """
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))
        sesion.commit()

        with sesion.begin_nested():
            sesion.execute(
                text("INSERT INTO zone_sequences (zone_id) VALUES (:zona)"), {"zona": zona.id}
            )

        assert sesion.scalar(
            select(ZoneSequence.next_number).where(ZoneSequence.zone_id == zona.id)
        ) == 1

    def test_no_admite_una_segunda_secuencia_para_la_misma_zona(
        self, sesion: Session
    ) -> None:
        """La garantia es de la base, asi que se prueba por SQL.

        Por el ORM se podria cambiar el resultado sin que la tabla cambie: al
        reasignar `zone.sequence`, el ORM decide que hacer con la fila anterior.
        Lo que no se puede cambiar es que en la base no caben dos filas con la
        misma zona.
        """
        evento = _evento(sesion, _proveedor(sesion))
        primera = _zona(sesion, evento, nombre="General")
        segunda = _zona(sesion, evento, nombre="Tribuna")
        sesion.add(ZoneSequence(zone=primera, next_number=7))
        sesion.commit()

        with pytest.raises(IntegrityError, match="zone_sequences_pkey"):
            sesion.execute(
                text("INSERT INTO zone_sequences (zone_id, next_number) VALUES (:zona, 1)"),
                {"zona": primera.id},
            )
        sesion.rollback()

        # La zona que si tiene su propia secuencia no se ve afectada, y la que
        # ya tenia contador lo conserva con su valor.
        sesion.add(ZoneSequence(zone=segunda, next_number=1))
        sesion.commit()
        assert sesion.scalar(select(func.count()).select_from(ZoneSequence)) == 2
        assert sesion.scalar(
            select(ZoneSequence.next_number).where(ZoneSequence.zone_id == primera.id)
        ) == 7

    def test_el_contador_sobrevive_a_recargar_la_sesion(self, sesion: Session) -> None:
        """El contador es la fila, no un atributo de la zona.

        Que el valor venga siempre de la base es lo que impide que dos partes del
        codigo tengan cada una su idea de por donde va la numeracion. Y que no
        exista una relacion `Zone.sequence` que reasignar es lo que impide que un
        `delete-orphan` reinicie la zona en 1 y repita numeros que ya tienen
        entrada.
        """
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))
        sesion.add(ZoneSequence(zone=zona, next_number=7))
        sesion.commit()

        sesion.expire_all()

        recargada = sesion.get(Zone, zona.id)
        assert recargada is not None
        assert not hasattr(recargada, "sequence")
        contador = sesion.get(ZoneSequence, zona.id)
        assert contador is not None
        assert contador.next_number == 7

    def test_la_zona_es_la_clave_primaria_de_su_secuencia(
        self, sesion: Session, engine: Engine
    ) -> None:
        """La invariante es la forma de la tabla, no una restriccion aparte.

        Con `zone_id` como clave primaria no existe un estado de la base en el
        que una zona tenga dos secuencias. Con una `UNIQUE` sobre una columna
        suelta existiria, y el fallo apareceria al numerar un pedido, con
        numeros repetidos.
        """
        primary = inspect(engine).get_pk_constraint("zone_sequences")

        assert primary["constrained_columns"] == ["zone_id"]

    def test_una_zona_no_tiene_secuencia_hasta_que_vende(self, sesion: Session) -> None:
        """Tener cero o una secuencia es lo esperado, no un error.

        La fila se crea en la primera venta (5.4). Si se creara al crear la zona,
        habria que decidir que hacer con las zonas que nunca venden, y una fila
        `next_number = 1` para una zona sin entradas es ruido.
        """
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))

        assert sesion.scalar(
            select(func.count())
            .select_from(ZoneSequence)
            .where(ZoneSequence.zone_id == zona.id)
        ) == 0
        assert sesion.get(ZoneSequence, zona.id) is None

    @pytest.mark.parametrize("siguiente", [0, -1])
    def test_rechaza_un_numero_que_no_puede_existir(
        self, sesion: Session, siguiente: int
    ) -> None:
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))
        sesion.add(ZoneSequence(zone=zona, next_number=siguiente))
        with pytest.raises(IntegrityError, match="ck_zone_sequences_next_number"):
            sesion.flush()
        sesion.rollback()

    def test_la_secuencia_desaparece_con_su_zona(self, sesion: Session) -> None:
        """Sin cascada, borrando la zona quedaria una secuencia apuntando al
        vacio y la tabla impediria borrar la zona en los deployments que si la
        respetan."""
        zona = _zona(sesion, _evento(sesion, _proveedor(sesion)))
        sesion.add(ZoneSequence(zone=zona))
        sesion.commit()
        identificador = zona.id

        sesion.delete(zona)
        sesion.commit()

        assert sesion.scalar(
            select(func.count())
            .select_from(ZoneSequence)
            .where(ZoneSequence.zone_id == identificador)
        ) == 0


class TestRestriccionesDelEvento:
    def test_rechaza_un_estado_fuera_de_la_lista(self, sesion: Session) -> None:
        sesion.add(
            Event(
                owner=_proveedor(sesion),
                name="Evento raro",
                starts_at=INICIO,
                ends_at=FIN,
                status="borrador",
            )
        )
        with pytest.raises(IntegrityError, match="ck_events_status"):
            sesion.flush()
        sesion.rollback()

    def test_acepta_los_tres_estados_del_ciclo(self, sesion: Session) -> None:
        proveedor = _proveedor(sesion)
        for indice, estado in enumerate(EventStatus):
            sesion.add(
                Event(
                    owner=proveedor,
                    name=f"Evento {indice}",
                    starts_at=INICIO,
                    ends_at=FIN,
                    status=estado.value,
                )
            )
        sesion.flush()

        assert sesion.scalar(select(func.count()).select_from(Event)) == len(EventStatus)

    def test_un_evento_nace_en_borrador(self, sesion: Session) -> None:
        """El estado inicial lo pone el dominio; la base solo limita los valores.

        Los valores validos son una regla de la tabla -y por eso hay `CHECK`-, pero
        que un evento nazca en `draft` y no en `published` es una regla del ciclo
        de vida, y esa vive en el modelo. Un evento creado por el dominio nace
        siempre en `draft`.
        """
        sesion.add(
            Event(owner=_proveedor(sesion), name="Evento nuevo", starts_at=INICIO, ends_at=FIN)
        )
        sesion.commit()

        guardado = sesion.scalar(select(Event))
        assert guardado is not None
        assert guardado.status == EventStatus.DRAFT.value
        assert guardado.es_editable

    def test_rechaza_un_evento_que_termina_antes_de_empezar(self, sesion: Session) -> None:
        sesion.add(
            Event(owner=_proveedor(sesion), name="Al reves", starts_at=FIN, ends_at=INICIO)
        )
        with pytest.raises(IntegrityError, match="ck_events_ventana_horaria"):
            sesion.flush()
        sesion.rollback()

    def test_rechaza_un_evento_que_no_dura_nada(self, sesion: Session) -> None:
        """Terminar en el mismo instante en que empieza es un evento de duracion
        cero, que no se puede publicar ni ordenar en el catalogo."""
        sesion.add(
            Event(
                owner=_proveedor(sesion), name="Instantaneo", starts_at=INICIO, ends_at=INICIO
            )
        )
        with pytest.raises(IntegrityError, match="ck_events_ventana_horaria"):
            sesion.flush()
        sesion.rollback()

    @pytest.mark.parametrize("nombre", ["", "   "])
    def test_rechaza_un_evento_sin_nombre(self, nombre: str) -> None:
        """El dominio lo ve al construir, antes de tocar la base.

        El nombre vacio es un error de peticion -un formulario que se envia en
        blanco- y por eso vale mas como un 422 con el campo señalado que como un
        409 de la base. El `CHECK` de la tabla, que se prueba aparte, cubre el
        otro camino: lo que entre sin pasar por aqui.
        """
        with pytest.raises(ValueError, match="vacio"):
            Event(owner=_proveedor_efimero(), name=nombre, starts_at=INICIO, ends_at=FIN)

    def test_la_base_rechaza_un_nombre_solo_espacios(
        self, sesion: Session, engine: Engine
    ) -> None:
        """El `CHECK` protege tambien de lo que no pasa por el modelo."""
        proveedor = _proveedor(sesion)
        sesion.commit()

        with pytest.raises(IntegrityError, match="ck_events_name_no_vacio"):
            sesion.execute(
                text(
                    "INSERT INTO events (owner_id, name, starts_at, ends_at, status) "
                    "VALUES (:dueno, '   ', :inicio, :fin, 'draft')"
                ),
                {"dueno": proveedor.id, "inicio": INICIO, "fin": FIN},
            )
        sesion.rollback()

    def test_un_evento_necesita_dueno(self, sesion: Session) -> None:
        sesion.add(
            Event(
                owner_id=uuid.uuid4(),
                name="Sin dueno",
                starts_at=INICIO,
                ends_at=FIN,
            )
        )
        with pytest.raises(IntegrityError, match="events_owner_id_fkey"):
            sesion.flush()
        sesion.rollback()

    def test_las_fechas_vuelven_con_zona_horaria(self, sesion: Session) -> None:
        """Un instante sin offset no se puede comparar con otro que si lo tiene.

        Un evento creado en UTC y leido en la zona del servidor daria
        `naive < aware` y un `TypeError` en la comparacion mas inesperada.
        """
        sesion.add(
            Event(
                owner=_proveedor(sesion), name="Con fechas", starts_at=INICIO, ends_at=FIN
            )
        )
        sesion.commit()

        guardado = sesion.scalar(select(Event))
        assert guardado is not None
        assert guardado.starts_at.tzinfo is not None
        assert guardado.starts_at == INICIO
        assert guardado.ends_at == FIN


class TestRestriccionesDeZona:
    @pytest.mark.parametrize("capacidad", [-1, -100])
    def test_rechaza_una_capacidad_negativa(
        self, sesion: Session, capacidad: int
    ) -> None:
        evento = _evento(sesion, _proveedor(sesion))

        sesion.add(Zone(event=evento, name="General", capacity=capacidad, price="10.00"))
        with pytest.raises(IntegrityError, match="ck_zones_capacity_no_negativa"):
            sesion.flush()
        sesion.rollback()

    def test_una_capacidad_de_cero_son_valida(self, sesion: Session) -> None:
        """Cero es una zona cerrada a la venta, no una zona rota.

        Se comprueba ahora porque es el limite: si alguien endurece el `CHECK` a
        `> 0` para "ser mas estricto", esta prueba lo avisa.
        """
        evento = _evento(sesion, _proveedor(sesion))

        zona = _zona(sesion, evento, nombre="Cerrada", capacity=0, price="0.00")

        assert zona.capacity == 0

    @pytest.mark.parametrize("precio", ["-0.01", "-100.00"])
    def test_rechaza_un_precio_negativo(self, sesion: Session, precio: str) -> None:
        evento = _evento(sesion, _proveedor(sesion))

        sesion.add(Zone(event=evento, name="General", capacity=10, price=precio))
        with pytest.raises(IntegrityError, match="ck_zones_precio_no_negativo"):
            sesion.flush()
        sesion.rollback()

    def test_un_precio_de_cero_son_valido(self, sesion: Session) -> None:
        """Un evento gratuito se representa con precio cero, no con precio nulo."""
        evento = _evento(sesion, _proveedor(sesion))

        zona = _zona(sesion, evento, nombre="Gratis", capacity=10, price="0.00")

        assert zona.price == Decimal("0.00")

    def test_rechaza_un_precio_con_mas_decimals_de_los_que_admite(self) -> None:
        """Un precio de `10.999` es un error del dominio, no uno de la base.

        Sin esta comprobacion, el `INSERT` llegaria a `NUMERIC(12, 2)` y
        PostgreSQL lo cortaria a 10.99 en silencio, o devolveria un error de
        precision que el servidor convertiria en un 500 con el DSN dentro.
        """
        with pytest.raises(ValueError, match="decimales"):
            Zone(name="General", capacity=10, price=Decimal("10.999"))

    def test_rechaza_un_precio_que_no_cabe_en_la_columna(self) -> None:
        with pytest.raises(ValueError, match="maximo"):
            Zone(name="General", capacity=10, price=Decimal("12345678901.50"))

    def test_rechaza_un_precio_con_coma_flotante(self) -> None:
        """El precio es dinero y se expresa con `Decimal`.

        Aceptar un `float` seria aceptar el error de redondeo en la puerta: 0.1
        en binario vale 0.1000000000000000055511151231257827, y eso viaja por el
        total del pedido hasta el redondeo final.
        """
        with pytest.raises(ValueError, match="Decimal"):
            Zone(name="General", capacity=10, price=0.1)

    @pytest.mark.parametrize("precio", ["NaN", "Infinity"])
    def test_rechaza_un_precio_que_no_es_finito(self, precio: str) -> None:
        """`NaN` pasa cualquier comparacion con `>=` sin quejarse, y un precio
        que no se puede comparar no es un precio."""
        with pytest.raises(ValueError, match="finito"):
            Zone(name="General", capacity=10, price=Decimal(precio))

    def test_el_precio_sobrevive_ida_y_vuelta_sin_deriva(self, sesion: Session) -> None:
        """El motivo de `NUMERIC` y no `double precision`, comprobado.

        Con coma flotante, tres entradas a 0.10 darian 0.30000000000000004 y el
        total habria que redondearlo. Con `NUMERIC`, el total es 0.30 exacto y
        el redondeo no hace falta.
        """
        evento = _evento(sesion, _proveedor(sesion))
        zona = _zona(sesion, evento, nombre="General", capacity=10, price="0.10")
        sesion.commit()
        sesion.expire_all()

        leido = sesion.get(Zone, zona.id)
        assert leido is not None
        assert leido.price == Decimal("0.10")
        assert leido.price * 3 == Decimal("0.30")

    def test_una_zona_no_puede_vivir_sin_evento(self, sesion: Session) -> None:
        sesion.add(Zone(event_id=uuid.uuid4(), name="Huerfana", capacity=10, price="5.00"))
        with pytest.raises(IntegrityError, match="zones_event_id_fkey"):
            sesion.flush()
        sesion.rollback()

    def test_borrar_el_evento_se_lleva_sus_zonas_y_sus_secuencias(
        self, sesion: Session
    ) -> None:
        """La cascada evento -> zona -> secuencia.

        Una zona sin evento no significa nada, y una secuencia de una zona que no
        esta seria un numero asignado a algo inexistente.
        """
        evento = _evento(sesion, _proveedor(sesion))
        zona = _zona(sesion, evento)
        sesion.add(ZoneSequence(zone=zona))
        sesion.commit()

        sesion.delete(evento)
        sesion.commit()

        assert sesion.scalar(select(func.count()).select_from(Zone)) == 0
        assert sesion.scalar(select(func.count()).select_from(ZoneSequence)) == 0

    def test_borrar_un_dueno_con_evento_no_se_leva_nada(self, sesion: Session) -> None:
        """`events.owner_id` es `RESTRICT`: un dueno no se borra por debajo.

        Las cuentas se dan de baja de forma logica, asi que borrar la fila de
        `users` no deberia ocurrir; si ocurre, que falle deja el historial de
        ventas sin huerfano en lugar de publicitar entradas sin dueno.
        """
        proveedor = _proveedor(sesion)
        _zona(sesion, _evento(sesion, proveedor))
        sesion.commit()

        sesion.delete(proveedor)
        with pytest.raises(IntegrityError, match="events_owner_id_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Event)) == 1


class TestIndices:
    def test_events_esta_indexada_por_dueno_y_estado(self, engine: Engine) -> None:
        """"Mis eventos" del proveedor y el listado del administrador."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("events")}

        assert indices["ix_events_owner_id_status"]["column_names"] == ["owner_id", "status"]

    def test_events_esta_indexada_por_estado_y_fecha(self, engine: Engine) -> None:
        """El catalogo publico: solo `published`, ordenados por fecha de inicio."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("events")}

        assert indices["ix_events_status_starts_at"]["column_names"] == ["status", "starts_at"]

    def test_zonas_tienen_un_indice_unico_por_evento_y_nombre(
        self, engine: Engine
    ) -> None:
        indices = {i["name"]: i for i in inspect(engine).get_indexes("zones")}

        assert indices["uq_zones_event_nombre"]["unique"] is True

    def test_el_indice_de_zonas_compara_el_nombre_sin_mayusculas(
        self, engine: Engine
    ) -> None:
        """La expresion es lo que convierte la unicidad en "misma zona".

        Se lee de `pg_indexes` y no del modelo porque lo que importa es lo que
        ejecuto la base: un `UNIQUE (event_id, name)` en el modelo y este indice
        en la base serian dos cosas distintas.
        """
        with engine.connect() as conexion:
            definicion: str = conexion.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'uq_zones_event_nombre'"
                )
            ).scalar_one()

        # PostgreSQL imprime el cast que anade al `btrim`; se busca la parte que
        # no depende de como lo formatee.
        assert "lower(btrim((name)::text))" in definicion
        assert "UNIQUE INDEX" in definicion

    def test_el_indice_de_zonas_sirve_para_leer_las_de_un_evento(
        self, sesion: Session
    ) -> None:
        """El indice empieza por `event_id`, asi que cubre la lectura del catalogo.

        No se mide el plan -seria fragil-, sino que se comprueba que la consulta
        que se va a hacer en 4.5 se apoya en una columna que el indice tiene en
        primera posicion.
        """
        indices = {i["name"]: i for i in inspect(sesion.get_bind()).get_indexes("zones")}

        assert indices["uq_zones_event_nombre"]["column_names"][0] == "event_id"


class TestRelaciones:
    def test_un_evento_trae_sus_zonas(self, sesion: Session) -> None:
        evento = _evento(sesion, _proveedor(sesion))
        _zona(sesion, evento, nombre="General")
        _zona(sesion, evento, nombre="Tribuna")
        sesion.expire_all()

        guardado = sesion.scalar(select(Event))
        assert guardado is not None
        assert sorted(zona.name for zona in guardado.zones) == ["General", "Tribuna"]

    def test_el_dueno_de_un_evento_se_sabe_desde_la_zona(self, sesion: Session) -> None:
        """La ruta de autorizacion de 4.2 llega al evento desde la zona.

        Es la razon de que `zones.event` exista y no solo `zones.event_id`: sin la
        relacion, comprobar la propiedad de un evento obliga a una consulta de
        mas en el camino mas frecuente de la API.
        """
        proveedor = _proveedor(sesion)
        zona = _zona(sesion, _evento(sesion, proveedor))
        sesion.expire_all()

        leida = sesion.get(Zone, zona.id)
        assert leida is not None
        assert leida.event.owner_id == proveedor.id

    def test_quitar_una_zona_de_la_coleccion_la_borra(self, sesion: Session) -> None:
        """Por que 4.3 no puede borrar una zona quitandola del evento.

        `delete-orphan` convierte "dejar de associationar" en un `DELETE`. Es lo
        que se quiere al tirar el evento entero, pero es una trampa en el camino
        de 4.3: quitar la zona de `event.zones` y confirmar borraria la fila sin
        preguntar nada, y la proteccion de "una zona con entradas no se borra"
        tendria que estar en la FK de `tickets.zone_id` (5.1) en vez de en el
        dominio. El borrado de zona se hara con un metodo explicito.
        """
        evento = _evento(sesion, _proveedor(sesion))
        _zona(sesion, evento)
        sesion.expire_all()

        cargado = sesion.scalar(select(Event))
        assert cargado is not None
        cargado.zones.remove(cargado.zones[0])
        sesion.flush()

        assert sesion.scalar(select(func.count()).select_from(Zone)) == 0
        assert sesion.scalar(select(func.count()).select_from(Event)) == 1

    def test_la_zona_hereda_el_evento_al_crearla(self, sesion: Session) -> None:
        proveedor = _proveedor(sesion)
        zona = Zone(event=_evento(sesion, proveedor), name="General", capacity=1, price="1.00")
        sesion.add(zona)
        sesion.commit()

        assert zona.event.owner_id == proveedor.id


class TestEstadosDelModelo:
    def test_solo_el_borrador_es_editable(self, sesion: Session) -> None:
        proveedor = _proveedor(sesion)

        for estado in EventStatus:
            evento = _evento(
                sesion, proveedor, nombre=f"Evento {estado.value}", status=estado.value
            )
            assert evento.es_editable is (estado is EventStatus.DRAFT)

    def test_las_marcas_de_tiempo_las_pon_la_base(self, sesion: Session) -> None:
        """Las marcas de tiempo las pone la base, no Python.

        Un `default=datetime.now` en Python pondria la hora del proceso de
        aplicacion, que con varios procesos puede no coincidir con la de la base
        y desordenar un catalogo ordenado por fecha de inicio.
        """
        evento = _evento(sesion, _proveedor(sesion))
        sesion.commit()

        assert evento.created_at is not None
        assert evento.updated_at is not None
        assert evento.created_at.tzinfo is not None
        # La fecha de inicio del evento es la que pone el dominio; la de
        # creacion la pone la base, y son cosas distintas.
        assert evento.created_at != INICIO

    def test_updated_at_avanza_al_modificar(self, sesion: Session) -> None:
        evento = _evento(sesion, _proveedor(sesion))
        sesion.commit()
        antes = evento.updated_at

        evento.status = EventStatus.PUBLISHED.value
        sesion.commit()
        sesion.refresh(evento)

        assert evento.updated_at >= antes


class TestElDuenoEsUnaCuentaDeUsuarios:
    """`events.owner_id` apunta a `users`, no a una copia de sus datos.

    Es la base de la autorizacion de 4.2: si el dueno fuera un UUID suelto o una
    tabla paralela, "este evento es mio" no se podria responder mirando la misma
    fila que la sesion y la suspension. Con la referencia, suspender una cuenta
    y perder la propiedad del evento son la misma comprobacion.
    """

    def test_el_dueno_es_la_misma_cuenta_que_inicia_sesion(self, sesion: Session) -> None:
        proveedor = _proveedor(sesion)
        sesion.add(
            AuthSession(
                user=proveedor,
                refresh_token_hash="z" * 64,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        _evento(sesion, proveedor)
        sesion.commit()

        leido = sesion.scalar(select(Event))
        assert leido is not None
        assert leido.owner.email == "proveedor@ejemplo.com"
        assert len(leido.owner.sessions) == 1
