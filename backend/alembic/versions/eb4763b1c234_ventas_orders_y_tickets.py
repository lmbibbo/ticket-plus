"""ventas: `orders` y `tickets`

Revision ID: eb4763b1c234
Revises: 21b195eeee3e
Create Date: 2026-09-29 14:02:11.402117

Se escribio con `autogenerate` y se reviso a mano contra
`ticket_api/models/sales.py`. Cuatro cosas que conviene no "corregir" despues:

**Las tres `RESTRICT` de `tickets` son lo que protege el historico de ventas.**
`tickets.zone_id` en `RESTRICT` es la regla de 4.3 hecha foreign key: una zona
con entradas no se puede borrar, y con `CASCADE` bastaria con borrar el evento
para llevarselas. `orders.id` y `tickets.order_id` en `RESTRICT` son la misma
idea del otro lado: una entrada emitida no se borra porque alguien cierre mal una
relacion, y el comprador se quedaria sin lo que pago. Ninguna de las tres filas se
borra en el flujo previsto; la restriccion protege del error.

**`uq_orders_client_idempotency_key` es unica por cliente, no global.** La clave
la elige el cliente, asi que solo significa algo junto con quien la envio. Una
unicidad global rechazaria dos compras legitimas de dos personas a la vez con
`compra-1`, que es el caso normal de una preventa.

**`uq_tickets_zone_id_number` es por zona y no global.** Cada zona numera desde
1, asi que el numero 3 de la zona A no tiene nada que ver con el 3 de la zona B: lo
unico que no puede repetirse es dentro de la misma zona, que es donde el
comprador veria dos entradas con el mismo nombre. Ese indice es ademas el que
cubre la escritura de 5.4, que inserta por (zona, numero).

**`tickets.nonce` es `NOT NULL` y no tiene `server_default`.** A diferencia de los
UUID, el nonce no lo puede inventar la base: es un valor opaco y su unico requisito
es que sea imprevisible y distinto por entrada. Si la base le pusiera algo
predecible, dos entradas con el mismo numero en zonas distintas tendrian payloads
distintos por el resto de datos, pero el nonce seria la parte facil de adivinar.

`downgrade` borra `tickets` antes que `orders` porque las claves foraneas lo
exigen.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb4763b1c234"
down_revision: str | None = "21b195eeee3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("total", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # La unicidad que hace posible la idempotencia sin tabla de bloqueo: dos
        # peticiones con la misma clave y a la vez, una gana y la otra recibe el
        # conflicto de la restriccion.
        sa.UniqueConstraint(
            "client_id", "idempotency_key", name="uq_orders_client_idempotency_key"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'sold', 'rejected')", name="ck_orders_status"
        ),
        sa.CheckConstraint("total >= 0", name="ck_orders_total_no_negativo"),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''", name="ck_orders_idempotency_key_no_vacia"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # No hay indice propio en `orders`: `uq_orders_client_idempotency_key` empieza
    # por `client_id` y ya cubre "mis pedidos".

    op.create_table(
        "tickets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        # Unico por zona: el 3 de una zona no choca con el 3 de otra.
        sa.UniqueConstraint("zone_id", "number", name="uq_tickets_zone_id_number"),
        sa.CheckConstraint("number >= 1", name="ck_tickets_number_positivo"),
        sa.CheckConstraint("btrim(nonce) <> ''", name="ck_tickets_nonce_no_vacio"),
        sa.ForeignKeyConstraint(["client_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    # "Mis entradas" del cliente, que es la vista privada de 7.5.
    op.create_index("ix_tickets_client_id", "tickets", ["client_id"], unique=False)
    # Las entradas de un pedido, con las que se reconstruye el resultado de una
    # compra idempotente.
    op.create_index("ix_tickets_order_id", "tickets", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tickets_order_id", table_name="tickets")
    op.drop_index("ix_tickets_client_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_table("orders")
