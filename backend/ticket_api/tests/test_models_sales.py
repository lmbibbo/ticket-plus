"""5.1 - Restricciones e indices de `orders` y `tickets`.

Comprueba comportamiento a traves de la base, no la forma del modelo: dos
pedidos con la misma clave y ver que la base lo rechaza. Un `UniqueConstraint`
retirado del esquema se detecta aqui aunque el modelo siga importandose.

Las dos unicidades que la tarea enuncia por nombre tienen su propia clase:

**`(client_id, idempotency_key)`.** Es por cliente y no global, y las dos mitades
importan. La global rechazaria dos personas comprando a la vez con `compra-1`,
que es el caso normal de una preventa; la unica por clave solaeria permitir que
un cliente anular el pedido de otro. Se comprueban las dos cosas por separado.

**`(zone_id, number)`.** Es por zona y no global. El numero de una entrada es lo
que la identifica ante el comprador, asi que repetirlo dentro de una zona es dar
dos entradas con el mismo nombre a dos personas. Fuera de la zona, el 3 de una y el
3 de otra no tienen nada que ver.

Ademas se fija `RESTRICT` en las tres claves foraneas de `tickets`: la de
`zones` es la regla de 4.3 hecha base de datos, y es lo que impide borrar una
zona con entradas aunque el dominio no mire.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticket_api.db.base import Base
from ticket_api.models import (
    Event,
    EventStatus,
    Order,
    OrderStatus,
    Role,
    Ticket,
    User,
    Zone,
    normalizar_clave,
)
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

_HASH_EJEMPLO = "$argon2id$v=19$m=65536,t=3,p=4$sal$hash"
INICIO = datetime(2027, 6, 1, 18, 0, tzinfo=UTC)
FIN = datetime(2027, 6, 1, 23, 0, tzinfo=UTC)
HASH_PETICION = "a" * 64


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Crea y destruye las tablas reales del modelo.

    Se crea el esquema de `Base` entero, no solo las de este modulo, porque las
    claves foraneas entre `orders`, `tickets`, `zones` y `events` hacen que el
    orden importe y el orden de las pruebas no es una garantia.
    """
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


def _cuenta(sesion: Session, email: str, role: str = Role.CLIENT.value) -> User:
    cuenta = User(email=email, password_hash=_HASH_EJEMPLO, role=role)
    sesion.add(cuenta)
    sesion.flush()
    return cuenta


def _evento(sesion: Session, dueno: User, status: str = EventStatus.PUBLISHED.value) -> Event:
    evento = Event(
        owner=dueno, name="Concierto de prueba", starts_at=INICIO, ends_at=FIN, status=status
    )
    sesion.add(evento)
    sesion.flush()
    return evento


def _zona(sesion: Session, evento: Event, nombre: str = "General", capacidad: int = 100) -> Zone:
    zona = Zone(event=evento, name=nombre, capacity=capacidad, price="25.00")
    sesion.add(zona)
    sesion.flush()
    return zona


def _pedido(
    sesion: Session,
    cliente: User,
    evento: Event,
    clave: str = "compra-1",
    status: str = OrderStatus.PENDING.value,
    total: Decimal | str = "50.00",
) -> Order:
    pedido = Order(
        client=cliente,
        event=evento,
        status=status,
        idempotency_key=clave,
        request_hash=HASH_PETICION,
        total=total,
    )
    sesion.add(pedido)
    sesion.flush()
    return pedido


def _entrada(
    sesion: Session,
    pedido: Order,
    zona: Zone,
    numero: int = 1,
    nonce: str | None = None,
) -> Ticket:
    entrada = Ticket(
        order=pedido,
        zone=zona,
        client_id=pedido.client_id,
        number=numero,
        nonce=nonce or f"nonce-{zona.id}-{numero}",
    )
    sesion.add(entrada)
    sesion.flush()
    return entrada


