"""La conversacion de la sesion web con la API.

`auth.py` sabe quien es el visitante y manipula la sesion del servidor; este
modulo es el que habla con FastAPI. La separacion no es estetica: las reglas de
*cuando* abrir, renovar y cerrar una sesion se pueden probar sin HTTP, y las
decisiones de *que mostrar* en un formulario viven en las vistas.

Los codigos de negocio que llegan en `problem+json` se deciden aqui y no en las
plantillas, para que un cambio en el backend no obligue a reescribir HTML. La web
no importa nada de `ticket_api`: son dos aplicaciones distintas y compartir
modulos volveria a atar lo que el diseno dejo suelto. Por eso compara estados
HTTP, que son el contrato publico, y no codigos de negocio, que serian una
dependencia hacia atras.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Final, cast

from flask import current_app, url_for

from ticket_web.api_client import (
    API_CLIENT_KEY,
    ApiFailure,
    ApiLike,
    ApiProblemError,
    ApiResponse,
    ApiUnavailableError,
)
from ticket_web.auth import (
    access_token,
    actualizar_credenciales,
    claims_del_token,
    guardar_credenciales,
    olvidar_credenciales,
    refresh_token,
)
from ticket_web.observability.middleware import set_problem_code

RUTA_REGISTRO: Final = "/api/v1/auth/register"
RUTA_LOGIN: Final = "/api/v1/auth/login"
RUTA_REFRESH: Final = "/api/v1/auth/refresh"
RUTA_LOGOUT: Final = "/api/v1/auth/logout"

#: Estado con el que la API devuelve una cuenta que ya puede entrar. El registro
#: decide con este valor si abre sesion o si explica que hay que esperar a la
#: aprobacion, y no con el rol pedido: la respuesta de la API manda sobre lo que
#: el formulario pidio.
ESTADO_ACTIVO: Final = "active"

#: Respuesta que la API da a unas credenciales que no acepta. Cualquier motivo -
#: correo inexistente, contrasena mala, cuenta sin aprobar- llega aqui igual, y esa
#: indistinguibilidad es deliberada: distinguir los casos convertiria el login en
#: un buscador de correos registrados y de proveedores pendientes.
NO_AUTENTICADO: Final = 401

#: Destino de la sesion segun el rol. Un rol que no este en el mapa no puede
#: entrar: se cae en la portada en vez de en un area que no le toca.
DESTINO_POR_ROL: Final[dict[str, str]] = {
    "client": "account.cuenta",
    "event-owner": "owner.eventos",
    "admin": "admin.usuarios",
}


class CredencialesRechazadas(ApiProblemError):
    """El API no acepto el correo ni la contrasena.

    Clase propia y no un codigo que las vistas tengan que comparar, porque el
    login tiene un unico motivo de fallo: todo lo demas -API caida, timeout, 500-
    no es culpa de lo que el visitante escribio y se resuelve distinto.
    """


@dataclass(frozen=True, slots=True)
class CuentaCreada:
    """Lo que el registro devuelve, ya traducido a una decision de la web."""

    usuario_id: str
    email: str
    rol: str
    activa: bool


def cliente_api() -> ApiLike:
    """El cliente compartido, el mismo que usan el resto de vistas."""
    return cast(ApiLike, current_app.extensions[API_CLIENT_KEY])


def cabecera_bearer() -> dict[str, str]:
    """`Authorization` con el token de acceso de la sesion actual, o nada.

    Se la pasa el `ApiClient` en cada peticion y no cada vista: una vista que
    olvidara la cabecera seria un 401 silencioso, y el token vive en la sesion
    del servidor, que es donde ya esta desde 6.1. Las rutas de credenciales
    (login, registro, refresh, logout) reciben lo suyo en el cuerpo y ignoran
    esta cabecera.
    """
    token = access_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def destino_por_rol(rol: str) -> str:
    """Endpoint al que va una sesion abierta con ese rol."""
    endpoint = DESTINO_POR_ROL.get(rol)
    if endpoint is None:
        return url_for("system.index")
    return url_for(endpoint)


def registrar(email: str, password: str, role: str) -> CuentaCreada:
    """Crea la cuenta y, si ya puede entrar, abre la sesion.

    El alta y el login son dos llamadas porque son dos operaciones distintas en la
    API: `register` no emite tokens, y un cliente recien creado sin sesion se
    queda mirando un formulario de login que no explica por que esta ahi.

    Para un proveedor esto no llega a loguear: la cuenta nace `pending` y la API
    rechazaria el login con un `invalid_credentials` generico, que le diria al
    visitante que su correo o su contrasena estan mal cuando lo cierto es que
    todavia no hay que aprobarlo.
    """
    respuesta = cliente_api().post(
        RUTA_REGISTRO,
        json={"email": email, "password": password, "role": role},
    )
    cuenta = _cuenta_creada(respuesta.data)
    if cuenta.activa:
        abrir_sesion(email, password)
    return cuenta


def abrir_sesion(email: str, password: str) -> None:
    """Autentica contra la API y guarda el par de tokens.

    `guardar_credenciales` vacia la sesion antes de escribir: al entrar con otra
    cuenta en el mismo navegador no puede quedar un token de la anterior.
    """
    try:
        respuesta = cliente_api().post(
            RUTA_LOGIN,
            json={"email": email, "password": password},
        )
    except ApiProblemError as exc:
        if exc.status == NO_AUTENTICADO:
            raise CredencialesRechazadas(exc.problem) from exc
        raise
    _guardar(respuesta, con_rotacion_de_csrf=True)


def cerrar_sesion() -> None:
    """Revoca el refresh en la API y borra las credenciales locales.

    El borrado ocurre igual si la API falla: dejar el refresh vivo en la base
    despues de que el visitante ha dicho que quiere salir seria un fallo de
    seguridad disfrazado de disponibilidad.
    """
    refresh = olvidar_credenciales()
    if refresh is None:
        return
    with suppress(ApiProblemError, ApiUnavailableError):
        cliente_api().post(RUTA_LOGOUT, json={"refresh_token": refresh})


def renovar_sesion() -> bool:
    """Rota el access token si el que hay en la sesion ya no vale.

    Devuelve si la sesion sigue viva. Un fallo de negocio -refresh revocado,
    reutilizado o cuenta no activa- significa que la sesion ya no existe, y lo
    unico honesto es borrarla: dejarla puesta haria que el visitante viera una
    navegacion de usuario autenticado que ninguna operacion va a aceptar.

    Un fallo de integracion, en cambio, no borra nada. La API puede estar caida y
    el refresh sigue siendo valido: tirarlo convertiria una caida de treinta
    segundos en un cierre de sesion que el visitante no ha pedido.
    """
    token = refresh_token()
    if token is None:
        return False

    try:
        respuesta = cliente_api().post(RUTA_REFRESH, json={"refresh_token": token})
    except ApiProblemError as exc:
        set_problem_code(f"refresh_rechazado:{exc.code}")
        olvidar_credenciales()
        return False
    except ApiUnavailableError as exc:
        set_problem_code(exc.code, mensaje=f"renovacion: {exc.fallo.value}")
        return True

    _guardar(respuesta, con_rotacion_de_csrf=False)
    return True


def necesita_renovacion() -> bool:
    """Si hay refresh y el access token de la sesion ya no es utilizable.

    `claims_del_token` devuelve `None` para un token caducado, asi que la pregunta
    "¿sigue vivo?" se hace leyendo el token y no comparando fechas por separado.
    """
    if refresh_token() is None:
        return False
    return claims_del_token(access_token() or "") is None


def _guardar(respuesta: ApiResponse, *, con_rotacion_de_csrf: bool) -> None:
    """Extrae el par de tokens de la respuesta y lo deja en la sesion.

    El par se comprueba antes de escribir. Un 200 al que le faltan los tokens no
    es una sesion, y guardarla dejaria al visitante en un estado que la web cree
    bueno y la API no: es exactamente el fallo que da por bonnes las credenciales
    de otro.
    """
    acceso, nuevo_refresh = _par_de_tokens(respuesta.data)
    if con_rotacion_de_csrf:
        guardar_credenciales(acceso, nuevo_refresh)
    else:
        actualizar_credenciales(acceso, nuevo_refresh)


def _par_de_tokens(datos: object) -> tuple[str, str]:
    if not isinstance(datos, dict):
        raise _ilegible()

    acceso = datos.get("access_token")
    nuevo_refresh = datos.get("refresh_token")
    if not isinstance(acceso, str) or not isinstance(nuevo_refresh, str):
        raise _ilegible()
    if not acceso or not nuevo_refresh:
        raise _ilegible()
    return acceso, nuevo_refresh


def _ilegible() -> ApiUnavailableError:
    """Respuesta que no se puede entender como tokens o como cuenta.

    Se traduce al mismo fallo recuperable que una API caida porque para el
    visitante es lo mismo: no hay nada que pueda hacer con una respuesta que no
    se entiende, y mostrar el cuerpo crudo seria filtrar la respuesta de otro
    proceso al navegador.
    """
    return ApiUnavailableError(ApiFailure.UNREADABLE)


def _cuenta_creada(datos: object) -> CuentaCreada:
    """Traduce el `UsuarioResponse` del registro a la decision de la web."""
    if not isinstance(datos, dict):
        raise _ilegible()

    usuario_id = datos.get("id")
    email = datos.get("email")
    rol = datos.get("role")
    estado = datos.get("status")
    if not isinstance(usuario_id, str) or not isinstance(email, str) or not isinstance(rol, str):
        raise _ilegible()
    if not isinstance(estado, str):
        raise _ilegible()

    return CuentaCreada(
        usuario_id=usuario_id,
        email=email,
        rol=rol,
        activa=estado == ESTADO_ACTIVO,
    )


__all__ = [
    "DESTINO_POR_ROL",
    "ESTADO_ACTIVO",
    "NO_AUTENTICADO",
    "RUTA_LOGIN",
    "RUTA_LOGOUT",
    "RUTA_REFRESH",
    "RUTA_REGISTRO",
    "CredencialesRechazadas",
    "CuentaCreada",
    "abrir_sesion",
    "cabecera_bearer",
    "cerrar_sesion",
    "cliente_api",
    "destino_por_rol",
    "necesita_renovacion",
    "registrar",
    "renovar_sesion",
]