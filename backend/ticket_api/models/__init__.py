"""Modelos de dominio.

Cada modulo agrupa las tablas de un area. `Base` vive en `ticket_api.db` porque
es infraestructura; lo que se guarda con ella es dominio.
"""

from ticket_api.models.events import Event, EventStatus, Zone, ZoneSequence
from ticket_api.models.identity import AuthSession, Role, User, UserStatus, normalizar_email
from ticket_api.models.sales import Order, OrderStatus, Ticket, normalizar_clave

__all__ = [
    "AuthSession",
    "Event",
    "EventStatus",
    "Order",
    "OrderStatus",
    "Role",
    "Ticket",
    "User",
    "UserStatus",
    "Zone",
    "ZoneSequence",
    "normalizar_clave",
    "normalizar_email",
]