class TestIdempotenciaPorCliente:
    def test_no_admite_dos_pedidos_con_la_misma_clave_del_mismo_cliente(
        self, sesion: Session
    ) -> None:
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        _pedido(sesion, cliente, evento, clave="compra-1")

        sesion.add(
            Order(
                client=cliente,
                event=evento,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash="b" * 64,
                total="10.00",
            )
        )
        with pytest.raises(IntegrityError, match="uq_orders_client_idempotency_key"):
            sesion.flush()
        sesion.rollback()

    def test_dos_clientes_pueden_usar_la_misma_clave(self, sesion: Session) -> None:
        """La clave la elige el cliente, y solo significa algo con quien la envio.

        Una unicidad global rechazaria dos personas comprando a la vez con
        `compra-1`, que es exactamente lo que pasa en una preventa popular.
        """
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        ana = _cuenta(sesion, "ana@ejemplo.com")
        luis = _cuenta(sesion, "luis@ejemplo.com")

        _pedido(sesion, ana, evento, clave="compra-1")
        _pedido(sesion, luis, evento, clave="compra-1")

        assert sesion.scalar(select(func.count()).select_from(Order)) == 2

    def test_un_cliente_puede_tener_tantos_pedidos_como_quisiera(self, sesion: Session) -> None:
        """La unicidad no es un tope de compras: es la de la clave repetida."""
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        cliente = _cuenta(sesion, "ana@ejemplo.com")

        for numero in range(5):
            _pedido(sesion, cliente, evento, clave=f"compra-{numero}")

        assert sesion.scalar(select(func.count()).select_from(Order)) == 5

    def test_la_clave_se_recorta_pero_no_se_pasa_a_minusculas(self, sesion: Session) -> None:
        """Lo que se envio es lo que se guarda, hasta en el caso.

        La clave es opaca y la elige el cliente, asi que se recorta pero no se
        normaliza: `compra-1` y `Compra-1` son dos claves distintas y dan dos
        pedidos. Es el criterio "lo que mandas es lo que se guarda", que hace que
        la comparacion de idempotencia no dependa de una regla de normalizacion
        que nadie escribio. El precio de esa eleccion lo paga un reintento mal
        escrito, que compra dos veces, y lo evita un cliente que use de verdad dos
        claves parecidas.
        """
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))

        _pedido(sesion, cliente, evento, clave="  Compra-1  ")

        guardado = sesion.scalar(select(Order))
        assert guardado is not None
        assert guardado.idempotency_key == "Compra-1"

        _pedido(sesion, cliente, evento, clave="compra-1")

        assert sesion.scalar(select(func.count()).select_from(Order)) == 2

    def test_una_clave_solo_con_espacios_no_sirve(self, sesion: Session) -> None:
        """Una clave vacia haria que todas las compras "sin clave" colisionaran."""
        with pytest.raises(ValueError, match="vacia"):
            Order(
                client=User(email="x@ejemplo.com", password_hash=_HASH_EJEMPLO),
                event=None,
                status=OrderStatus.PENDING.value,
                idempotency_key="   ",
                request_hash=HASH_PETICION,
                total="0.00",
            )

    def test_una_clave_desbordada_no_cabe_en_la_columna(self, sesion: Session) -> None:
        """Sin tope, un cuerpo enorme del cliente se mete en el indice de todos
        los pedidos del sistema."""
        with pytest.raises(ValueError, match="supera"):
            normalizar_clave("c" * 256)

    def test_la_base_rechaza_una_clave_vacia_por_sql(self, sesion: Session) -> None:
        """El `CHECK` protege tambien de lo que no pasa por el modelo."""
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        sesion.commit()

        with pytest.raises(IntegrityError, match="ck_orders_idempotency_key_no_vacia"):
            sesion.execute(
                text(
                    "INSERT INTO orders (client_id, event_id, status, idempotency_key, "
                    "request_hash, total) VALUES (:cliente, :evento, 'pending', '  ', :hash, 0)"
                ),
                {"cliente": cliente.id, "evento": evento.id, "hash": HASH_PETICION},
            )
        sesion.rollback()


