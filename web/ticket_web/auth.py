"""Identidad del visitante en la sesion web.

La web no decide quien es el usuario: decide que enlaces dibujar y a que vistas
dejar entrar, y para eso necesita el rol. Lo lee del `role` que la API ya firmo en
el token de acceso, que vive en el almacen del servidor desde 6.1 y nunca sale
hacia el navegador.

**Por que basta con leer el token sin verificarlo.** La firma se comprueba en la
API, que es la frontera autoritativa: cada operacion vuelve a validar el token y a
exigir el rol, asi que un `role` manipulado no compra ni una compra ni una
aprobacion. Aqui solo decide que se dibuja, y eso es presentacion. Verificar la
firma en la web exigiria entregarle la clave publica de Ed25519 y mantener una
segunda implementacion de la verificacion, con el riesgo de que las diverjan.

**Por que un token caducado se traduce en "sin sesion".** El token de acceso tiene
vida corta a proposito. Cuando caduca, la identidad desaparece de la sesion, la
navegacion vuelve a la de visitante y la siguiente operacion necesita renovar el
token, que es el camino de 6.3.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Final, TypeVar

from flask import current_app, g, render_template, session

#: Claves con las que la sesion guarda las credenciales. Se centralizan para que
#: ni el guardado ni la lectura se escriban con literales distintos.
CLAVE_ACCESO: Final = "access_token"
CLAVE_REFRESH: Final = "refresh_token"

#: Claims que hacen falta para saber quien va. Sin `exp` el token no caduca y sin
#: `sub` no hay sujeto al que atribuir la sesion, asi que se exigen los dos.
_CLAIM_EXPIRACION: Final = "exp"
_CLAIM_ROL: Final = "role"
_CLAIM_SUJETO: Final = "sub"
_CLAIM_SESION: Final = "sid"

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class Identidad:
    """Quien esta conectado, segun el token de acceso."""

    usuario_id: uuid.UUID
    rol: str
    sesion_id: str | None


def guardar_credenciales(access_token: str, refresh_token: str) -> None:
    """Guarda el par de tokens en el almacen del servidor.

    Se vacia la sesion antes: si el mismo navegador tenia otra cuenta abierta y
    entra con una nueva, conservarla dejaria una identidad hibrida con tokens de
    dos sesiones distintas.
    """
    session.clear()
    session[CLAVE_ACCESO] = access_token
    session[CLAVE_REFRESH] = refresh_token


def actualizar_credenciales(access_token: str, refresh_token: str) -> None:
    """Sustituye el par de tokens sin vaciar el resto de la sesion.

    Es la diferencia con `guardar_credenciales`, y no es un detalle: la renovacion
    es la misma sesioniente que sigue con la misma identidad, y vaciar la sesion
    rotaria tambien el token CSRF. Un formulario que el visitante ya tiene en
    pantalla pasaria a fallar con un 400 que no puede corregir, obligandole a
    recargar la pagina cada vez que su token de acceso caduca.

    En el login y el registro, en cambio, si se rota el token CSRF: ahi cambia la
    persona que hay delante del navegador y es donde un token heredado de la
    sesion anterior seria un fixation.
    """
    session[CLAVE_ACCESO] = access_token
    session[CLAVE_REFRESH] = refresh_token


def olvidar_credenciales() -> str | None:
    """Borra las credenciales de la sesion y devuelve el refresh token.

    El token se devuelve para que la vista pueda revocarlo en la API: borrar solo
    la copia local dejaria el refresh token vivo, y uno de esos basta para abrir
    sesion hasta que caduque.
    """
    refresh = session.get(CLAVE_REFRESH)
    session.clear()
    return refresh if isinstance(refresh, str) else None


def access_token() -> str | None:
    """Token de acceso guardado en la sesion, si lo hay."""
    return _texto_de_sesion(CLAVE_ACCESO)


def refresh_token() -> str | None:
    """Refresh token guardado en la sesion, si lo hay."""
    return _texto_de_sesion(CLAVE_REFRESH)


def identidad_actual() -> Identidad | None:
    """Quien esta conectado, o `None` si no hay sesion utilizable.

    Un token ilegible o sin las claims necesarias se trata como si no hubiera
    sesion. Propagar el fallo obligaria a que cada vista distinguiera tres casos
    -"no entro", "entro con algo raro" y "entro con el token caducado"- y la
    respuesta correcta es la misma: pedir un login nuevo.
    """
    token = access_token()
    if token is None:
        return None

    claims = claims_del_token(token)
    if claims is None:
        return None

    try:
        sujeto = uuid.UUID(str(claims[_CLAIM_SUJETO]))
    except (KeyError, ValueError):
        return None

    rol = claims.get(_CLAIM_ROL)
    sesion_id = claims.get(_CLAIM_SESION)
    return Identidad(
        usuario_id=sujeto,
        rol=rol if isinstance(rol, str) else "",
        sesion_id=sesion_id if isinstance(sesion_id, str) else None,
    )


def rol_actual() -> str | None:
    """Rol de la sesion activa, o `None` si no hay sesion."""
    identidad = identidad_actual()
    return None if identidad is None else identidad.rol


def esta_autenticado() -> bool:
    """Si hay una identidad utilizable en la sesion."""
    return identidad_actual() is not None


def exigir_rol(*roles: str) -> Callable[[F], F]:
    """Reserva una vista a los roles indicados.

    Quien no cumple recibe 403 y no un 401 ni un salto a `/auth/login`. La
    respuesta no distingue "no entro" de "entro pero no le toca", que es lo que la
    especificacion pide: negar sin revelar que la vista existe.
    """

    def _decorador(vista: F) -> F:
        @wraps(vista)
        def _envuelta(*args: Any, **kwargs: Any) -> Any:
            identidad = identidad_actual()
            if identidad is None or identidad.rol not in roles:
                return acceso_denegado()
            g.identity = identidad
            return vista(*args, **kwargs)

        return _envuelta  # type: ignore[return-value]

    return _decorador


def acceso_denegado() -> tuple[str, int]:
    """Pagina de acceso denegado, con su 403 y su codigo de negocio."""
    from ticket_web.observability.middleware import set_problem_code

    set_problem_code("access_denied")
    return (render_template("error/acceso_denegado.html"), 403)


def _texto_de_sesion(clave: str) -> str | None:
    """Valor de una clave de sesion, solo si es texto."""
    valor = session.get(clave)
    return valor if isinstance(valor, str) else None


def claims_del_token(token: str) -> dict[str, Any] | None:
    """Payload del JWT si el token es utilizable, o `None`.

    No se verifica la firma a proposito; el motivo esta en el modulo. Lo que si se
    comprueba es que el payload sea un objeto JSON con `exp` en el futuro: un
    token sin expiracion no caduca nunca, y uno ya caducado no sirve. Aceptar
    cualquiera de los dos seria el fallo exacto que la vida corta del token evita.
    """
    partes = token.split(".")
    if len(partes) != 3:
        return None
    try:
        crudo = base64.urlsafe_b64decode(partes[1] + "=" * (-len(partes[1]) % 4))
        claims = json.loads(crudo)
    except (ValueError, binascii.Error):
        return None
    if not isinstance(claims, dict):
        return None
    if not _expiracion_vigente(claims.get(_CLAIM_EXPIRACION)):
        return None
    return claims


def _expiracion_vigente(exp: object) -> bool:
    """Si el claim `exp` es un numero que esta en el futuro.

    `exp` del JWT son segundos desde la epoca. Un valor que no sea un numero se
    trata como caducado: preferible perder una sesion a mantener una abierta con
    una expiracion que nadie sabe leer.
    """
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        return False
    return float(exp) > datetime.now(UTC).timestamp()


def nombre_app() -> str:
    """Nombre con el que la web se identifica en la bitacora."""
    return str(current_app.config["APP_NAME"])


__all__ = [
    "CLAVE_ACCESO",
    "CLAVE_REFRESH",
    "Identidad",
    "acceso_denegado",
    "access_token",
    "actualizar_credenciales",
    "claims_del_token",
    "esta_autenticado",
    "exigir_rol",
    "guardar_credenciales",
    "identidad_actual",
    "nombre_app",
    "refresh_token",
    "rol_actual",
]