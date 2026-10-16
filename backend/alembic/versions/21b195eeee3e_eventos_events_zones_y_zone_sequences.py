"""eventos: `events`, `zones` y `zone_sequences`

Revision ID: 21b195eeee3e
Revises: e34227427793
Create Date: 2026-09-29 12:39:28.855623

Se escribio con `autogenerate` y se reviso a mano contra
`ticket_api/models/events.py`. Cuatro cosas que conviene no "corregir" despues:

**La unicidad de zona es un indice sobre una expresion, no un `UNIQUE` de
columnas.** `uq_zones_event_nombre` indexa `(event_id, lower(btrim(name)))` y es
unico. Con `(event_id, name)` a secas, `General` y `general` seriam dos zonas
distintas con capacidades distintas, y el catalogo mostraria las dos. El nombre
se guarda con su grafia -`VIP` se ve como `VIP`- y la comparacion la hace la
expresion. El mismo `btrim` no aplana los espacios del medio, de ahi que el
`validates` del modelo los aplane antes de escribir: `General ` y `General  `
colarian como nombres distintos en este indice.

**`zone_sequences.zone_id` es la clave primaria.** Es lo que hace que "una zona
tiene una sola secuencia" no sea una restriccion que se pueda saltarse sino una
forma de la tabla: no existe estado de la base en el que haya dos secuencias
para la misma zona. Con una `UNIQUE` sobre una columna suelta, la segunda fila
entaria y solo fallaria al numerar, con un pedido a medias.

**`events.owner_id` es `RESTRICT` y `zones.event_id` es `CASCADE`.** No es
asimetro sin motivo. Una cuenta se da de baja de forma logica
(`users.status = deleted`), asi que borrar la fila de `users` no deberia
suceder nunca; si sucede, que falle es preferible a dejar eventos con entradas
vendidas sin dueno. En cambio, una zona sin evento no significa nada, y
borrar el evento se lleva sus zonas. El borrado de una zona con inventario se
rechaza en el dominio (4.3) y `tickets.zone_id` sera `RESTRICT` en 5.1, asi que
esta cascada no puede borrar entradas.

**`price` es `NUMERIC(12, 2)` y no `double precision`.** Un total de pedido es
una suma de productos de precios por cantidades; con coma flotante, 0.1 + 0.2
da 0.30000000000000004 y el error viaja hasta el redondeo final. `NUMERIC` es
decimal exacto. La comprobacion de que el precio cabe en 12 digitos y de que no
tiene mas de dos decimales esta en el `@validates` del modelo, no aqui: un
precio que no cabe debe ser un error de validacion del dominio y no un error de
precision de la base.

`downgrade` borra en orden inverso al de `upgrade` porque las claves foraneas lo
exigen: `zone_sequences` apunta a `zones` y `zones` a `events`.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "21b195eeee3e"
down_revision: str | None = "e34227427793"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(f"status IN ('draft', 'published', 'retired')", name="ck_events_status"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_events_ventana_horaria"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_events_name_no_vacio"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # "Mis eventos" del proveedor y listado del administrador.
    op.create_index("ix_events_owner_id_status", "events", ["owner_id", "status"], unique=False)
    # Catalogo publico: solo `published`, ordenado por fecha de inicio.
    op.create_index("ix_events_status_starts_at", "events", ["status", "starts_at"], unique=False)

    op.create_table(
        "zones",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("capacity >= 0", name="ck_zones_capacity_no_negativa"),
        sa.CheckConstraint("price >= 0", name="ck_zones_precio_no_negativo"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_zones_name_no_vacio"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Un evento no puede repetir zona. El indice es unico y su expresion es lo que
    # hace que `General` y `general` sean la misma zona. Ademas cubre
    # `WHERE event_id = ?`, que es como se leen las zonas de un evento, asi que no
    # hace falta un indice mas sobre `event_id`.
    op.create_index(
        "uq_zones_event_nombre",
        "zones",
        ["event_id", sa.literal_column("lower(btrim(name))")],
        unique=True,
    )

    op.create_table(
        "zone_sequences",
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("next_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Los numeros de entrada empiezan en 1.
        sa.CheckConstraint("next_number >= 1", name="ck_zone_sequences_next_number"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("zone_id"),
    )


def downgrade() -> None:
    op.drop_table("zone_sequences")
    op.drop_index("uq_zones_event_nombre", table_name="zones")
    op.drop_table("zones")
    op.drop_index("ix_events_status_starts_at", table_name="events")
    op.drop_index("ix_events_owner_id_status", table_name="events")
    op.drop_table("events")