class TestRestriccionesDelPedido:
    @pytest.mark.parametrize("estado", ["pagado", "Sold", "", "cancelado"])
    def test_rechaza_un_estado_fuera_de_la_lista(self, sesion: Session, estado: str) -> None:
        sesion.add(
            Order(
                client=_cuenta(sesion, "ana@ejemplo.com"),
                event=_evento(
                    sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
                ),
                status=estado,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total="50.00",
            )
        )
        with pytest.raises(IntegrityError, match="ck_orders_status"):
            sesion.flush()
        sesion.rollback()

    def test_acepta_los_cuatro_estados(self, sesion: Session) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        cliente = _cuenta(sesion, "ana@ejemplo.com")

        for indice, estado in enumerate(OrderStatus):
            _pedido(sesion, cliente, evento, clave=f"compra-{indice}", status=estado.value)

        assert sesion.scalar(select(func.count()).select_from(Order)) == len(OrderStatus)

    def test_un_pedido_nace_pendiente(self, sesion: Session) -> None:
        """El estado por defecto lo pone el dominio; la base solo limita los valores.

        La separacion es deliberada: los valores validos son una regla de la
        tabla -y por eso hay `CHECK`-, pero el estado inicial es una regla del
        ciclo de vida, y vive en el modelo. Un pedido nasce `pending`, que es
        justo el estado en el que un pago rechazado tiene que quedarse: nunca
        `sold`.
        """
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))

        pedido = Order(
            client=cliente,
            event=evento,
            idempotency_key="compra-1",
            request_hash=HASH_PETICION,
            total="50.00",
        )
        sesion.add(pedido)
        sesion.commit()

        assert pedido.status == OrderStatus.PENDING.value
        assert not pedido.esta_vendido

    def test_rechaza_un_total_negativo(self, sesion: Session) -> None:
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))

        sesion.add(
            Order(
                client=cliente,
                event=evento,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total="-0.01",
            )
        )
        with pytest.raises(IntegrityError, match="ck_orders_total_no_negativo"):
            sesion.flush()
        sesion.rollback()

    def test_un_total_de_cero_son_valido(self, sesion: Session) -> None:
        """Un pedido de cero euros es legitimo: hay zonas gratuitas."""
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))

        pedido = _pedido(sesion, cliente, evento, total="0.00")

        assert pedido.total == Decimal("0.00")

    @pytest.mark.parametrize("total", ["10.999", "0.001"])
    def test_rechaza_un_total_con_mas_decimals_de_los_que_admite(self, total: str) -> None:
        """Sin esta comprobacion, `NUMERIC(14, 2)` cortaria a 10.99 en silencio
        y el comprador pagaria una cantidad distinta de la que se le enseño."""
        with pytest.raises(ValueError, match="decimales"):
            Order(
                client=User(email="x@ejemplo.com", password_hash=_HASH_EJEMPLO),
                event=None,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total=total,
            )

    def test_rechaza_un_total_que_no_cabe_en_la_columna(self) -> None:
        with pytest.raises(ValueError, match="maximo"):
            Order(
                client=User(email="x@ejemplo.com", password_hash=_HASH_EJEMPLO),
                event=None,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total="1234567890123.45",
            )

    def test_rechaza_un_total_con_coma_flotante(self) -> None:
        with pytest.raises(ValueError, match="Decimal"):
            Order(
                client=User(email="x@ejemplo.com", password_hash=_HASH_EJEMPLO),
                event=None,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total=0.1,
            )

    def test_el_total_sobrevive_ida_y_vuelta_sin_deriva(self, sesion: Session) -> None:
        """El motivo de `NUMERIC` y no `double precision`, con la suma de verdad.

        El total de un pedido es la suma de precios por cantidades. Con coma
        flotante, 0.1 + 0.2 daria 0.30000000000000004 y habria que redondear, que
        es donde una plataforma de entradas deja de cuadrar.
        """
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        pedido = _pedido(sesion, cliente, evento, total="0.10")
        sesion.commit()
        sesion.expire_all()

        leido = sesion.get(Order, pedido.id)
        assert leido is not None
        assert leido.total == Decimal("0.10")
        assert leido.total * 3 == Decimal("0.30")
        assert leido.total * 3 + Decimal("0.20") == Decimal("0.50")

    def test_un_pedido_necesita_cliente_y_evento(self, sesion: Session) -> None:
        evento = _evento(sesion, _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value))
        sesion.add(
            Order(
                client_id=uuid.uuid4(),
                event=evento,
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-1",
                request_hash=HASH_PETICION,
                total="50.00",
            )
        )
        with pytest.raises(IntegrityError, match="orders_client_id_fkey"):
            sesion.flush()
        sesion.rollback()

        sesion.add(
            Order(
                client=_cuenta(sesion, "ana@ejemplo.com"),
                event_id=uuid.uuid4(),
                status=OrderStatus.PENDING.value,
                idempotency_key="compra-2",
                request_hash=HASH_PETICION,
                total="50.00",
            )
        )
        with pytest.raises(IntegrityError, match="orders_event_id_fkey"):
            sesion.flush()
        sesion.rollback()

    def test_borrar_el_cliente_no_se_lleva_sus_pedidos(self, sesion: Session) -> None:
        """`orders.client_id` es `RESTRICT`.

        Las cuentas se dan de baja de forma logica, asi que esta fila no deberia
        caer nunca; si cae, que falle deja el historico de compras entero.
        """
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        _pedido(sesion, cliente, _evento(sesion, proveedor))
        sesion.commit()

        sesion.delete(cliente)
        with pytest.raises(IntegrityError, match="orders_client_id_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Order)) == 1


class TestNumeracionDeEntradas:
    def test_no_admite_dos_entradas_con_el_mismo_numero_en_la_misma_zona(
        self, sesion: Session
    ) -> None:
        """El numero de una entrada es lo que la identifica ante el comprador.

        Repetirlo dentro de una zona es dar dos entradas con el mismo nombre a dos
        personas, que es el fallo que este indice unico existe para evitar.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)
        _entrada(sesion, pedido, zona, numero=1, nonce="n-1")

        sesion.add(
            Ticket(
                order=pedido,
                zone=zona,
                client_id=pedido.client_id,
                number=1,
                nonce="n-2",
            )
        )
        with pytest.raises(IntegrityError, match="uq_tickets_zone_id_number"):
            sesion.flush()
        sesion.rollback()

    def test_el_mismo_numero_en_zonas_distintas_son_entradas_distintas(
        self, sesion: Session
    ) -> None:
        """Cada zona numera desde 1: el 3 de una y el 3 de otra no chocan.

        Con un unico global, el primer pedido que tocara dos zonas fallaria, y el
        tendria el aspecto de un problema de inventario.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        general = _zona(sesion, evento, nombre="General")
        tribuna = _zona(sesion, evento, nombre="Tribuna")
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        pedido = _pedido(sesion, cliente, evento)

        _entrada(sesion, pedido, general, numero=3, nonce="n-general")
        _entrada(sesion, pedido, tribuna, numero=3, nonce="n-tribuna")

        assert sesion.scalar(select(func.count()).select_from(Ticket)) == 2

    def test_las_entradas_siguen_numerandose_despues_de_otro_pedido(
        self, sesion: Session
    ) -> None:
        """La numeracion no se reinicia porque el contador se borre.

        Con `MAX(number) + 1` en vez de `zone_sequences`, borrar el ultimo
        reiniciaria la cuenta y volveria a entregar numeros ya vendidos. Aqui se
        fija que los numeros 1 y 2 bloqueados siguen bloqueados aunque se inseran
        numeros mayores en medio.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        primero = _pedido(sesion, cliente, evento, clave="compra-1")
        segundo = _pedido(sesion, cliente, evento, clave="compra-2")

        _entrada(sesion, primero, zona, numero=1, nonce="n-1")
        _entrada(sesion, segundo, zona, numero=2, nonce="n-2")
        _entrada(sesion, segundo, zona, numero=3, nonce="n-3")

        with pytest.raises(IntegrityError, match="uq_tickets_zone_id_number"):
            sesion.execute(
                text("INSERT INTO tickets (order_id, zone_id, client_id, number, nonce) "
                     "VALUES (:pedido, :zona, :cliente, 1, 'n-copia')"),
                {"pedido": segundo.id, "zona": zona.id, "cliente": cliente.id},
            )
        sesion.rollback()

    @pytest.mark.parametrize("numero", [0, -1])
    def test_rechaza_un_numero_que_no_puede_existir(
        self, sesion: Session, numero: int
    ) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)

        sesion.add(
            Ticket(
                order=pedido,
                zone=zona,
                client_id=pedido.client_id,
                number=numero,
                nonce="n",
            )
        )
        with pytest.raises(IntegrityError, match="ck_tickets_number_positivo"):
            sesion.flush()
        sesion.rollback()

    def test_un_nonce_vacio_no_sirve(self, sesion: Session) -> None:
        """El nonce entra en el payload firmado: uno vacio seria un payload con
        la parte facil de adivinar siempre igual."""
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)

        sesion.add(
            Ticket(
                order=pedido,
                zone=zona,
                client_id=pedido.client_id,
                number=1,
                nonce="   ",
            )
        )
        with pytest.raises(IntegrityError, match="ck_tickets_nonce_no_vacio"):
            sesion.flush()
        sesion.rollback()

    def test_una_entrada_no_puede_vivir_sin_pedido_zona_o_cliente(
        self, sesion: Session
    ) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)

        sesion.add(
            Ticket(
                order_id=uuid.uuid4(),
                zone=zona,
                client_id=pedido.client_id,
                number=1,
                nonce="n",
            )
        )
        with pytest.raises(IntegrityError, match="tickets_order_id_fkey"):
            sesion.flush()
        sesion.rollback()

        sesion.add(
            Ticket(
                order=pedido,
                zone_id=uuid.uuid4(),
                client_id=pedido.client_id,
                number=1,
                nonce="n",
            )
        )
        with pytest.raises(IntegrityError, match="tickets_zone_id_fkey"):
            sesion.flush()
        sesion.rollback()

        sesion.add(
            Ticket(
                order=pedido,
                zone=zona,
                client_id=uuid.uuid4(),
                number=1,
                nonce="n",
            )
        )
        with pytest.raises(IntegrityError, match="tickets_client_id_fkey"):
            sesion.flush()
        sesion.rollback()


class TestUnaZonaConEntradasNoSeBorra:
    """La regla de 4.3, en la parte que depende de que `tickets` exista.

    El dominio lo rechaza con un mensaje propio, pero lo que se comprueba aqui es
    la otra mitad: que la base tambien lo rechaza. Con `CASCADE` en
    `tickets.zone_id` bastaria con borrar el evento para arrastrarse las zonas con
    las entradas, y el dominio no tendria nada que proteger.
    """

    def test_borrar_una_zona_con_entradas_falla_en_la_base(self, sesion: Session) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)
        _entrada(sesion, pedido, zona, numero=1)
        sesion.commit()

        sesion.delete(zona)
        with pytest.raises(IntegrityError, match="tickets_zone_id_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Ticket)) == 1

    def test_borrar_el_evento_no_arrastra_las_zonas_con_entradas(
        self, sesion: Session
    ) -> None:
        """La cascada evento -> zona se detiene en la entrada.

        Es el mismo caso por el otro lado, y es el que importa: si
        `tickets.zone_id` fuera `CASCADE`, borrar un evento -o una cascade desde
        `events` - se llevaria las entradas y con ellas la prueba de que se
        vendió algo.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)
        _entrada(sesion, pedido, zona, numero=1)
        sesion.commit()

        sesion.delete(evento)
        # La constraint que salta primero la elige el orden de borrado del ORM -
        # aqui la del pedido- y no es lo que se quiere comprobar. Lo que importa
        # es que el borrado falle y que no se pierda nada.
        with pytest.raises(IntegrityError, match="_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Zone)) == 1
        assert sesion.scalar(select(func.count()).select_from(Order)) == 1
        assert sesion.scalar(select(func.count()).select_from(Ticket)) == 1

    def test_una_zona_sin_entradas_si_se_puede_borrar(self, sesion: Session) -> None:
        """La restriccion molesta lo justo: no bloquea la gestion de zonas vacias.

        Si `RESTRICT` estuviera tambien en algun otro sitio -por ejemplo si la
        zona tuviera una secuencia- la promesa de "una zona con entradas no se
        borra" se habria convertido en "ninguna zona se borra nunca", y 4.3 se
        quedaria sin forma de limpiar un evento mal publicado.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        _zona(sesion, evento, nombre="General")
        vacia = _zona(sesion, evento, nombre="Sin vender")
        sesion.commit()

        sesion.delete(vacia)
        sesion.commit()

        assert [z.name for z in sesion.scalars(select(Zone))] == ["General"]

    def test_borrar_el_cliente_tampoco_se_lleva_las_entradas(
        self, sesion: Session
    ) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        pedido = _pedido(sesion, cliente, evento)
        _entrada(sesion, pedido, zona, numero=1)
        sesion.commit()

        sesion.delete(cliente)
        with pytest.raises(IntegrityError, match="_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Ticket)) == 1

    def test_borrar_el_pedido_tampoco_se_lleva_sus_entradas(
        self, sesion: Session
    ) -> None:
        """Una entrada emitida no se borra porque alguien cierre mal una relacion.

        El comprador se quedaria sin la entrada que pago, y el numero de la zona
        se reutilizaria sin querer.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)
        _entrada(sesion, pedido, zona, numero=1)
        sesion.commit()

        sesion.delete(pedido)
        with pytest.raises(IntegrityError, match="tickets_order_id_fkey"):
            sesion.flush()
        sesion.rollback()

        assert sesion.scalar(select(func.count()).select_from(Ticket)) == 1


