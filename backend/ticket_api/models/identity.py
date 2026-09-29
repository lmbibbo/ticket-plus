"""Identidad: `users` y `auth_sessions`.

Tres decisiones de este archivo que no son obvias:

**El email se normaliza al escribir y la base lo vuelve a comprobar.** Un
`@validates` normaliza y un `CHECK` en la tabla exige `email = lower(btrim(email))`.
Los dos, y no solo el primero, porque el `CHECK` tambien protege de lo que
entre por SQL directo, por una migracion o por un script de mantenimiento. La
unicidad de un email no normalizado es falsa: `Ana@Ejemplo.com` y
`ana@ejemplo.com` son dos cuentas distintas y solo uno de los dos cabe en el
indice unico.

**El refresh token se guarda con SHA-256, no con Argon2id.** Argon2id se usa
para contrasenas, que son cortas y se pueden adivinar. Un refresh token opaco
tiene toda la entropia que queramos, asi que no hay nada que adivinar: un hash
rapido y su indice son mejores. Poner Argon2 aqui solo costaria CPU por
peticion de renovacion.

**Cada refresh token lleva `family_id`.** La renovacion rota el token, y la
reutilizacion de un token ya rotado se detecta comparando familias: si alguien
presenta un token viejo, se revoca la familia entera. Sin la columna no se puede
exigir el comportamiento de "reutilizacion detectada" que pide la especificacion.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from ticket_api.db.base import Base

#: Generacion de identificadores en la propia base. Se declara tambien como
#: `server_default` y no solo como `default` de Python: si el UUID lo pusiera
#: unicamente el ORM, cualquier `INSERT` que no pase por el modelo -una migracion,
#: un script de mantenimiento, una correccion manual- fallaria con NOT NULL.
#: `gen_random_uuid()` viene en PostgreSQL 13 o posterior, sin extension.
UUID_EN_LA_BASE = text("gen_random_uuid()")

#: Longitud maxima de un email (RFC 5321). Un limite propio evita que un valor
#: enorme se trague el indice unico de la tabla.
EMAIL_MAXIMO: int = 320

#: Longitudes de los hashes. El de contrasena codifica parametros, sal y hash en
#: texto; el del refresh es un SHA-256 en hexadecimal.
PASSWORD_HASH_MAXIMO: int = 255
REFRESH_HASH_MAXIMO = 64


class Role(enum.StrEnum):
    """Los tres roles. El orden no implica permisos."""

    CLIENT = "client"
    EVENT_OWNER = "event-owner"
    ADMIN = "admin"


class UserStatus(enum.StrEnum):
    """Ciclo de vida de una cuenta.

    `deleted` es borrado logico y no libera el email: la fila sigue ocupando el
    indice unico. Reutilizar el correo de una cuenta dada de baja permitiria que
    alguien se hiciese con el historial de la anterior, asi que el email queda
    retirado para siempre.
    """

    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"
    DELETED = "deleted"


def normalizar_email(valor: str) -> str:
    """Deja el email en la forma en la que se compara.

    Solo recorta y pasa a minusculas. No se unifican puntos ni alias de dominio:
    `ana+entradas@correo.com` y `ana@correo.com` pueden ser cuentas distintas, y
   tratarlas como una le regalaria a cualquiera acceso a la cuenta del otro.
    """
    limpio = valor.strip().lower()
    if not limpio:
        raise ValueError("el email no puede estar vacio")
    if len(limpio) > EMAIL_MAXIMO:
        raise ValueError(f"el email supera los {EMAIL_MAXIMO} caracteres")
    return limpio


def _valores(enum_tipo: type[enum.StrEnum]) -> str:
    """Lista de valores para una restriccion CHECK."""
    return ", ".join(f"'{miembro.value}'" for miembro in enum_tipo)


class User(Base):
    """Una cuenta. El email es la identidad de acceso."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    email: Mapped[str] = mapped_column(String(EMAIL_MAXIMO), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(PASSWORD_HASH_MAXIMO), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=Role.CLIENT.value)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UserStatus.ACTIVE.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user",
        # `delete-orphan` cubre el borrado desde Python; `passive_deletes` deja
        # que sea la base la que borre y devuelva el numero de filas borradas.
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            f"role IN ({_valores(Role)})",
            name="ck_users_role",
        ),
        CheckConstraint(
            f"status IN ({_valores(UserStatus)})",
            name="ck_users_status",
        ),
        # El invariante que hace que el indice unico signifique algo. Tambien
        # rechaza un email con espacios al borde que Python permitiria.
        CheckConstraint(
            "email = lower(btrim(email))",
            name="ck_users_email_normalizado",
        ),
        # La consulta del administrador es "proveedores pendientes": sin este
        # indice seria un recorrido de tabla por cada apertura de la lista.
        Index("ix_users_role_status", "role", "status"),
    )

    @validates("email")
    def _normaliza(self, _clave: str, valor: str) -> str:
        return normalizar_email(valor)

    @property
    def esta_activo(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    def __repr__(self) -> str:
        return f"<User {self.role} {self.status}>"


class AuthSession(Base):
    """Una sesion de renovacion.

    La sesion es la fila, no el token: cada renovacion crea una fila nueva y
    apunta a la anterior, de modo que la cadena completa queda registrada. Se
    guarda el hash del token, nunca el token: una copia de la base de datos no
    debe bastar para suplantar a nadie.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4, server_default=UUID_EN_LA_BASE
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        # CASCADE y no RESTRICT: al borrar una cuenta se van sus sesiones. Un
        # ON DELETE restrict dejaria filas huerfanas que impiden el borrado y
        # que ademas nadie podria revocar.
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(REFRESH_HASH_MAXIMO), nullable=False)
    #: Identificador compartido por todos los tokens de una misma cadena. Es lo
    #: que hace posible revocar la cadena al detectar una reutilizacion.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, default=uuid4)
    #: La sesion a la que dio lugar esta. `SET NULL` y no cascade: el borrado de
    #: una sesion no debe arrastrar a la que la sustituyo.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    # La relacion necesita `foreign_keys` explicito: sin el, SQLAlchemy no sabe
    # cual de las dos columnas que apuntan a `auth_sessions.id` es la que une, y
    # la carga falla. Se escriben como cadena porque un lambda aqui se evaluaria
    # en el cuerpo de la clase, donde `id` y `replaced_by_id` aun no existen.
    replaced_by: Mapped[AuthSession | None] = relationship(
        remote_side="AuthSession.id",
        foreign_keys="AuthSession.replaced_by_id",
    )

    __table_args__ = (
        # Unico: dos sesiones con el mismo token serian la misma sesion, y el
        # indice de este contrainte cubre la busqueda por hash al renovar.
        UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"),
        # Una sesion no puede ser su propia sucesora.
        CheckConstraint(
            "replaced_by_id IS NULL OR replaced_by_id <> id",
            name="ck_auth_sessions_no_ciclica",
        ),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_family_id", "family_id"),
        # Soporta la limpieza periodica de sesiones vencidas (riesgo operativo
        # declarado en el diseno): sin indice seria un seq scan por ejecucion.
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    @property
    def esta_revocada(self) -> bool:
        return self.revoked_at is not None

    def esta_vencida(self, ahora: datetime) -> bool:
        return self.expires_at <= ahora

    def puede_renovarse(self, ahora: datetime) -> bool:
        """Vigente y no revocada. Un token caducado no se renueva ni se reutiliza."""
        return not self.esta_revocada and not self.esta_vencida(ahora)

    def revocar(self, ahora: datetime | None = None) -> bool:
        """Revoca la sesion. Devuelve si la cambio, para no repetir trabajo."""
        if self.esta_revocada:
            return False
        self.revoked_at = ahora or datetime.now(UTC)
        return True

    def __repr__(self) -> str:
        estado = "revocada" if self.esta_revocada else "activa"
        return f"<AuthSession {estado} familia={self.family_id}>"
