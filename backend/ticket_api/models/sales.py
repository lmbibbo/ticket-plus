"""Ventas: `orders` y `tickets`.

Cuatro decisiones de este archivo que no son obvias:

**`tickets.zone_id` es `RESTRICT` y es la regla de 4.3 hecha foreign key.** La
especificacion dice que una zona con entradas asignadas no se puede eliminar, y
una `CASCADE` lo permitiria: bastaria con borrar el evento y se iran las zonas
con las entradas que nunca se vendieron, que es exactamente el dato que no se
puede perder. Con `RESTRICT` el borrado falla en la base aunque el dominio se
equivoque, que es lo que la tarea pide cuando dice "en el dominio y mediante una
restriccion de integridad".

**`orders.idempotency_key` es unica por cliente, no global.** Dos clientes
distintos pueden usar `compra-1` el mismo dia sin que eso signifique nada: la
clave la elige el cliente y solo significa algo junto con quien la envio. La
unicidad global seria una falsa restriccion que rechazaria compras legitimas de
dos personas a la vez, que es el caso normal de una preventa.

**El hash de la solicitud va en la fila del pedido, no se recalcula.** La
comparacion de idempotencia es "misma clave y mismos datos". Guardar el hash
junto a la clave hace la comparacion una lectura de indice en vez de tener que
volver a canonicalizar la peticion -que es lo que la haria fragile ante
cualquier cambio de formato-.

**`tickets.number` no se reinicia nunca y su unicidad es por zona.** El numero
de una entrada es lo que la identifica ante el comprador, asi que repetirlo
dentro de una zona seria darle dos entradas con el mismo nombre a dos personas.
`(zone_id, number)` y no `number` a secas: cada zona numera desde 1, y el
numero 3 de la zona A no tiene nada que ver con el 3 de la zona B. El indice
unico es ademas el que va a cubrir la asignacion de 5.4.
"""

from __future__ import annotations

import enum
import re
import uuid
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ticket_api.db.base import Base
from ticket_api.models.events import Event, Zone
from ticket_api.models.identity import UUID_EN_LA_BASE, User

#: Longitudes de las columnas de texto. La clave de idempotencia la elige el
#: cliente, asi que se acota: sin tope, un cliente que envie un cuerpo de un
#: gigabyte se lo mete al indice unico de todos los pedidos.
IDEMPOTENCY_KEY_MAXIMO: int = 255
#: SHA-256 en hexadecimal de la peticion canonicalizada.
REQUEST_HASH_MAXIMO: int = 64
#: Longitud del nonce opaco de la entrada. Es un identificador, no un secreto con
#: entropia: lo que no se puede adivinar es la firma de 5.6, no el nonce.
NONCE_MAXIMO: int = 64

#: El total de un pedido es la suma de precios por cantidades y puede ser mas
#: grande que un precio suelto, asi que tiene dos digitos mas de precision.
PRECISION_TOTAL: int = 14
ESCALA_TOTAL: int = 2
TOTAL_MAXIMO: Decimal = Decimal("999999999999.99")

_ESPACIOS = re.compile(r"\s+")


class OrderStatus(enum.StrEnum):
    """Ciclo de vida de un pedido.

    `pending` es "registrado y sin activar": es el estado en el que queda un
    pedido cuyo pago se rechaza, y tambien el punto de partida de uno aprovado.
    `approved` significa que la pasarela simulada dio el visto bueno y todavia
    no se ha asignado inventario, que es el estado en el que una compra con las
    zonas ya bloqueadas y las entradas a medio crear dejaria el pedido si algo
    fallara. `sold` es el unico estado con entradas: la asignacion y el cambio a
    `sold` van en la misma transaccion, asi que `sold` sin entradas y entradas
    sin `sold` no son estados alcanzables.
    """

    PENDING = "pending"
    APPROVED = "approved"
    SOLD = "sold"
    REJECTED = "rejected"


def normalizar_clave(valor: str) -> str:
    """Deja la clave de idempotencia en la forma en la que se compara.

    Se recorta y aplana espacios, y no se pasa a minusculas: la clave la elige el
    cliente y puede ser un UUID, un hash o cualquier cadena opaca. Cambiarle el
    caso haria que `compra-1` y `Compra-1` fueran dos claves distintas, y el
    mismo reintento con la misma clave dejaria de reconocerse.
    """
    limpio = _ESPACIOS.sub(" ", valor.strip())
    if not limpio:
        raise ValueError("la clave de idempotencia no puede estar vacia")
    if len(limpio) > IDEMPOTENCY_KEY_MAXIMO:
        raise ValueError(f"la clave de idempotencia supera los {IDEMPOTENCY_KEY_MAXIMO} caracteres")
    return limpio


def _valores(enum_tipo: type[enum.StrEnum]) -> str:
    """Lista de valores para una restriccion CHECK."""
    return ", ".join(f"'{miembro.value}'" for miembro in enum_tipo)


def _decimal_exacto(valor: Decimal | int | str, maximo: Decimal, escala: int, que: str) -> Decimal:
    """Convierte a `Decimal` y comprueba la escala y el rango.

    El `CHECK` de la tabla protege de lo que no pasa por el ORM; lo que hace esto
    es que un total o un precio que no caben en la columna sea un error del
    dominio y no un error de precision de PostgreSQL, que el servidor convertiria
    en un 500 con el detalle del DSN.
    """
    if isinstance(valor, float):
        raise ValueError(
            f"el {que} se expresa con Decimal, no con float: un float arrastra "
            "error de redondeo hasta el total del pedido"
        )
    numero = Decimal(valor)
    if not numero.is_finite():
        raise ValueError(f"el {que} tiene que ser un numero finito")
    exponente = numero.as_tuple().exponent
    if isinstance(exponente, int) and exponente < -escala:
        raise ValueError(f"el {que} admite como mucho {escala} decimales")
    if numero > maximo:
        raise ValueError(f"el {que} supera el maximo de {maximo}")
    return numero