class TestIndice:
    def test_entradas_esta_indexada_por_cliente(self, engine: Engine) -> None:
        """"Mis entradas" del cliente. Es la consulta de la vista privada."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("tickets")}

        assert indices["ix_tickets_client_id"]["column_names"] == ["client_id"]

    def test_entradas_esta_indexada_por_pedido(self, engine: Engine) -> None:
        """Las entradas de un pedido, para reconstruir el resultado de una compra
        idempotente sin recorrer las del cliente."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("tickets")}

        assert indices["ix_tickets_order_id"]["column_names"] == ["order_id"]

    def test_el_indice_de_numeracion_es_unico_por_zona(self, engine: Engine) -> None:
        """Se comprueba en la base, no en el modelo: lo que importa es lo que
        ejecuto la migracion."""
        unicas = {
            uq["name"]: uq["column_names"]
            for uq in inspect(engine).get_unique_constraints("tickets")
        }

        assert unicas["uq_tickets_zone_id_number"] == ["zone_id", "number"]

    def test_la_unica_de_pedidos_empieza_por_el_cliente(self, engine: Engine) -> None:
        """Que empiece por `client_id` es lo que cubre "mis pedidos" sin un indice
        aparte, y lo que hace que la restriccion sea por cliente."""
        unicas = {
            uq["name"]: uq["column_names"]
            for uq in inspect(engine).get_unique_constraints("orders")
        }

        assert unicas["uq_orders_client_idempotency_key"] == ["client_id", "idempotency_key"]


