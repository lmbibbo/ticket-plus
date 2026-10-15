"""identidad inicial: `users` y `auth_sessions`

Revision ID: e34227427793
Revises:
Create Date: 2026-09-29 11:44:44.337299

Es la primera migracion del proyecto, asi que fija el esquema contra el que se
verifica todo lo demas. Se escribio con `autogenerate` y se reviso a mano contra
`ticket_api/models/identity.py`.

Tres cosas que no son obvias y que conviene no "corregir" mas adelante:

**`gen_random_uuid()` es `server_default` y no solo `default` de Python.** Es lo
que permite que un `INSERT` que no pasa por el ORM -una migracion, un script de
mantenimiento, una correccion manual- no falle con NOT NULL. Por eso
`users.id` y `auth_sessions.id` lo llevan.

**`family_id` es el unico UUID sin `server_default`, a proposito.** Es el
identificador compartido por todos los tokens de una misma cadena, y la
deteccion de reutilizacion se apoya en el: comparar familias es lo que permite
revocar la cadena entera cuando se presenta un token ya rotado. Si la base le
pusiera un valor aleatorio a una sesion creada sin `family_id` explicito, esa
sesion quedaria en una familia propia y la reutilizacion pasaria
desdetectada, en silencio. Un NOT NULL que rompe es preferible a un valor
plausible y equivocado.

**`auth_sessions.refresh_token_hash` es unico a proposito.** Dos sesiones con el
mismo token serian la misma sesion, y el indice de esa restriccion es ademas el
que usa la busqueda por hash al renovar, que es la operacion de la frecuencia.

`downgrade` borra primero `auth_sessions`: el orden inverso al de `upgrade` no es
estetico, es necesario, porque su clave foranea a `users` lo exige.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e34227427793"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("role IN ('client', 'event-owner', 'admin')", name="ck_users_role"),
        sa.CheckConstraint(
            "status IN ('active', 'pending', 'suspended', 'deleted')", name="ck_users_status"
        ),
        # El invariante que hace que el indice unico signifique algo. Tambien
        # rechaza lo que entre por SQL directo con espacios al borde.
        sa.CheckConstraint("email = lower(btrim(email))", name="ck_users_email_normalizado"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_role_status", "users", ["role", "status"], unique=False)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "replaced_by_id IS NULL OR replaced_by_id <> id", name="ck_auth_sessions_no_ciclica"
        ),
        # SET NULL y no cascade: borrar una sesion no debe arrastrar a la que la
        # sustituyo, que es la que sigue viva.
        sa.ForeignKeyConstraint(["replaced_by_id"], ["auth_sessions.id"], ondelete="SET NULL"),
        # CASCADE y no restrict: al borrar una cuenta se van sus sesiones, que si
        # no quedarian huerfanas y sin forma de revocar.
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"
        ),
    )
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"], unique=False)
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"], unique=False)
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_family_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_role_status", table_name="users")
    op.drop_table("users")
