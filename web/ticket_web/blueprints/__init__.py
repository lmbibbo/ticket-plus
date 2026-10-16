"""Blueprints de la aplicacion web.

Cada area de la web (autenticacion, area de cliente, gestion de proveedor,
administracion y sistema) tiene su blueprint. El motivo no es estetico: los
blueprints son el punto donde se cuelga la proteccion por rol, de modo que anadir
una vista a `admin` no puede dejarse sin proteger por olvido.
"""

from __future__ import annotations

from flask import Flask

from ticket_web.blueprints.account import account_bp
from ticket_web.blueprints.admin import admin_bp
from ticket_web.blueprints.auth import auth_bp
from ticket_web.blueprints.owner import owner_bp
from ticket_web.blueprints.system import system_bp

#: Blueprints que forman la aplicacion web. Cada uno cuelga sus vistas y, cuando
#: corresponde, la proteccion por rol que las limita.
BLUEPRINTS = (
    system_bp,
    auth_bp,
    account_bp,
    owner_bp,
    admin_bp,
)


def register_blueprints(app: Flask) -> None:
    """Registra todos los blueprints de la web."""
    for blueprint in BLUEPRINTS:
        app.register_blueprint(blueprint)


__all__ = [
    "BLUEPRINTS",
    "account_bp",
    "admin_bp",
    "auth_bp",
    "owner_bp",
    "register_blueprints",
    "system_bp",
]