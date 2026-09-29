"""Modelos de dominio.

Cada modulo agrupa las tablas de un area. `Base` vive en `ticket_api.db` porque
es infraestructura; lo que se guarda con ella es dominio.
"""

from ticket_api.models.identity import AuthSession, Role, User, UserStatus, normalizar_email

__all__ = ["AuthSession", "Role", "User", "UserStatus", "normalizar_email"]
