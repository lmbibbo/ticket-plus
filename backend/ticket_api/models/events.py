"""Eventos: `events`, `zones` y `zone_sequences`.

Cuatro decisiones de este archivo que no son obvias:

**El nombre de zona se compara sin distinguir mayusculas, pero se guarda como
el proveedor lo escribio.** Un `UNIQUE (event_id, name)` literal deja pasar
`General` y `general`, que son la misma zona con dos nombres y una capacidad
distinta cada una: el catalogo mostraria dos zonas y la disponibilidad se
repartiria entre las dos. La unicidad va en un indice unico sobre
`lower(btrim(name))`, asi que la colision se detecta y el nombre sale intacto en
la pantalla. No se normaliza a minusculas la columna porque `VIP` se debe ver
como `VIP`.

**`zone_sequences.zone_id` es la clave primaria, no una clave foranea con
restriccion unica.** Las dos cosas dicen "una fila por zona", pero con la PK la
invariante no se puede ni expresar mal: no existe un estado de la base en el que
una zona tenga dos secuencias. Una `UNIQUE` dejaria que alguien inserte la
segunda fila y solo fallaria al asignar numeros, con pedidos a medias. La fila se
crea en la primera compra (5.4), no aqui: una zona sin secuencia no esta rota,
simplemente no ha vendido.

**`events.owner_id` es `RESTRICT` y no `CASCADE`.** Una cuenta se da de baja de
forma logica (`users.status = deleted`), asi que el borrado fisico no deberia
darse, pero si se diera, cascadear dejaria sin dueño a unos eventos con entradas
vendidas. Que el borrado falle es preferible a que el historico de compras quede
huerfano.

**La capacidad es un entero y el precio un `NUMERIC(12, 2)`.** Capacidad cuenta
entradas, no admite fracciones. El precio es dinero: con coma flotante,
`0.1 + 0.2` da `0.30000000000000004` y un total de pedido acumulado arrastra el
error hasta el redondeo. `NUMERIC` en PostgreSQL es un decimal exacto, y la
multiplicacion por cantidad de la compra es exacta.
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
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ticket_api.db.base import Base
from ticket_api.models.identity import UUID_EN_LA_BASE, User

#: Longitudes de los nombres. El de evento es generoso porque es el titulo que se
#: ve en el catalogo; el de zona, corto porque es una etiqueta de una lista.
EVENT_NAME_MAXIMO: int = 200
ZONE_NAME_MAXIMO: int = 80

#: Escala del precio: dos decimales, que es la moneda del sistema.
PRECISION_PRECIO: int = 12
ESCALA_PRECIO: int = 2

#: Precio maximo que cabe con dos decimales en 12 digitos de precision.
PRECIO_MAXIMO: Decimal = Decimal("9999999999.99")

#: Cantos de nombre que se convierten en un solo espacio. `btrim` de PostgreSQL
#: quita los espacios de los extremos pero no aplana los del medio, asi que sin
#: esto `General ` y `General  ` -con dos espacios- colarian como nombres
#: distintos en el indice unico.
_ESPACIOS = re.compile(r"\s+")


class EventStatus(enum.StrEnum):
    """Ciclo de vida del evento.

    `retired` no es `draft` al reves: un evento retirado no vuelve al catalogo.
    Volverlo a publicar exigiria volver a validarlo, y quien lo hacia deberia
    poder hacerlo desde `draft`, no desde un estado que ya estuvo en el catalogo
    con entradas vendidas.
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


def normalizar_nombre(valor: str, maximo: int, que: str) -> str:
    """Deja un nombre comparable y presentable.

    Recorta y aplana los espacios, pero conserva el texto tal cual. La
    comparacion sin mayusculas la hace el indice de la base, no esta funcion: si
    el nombre se guardara en minusculas, `VIP` se mostraria como `vip` en todas
    las pantallas.
    """
    limpio = _ESPACIOS.sub(" ", valor.strip())
    if not limpio:
        raise ValueError(f"el {que} no puede estar vacio")
    if len(limpio) > maximo:
        raise ValueError(f"el {que} supera los {maximo} caracteres")
    return limpio


def _valores(enum_tipo: type[enum.StrEnum]) -> str:
    """Lista de valores para una restriccion CHECK."""
    return ", ".join(f"'{miembro.value}'" for miembro in enum_tipo)


class Event(Base):
    """Un evento. El propietario es quien puede gestionarlo; un admin tambien."""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT explicito: ver la nota del modulo.
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(EVENT_NAME_MAXIMO), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=EventStatus.DRAFT.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship()
    zones: Mapped[list[Zone]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_valores(EventStatus)})", name="ck_events_status"),
        # Un evento que termina antes de empezar no se puede publicar ni leer.
        CheckConstraint("ends_at > starts_at", name="ck_events_ventana_horaria"),
        # El nombre no puede ser solo espacios: saldría un evento sin título en el
        # catálogo, y `btrim` en la base es lo que protege también de lo que
        # entre por SQL directo.
        CheckConstraint("btrim(name) <> ''", name="ck_events_name_no_vacio"),
        # La vista de "mis eventos" del proveedor y la del administrador.
        Index("ix_events_owner_id_status", "owner_id", "status"),
        # El catálogo publico: solo `published`, ordenado por fecha de inicio.
        Index("ix_events_status_starts_at", "status", "starts_at"),
    )

    @validates("name")
    def _normaliza(self, _clave: str, valor: str) -> str:
        return normalizar_nombre(valor, EVENT_NAME_MAXIMO, "nombre del evento")

    @property
    def es_editable(self) -> bool:
        """Solo un borrador se edita. Un evento publicado o retirado no."""
        return self.status == EventStatus.DRAFT.value

    def __repr__(self) -> str:
        return f"<Event {self.status} {self.name!r}>"


