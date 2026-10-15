"""Blueprint del area de cliente: sus entradas y su cuenta.

La vista existe desde 6.2 para que la navegacion por rol tenga destino y para
que la proteccion sea comprobable; el contenido real -las entradas compradas, sus
numeros y sus codigos QR- es 7.5.
"""

from __future__ import annotations

from flask import Blueprint, render_template

from ticket_web.auth import exigir_rol

account_bp = Blueprint("account", __name__, url_prefix="/mi-cuenta")


@account_bp.get("/")
@exigir_rol("client")
def cuenta() -> str:
    """Pagina privada del cliente."""
    return render_template("account/cuenta.html")