class Order(Base):
    """Un pedido de entradas de un evento, hecho por un cliente."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT: un pedido es el historico de una compra. Si se pudiera borrar
        # la cuenta y se llevara por delante los pedidos, las entradas vendidas se
        # quedarian sin compra de la que dependen.
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=OrderStatus.PENDING.value
    )
    #: La elige el cliente. Unica junto al cliente, no global: ver la nota.
    idempotency_key: Mapped[str] = mapped_column(
        String(IDEMPOTENCY_KEY_MAXIMO), nullable=False
    )
    #: SHA-256 de la peticion canonicalizada. Es lo que distingue "el mismo
    #: reintento" de "la misma clave con otros datos".
    request_hash: Mapped[str] = mapped_column(String(REQUEST_HASH_MAXIMO), nullable=False)
    total: Mapped[Decimal] = mapped_column(
        Numeric(precision=PRECISION_TOTAL, scale=ESCALA_TOTAL), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    client: Mapped[User] = relationship(foreign_keys=[client_id])
    event: Mapped[Event] = relationship()
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="order",
        # Sin `delete-orphan`: una entrada emitida no se borra porque se haya
        # cerrado mal la relacion. El numero asignado es historia.
        passive_deletes=True,
    )

    __table_args__ = (
        # La unicidad que hace posible la idempotencia sin tabla de bloqueo: dos
        # peticiones con la misma clave y a la vez, una gana y la otra recibe el
        # conflicto de la restriccion.
        UniqueConstraint("client_id", "idempotency_key", name="uq_orders_client_idempotency_key"),
        CheckConstraint(f"status IN ({_valores(OrderStatus)})", name="ck_orders_status"),
        CheckConstraint("total >= 0", name="ck_orders_total_no_negativo"),
        CheckConstraint(
            "btrim(idempotency_key) <> ''", name="ck_orders_idempotency_key_no_vacia"
        ),
        # El indice de la unicidad empieza por `client_id`, asi que "mis pedidos"
        # ya esta cubierto y no hace falta un indice mas aqui.
    )

    @validates("idempotency_key")
    def _normaliza(self, _clave: str, valor: str) -> str:
        return normalizar_clave(valor)

    @validates("total")
    def _comprueba_total(self, _clave: str, valor: Decimal | int | str) -> Decimal:
        return _decimal_exacto(valor, TOTAL_MAXIMO, ESCALA_TOTAL, "total")

    @property
    def esta_vendido(self) -> bool:
        return self.status == OrderStatus.SOLD.value

    def __repr__(self) -> str:
        return f"<Order {self.status} total={self.total}>"


class Ticket(Base):
    """Una entrada virtual. No se borra ni se renumera."""

    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT y no CASCADE: si se pudiera borrar un pedido se llevaria sus
        # entradas, y el comprador se quedaria sin la entrada que pago. Ninguna
        # de las dos se borra nunca en el dominio, asi que la restriccion protege
        # del error, no del flujo previsto.
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT: es la regla de 4.3 en la base. Una zona con entradas no se
        # puede borrar, y esta foreign key es lo que lo hace cierto aunque el
        # dominio no mire.
        ForeignKey("zones.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: Se copia del pedido en vez de deducirse por la relacion. Es lo que hace
    #: que la consulta de "mis entradas" sea un indice y no un `JOIN`, y evita
    #: que cambiar el dueno de un pedido dejara entradas huerfanas o con el dueno
    #: equivocado.
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: El numero dentro de la zona. Unico junto a la zona, nunca global.
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Valor opaco unico por entrada. Entra en el payload firmado de 5.6: dos
    #: entradas con el mismo numero en zonas distintas tienen numeros distintos
    #: de payload, porque el nonce es distinto.
    nonce: Mapped[str] = mapped_column(String(NONCE_MAXIMO), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order: Mapped[Order] = relationship(back_populates="tickets")
    zone: Mapped[Zone] = relationship()
    client: Mapped[User] = relationship(foreign_keys=[client_id])

    __table_args__ = (
        # Dos entradas con el mismo numero en la misma zona serian dos entradas
        # distintas con el mismo nombre ante el comprador. El indice cubre
        # ademas la asignacion de 5.4, que escribe por (zona, numero).
        UniqueConstraint("zone_id", "number", name="uq_tickets_zone_id_number"),
        # La entrada se numera desde 1, igual que las zonas.
        CheckConstraint("number >= 1", name="ck_tickets_number_positivo"),
        CheckConstraint("btrim(nonce) <> ''", name="ck_tickets_nonce_no_vacio"),
        # "Mis entradas" del cliente. Es el indice que dice el diseno y el que
        # usa la vista privada de 7.5.
        Index("ix_tickets_client_id", "client_id"),
        # Las entradas de un pedido, que es como se reconstruye el resultado de
        # una compra idempotente sin recorrer las del cliente.
        Index("ix_tickets_order_id", "order_id"),
    )

    def __repr__(self) -> str:
        return f"<Ticket zona={self.zone_id} numero={self.number}>"