class Zone(Base):
    """Una zona de un evento, con su capacidad y su precio."""

    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        # CASCADE: una zona sin evento no significa nada. Y no se cuela por
        # aqui un evento con entradas, porque el borrado de zonas con inventario
        # se rechaza en 4.3 y `tickets.zone_id` sera RESTRICT en 5.1.
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(ZONE_NAME_MAXIMO), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Decimal exacto. Ver la nota del modulo sobre por que no es `float`.
    price: Mapped[Decimal] = mapped_column(
        Numeric(precision=PRECISION_PRECIO, scale=ESCALA_PRECIO), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    event: Mapped[Event] = relationship(back_populates="zones")

    # No hay relacion inversa `Zone.sequence`, y es deliberado. Un 1:1 con la
    # clave del hijo como clave primaria complica el borrado en cascada y
    # es fragile ante cambios de esquema, y lo unico que se haria con ella es
    # releer un contador. Ademas invita a reasignar `zone.sequence`, y eso si
    # que seria peligroso: `delete-orphan` borraria la fila y la zona volveria a
    # numerar desde 1, repitiendo numeros que ya tienen entrada. El contador se
    # lee con una consulta por `zone_id`, que es lo que hara 5.4 con su bloqueo.

    __table_args__ = (
        # "Una zona no puede tener capacidad negativa" y "el precio no puede ser
        # negativo": los dos son la regla de 4.3, y esta es la mitad que no
        # depende de que la API este delante. Cero si es valido -una zona cerrada
        # a la venta se representa asi- y un valor negativo no lo es nunca.
        CheckConstraint("capacity >= 0", name="ck_zones_capacity_no_negativa"),
        CheckConstraint("price >= 0", name="ck_zones_precio_no_negativo"),
        CheckConstraint("btrim(name) <> ''", name="ck_zones_name_no_vacio"),
        # La unicidad que la tarea enuncia: un evento no puede repetir zona.
        # Expresion, y no `(event_id, name)`, para que `General` y `general`
        # colisionen. Su indice sirve ademas para `WHERE event_id = ?`, que es
        # como se leen las zonas de un evento, asi que no hace falta un indice
        # mas sobre `event_id`.
        Index(
            "uq_zones_event_nombre",
            "event_id",
            text("lower(btrim(name))"),
            unique=True,
        ),
    )

    @validates("name")
    def _normaliza(self, _clave: str, valor: str) -> str:
        return normalizar_nombre(valor, ZONE_NAME_MAXIMO, "nombre de la zona")

    @validates("price")
    def _comprueba_precio(self, _clave: str, valor: Decimal | int | str) -> Decimal:
        """Deja el precio en `Decimal` y comprueba lo que el `CHECK` no comprueba.

        El `CHECK` de la tabla dice que el precio no es negativo, y es lo que
        protege de un `INSERT` que no pase por el ORM. Lo que no dice, y aqui se
        comprueba, es que quepan en `NUMERIC(12, 2)`: sin esto, un precio de
        `12345678901.5` llega a la base y sale como un error de precision -un 500
        con el detalle del DSN- en lugar de un error de validacion del dominio.
        """
        if isinstance(valor, float):
            raise ValueError(
                "el precio se expresa con Decimal, no con float: un float arrastra "
                "error de redondeo hasta el total del pedido"
            )
        precio = Decimal(valor)
        if not precio.is_finite():
            raise ValueError("el precio tiene que ser un numero finito")
        # `as_tuple().exponent` es un entero para los numeros normales y una
        # letra para `NaN` e infinitos, que el `is_finite` de arriba ya ha
        # descartado. Se comprueba el tipo por si el tipo de la excepcion cambia.
        exponente = precio.as_tuple().exponent
        if isinstance(exponente, int) and exponente < -ESCALA_PRECIO:
            raise ValueError(f"el precio admite como mucho {ESCALA_PRECIO} decimales")
        if precio > PRECIO_MAXIMO:
            raise ValueError(f"el precio supera el maximo de {PRECIO_MAXIMO}")
        return precio

    def __repr__(self) -> str:
        return f"<Zone {self.name!r} capacidad={self.capacity} precio={self.price}>"


class ZoneSequence(Base):
    """El proximo numero libre de una zona. Una fila por zona, y solo una.

    `next_number` es el siguiente a asignar, no el ultimo usado: la primera fila
    vale 1, que es el primer numero que recibe la primera compra. La fila no se
    crea al crear la zona, sino en la primera venta (5.4), porque hasta entonces
    no hay nada que numerar.
    """

    __tablename__ = "zone_sequences"

    #: La zona ES la clave: "una zona tiene una sola secuencia" queda impuesto por
    #: la estructura de la tabla y no por una restriccion que se pueda saltar.
    zone_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE"),
        primary_key=True,
    )
    next_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Sin `back_populates`: `Zone` no declara relacion inversa, y `back_populates`
    # apuntando a algo que no existe falla al configurar los mapeadores -con un
    # `KeyError` en mitad de una peticion, no al importar.
    zone: Mapped[Zone] = relationship()

    __table_args__ = (
        # Los numeros empiezan en 1, segun la especificacion de entradas. Un 0 o
        # un negativo aqui seria un numero de entrada que no existe.
        CheckConstraint("next_number >= 1", name="ck_zone_sequences_next_number"),
    )

    def __repr__(self) -> str:
        return f"<ZoneSequence siguiente={self.next_number}>"