class TestRelaciones:
    def test_un_pedido_trae_sus_entradas_con_zona_y_comprador(self, sesion: Session) -> None:
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        pedido = _pedido(sesion, cliente, evento)
        _entrada(sesion, pedido, zona, numero=1, nonce="n-1")
        sesion.expire_all()

        cargado = sesion.scalar(select(Order))
        assert cargado is not None
        assert len(cargado.tickets) == 1
        assert cargado.tickets[0].zone.name == "General"
        assert cargado.tickets[0].client.email == "ana@ejemplo.com"
        assert cargado.tickets[0].nonce == "n-1"

    def test_el_cliente_de_una_entrada_es_el_del_pedido(self, sesion: Session) -> None:
        """La columna esta en `tickets` a proposito.

        Es lo que hace que "mis entradas" sea un indice y no un `JOIN`, y evita
        que cambiar el dueno de un pedido -que no deberia pasar- dejara entradas
        con el dueno equivocado. Aqui se comprueba que las dos rutas dan lo
        mismo.
        """
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        zona = _zona(sesion, evento)
        cliente = _cuenta(sesion, "ana@ejemplo.com")
        pedido = _pedido(sesion, cliente, evento)
        entrada = _entrada(sesion, pedido, zona, numero=1)
        sesion.expire_all()

        leida = sesion.scalar(select(Ticket))
        assert leida is not None
        assert leida.client_id == pedido.client_id == cliente.id
        assert leida.order.client_id == cliente.id
        assert entrada.id == leida.id

    def test_el_nonce_es_distinto_por_entrada(self, sesion: Session) -> None:
        """El nonce es lo que hace que dos entradas con el mismo numero en zonas
        distintas tengan payloads distintos en 5.6."""
        proveedor = _cuenta(sesion, "prov@ejemplo.com", Role.EVENT_OWNER.value)
        evento = _evento(sesion, proveedor)
        general = _zona(sesion, evento, nombre="General")
        tribuna = _zona(sesion, evento, nombre="Tribuna")
        pedido = _pedido(sesion, _cuenta(sesion, "ana@ejemplo.com"), evento)

        una = _entrada(sesion, pedido, general, numero=1, nonce="n-general")
        otra = _entrada(sesion, pedido, tribuna, numero=1, nonce="n-tribuna")

        assert una.nonce != otra.nonce
        assert len({una.nonce, otra.nonce}) == 2
