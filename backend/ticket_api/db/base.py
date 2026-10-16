"""Base declarativa de SQLAlchemy 2.

Vive en el backend y solo en el backend: Flask no importa este paquete.
Los modelos de dominio se anaden en 3.2 (`users`, `auth_sessions`), 4.1
(`events`, `zones`, `zone_sequences`) y 5.1 (`orders`, `tickets`).
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base de todos los modelos de dominio."""
