"""Dependencias compartidas de las rutas.

Viven fuera de los routers para que un servicio nuevo pueda depender de la
configuracion sin importar un router entero, y sin que cada router tenga que
repetir el mismo `Annotated[..., Depends(...)]`.

Las de autorizacion son las unicas que deciden quien entra: una ruta que las
declare no tiene que comprobar nada mas. El rol se comprueba en la dependencia y
no en el cuerpo de la vista, que es la unica forma de que "solo el
administrador" no dependa de que cada ruta se acuerde. Cada rol tiene su propia
funcion y no una fabrica con un parametro porque asi el OpenAPI y el grafo de
dependencias dicen que hace cada ruta sin tener que seguir la llamada.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from ticket_api.config import Settings
from ticket_api.db.session import DbUnit
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import Role
from ticket_api.services.autorizacion import Actor, identidad_actual

#: Esquema de la cabecera `Authorization`. Se compara en minusculas porque RFC
#: 7235 lo define como tal, y escribir la cabecera es cosa del cliente.
ESQUEMA = "bearer"


def configuracion_de(request: Request) -> Settings:
    """Configuracion con la que se construyo la instancia que esta sirviendo.

    Se lee de `app.state` y no del cache global porque las pruebas construyen la
    aplicacion con una clave de firma propia: si se leyera del singleton, firmaria
    con la clave de desarrollo y los tokens de la prueba valdrian fuera de ella.
    """
    ajustes: Settings = request.app.state.settings
    return ajustes


#: Alias con el mismo proposito que `DbUnit`: un unico punto que las pruebas
#: pueden sustituir y que las firmas no tienen que repetir.
SettingsDep = Annotated[Settings, Depends(configuracion_de)]


def _token_de(cabecera: str | None) -> str:
    """Saca el token de la cabecera `Authorization`.

    Sin cabecera, o con un esquema que no sea `Bearer`, el resultado es un 401
    generico. Se comprueba el esquema entero y no se recorta nada: un token
    suelto sin esquema no es un token de esta API.
    """
    if cabecera is None:
        raise ProblemException(
            status=401,
            code=ErrorCode.UNAUTHENTICATED,
            title="No autenticado",
            detail="Falta la cabecera Authorization.",
        )
    esquema, _, token = cabecera.partition(" ")
    if esquema.lower() != ESQUEMA or not token.strip():
        raise ProblemException(
            status=401,
            code=ErrorCode.UNAUTHENTICATED,
            title="No autenticado",
            detail="La cabecera Authorization debe usar el esquema Bearer.",
        )
    return token.strip()


def actor_actual(
    unidad: DbUnit,
    ajustes: SettingsDep,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """Comprueba el token y devuelve quien hace la peticion.

    El identificador se deja en el estado de la peticion para que el middleware
    de acceso lo escriba en la bitacora: la especificacion pide que las acciones
    de administracion queden registradas con la identidad de quien las hizo, y
    sin esto la linea solo tendria un `request_id` imposible de atribuir.
    """
    actor = identidad_actual(unidad, _token_de(authorization), ajustes)
    request.state.user_id = str(actor.usuario_id)
    return actor


def administrador(actor: ActorDep) -> Actor:
    actor.exigir_rol(Role.ADMIN)
    return actor


def proveedor(actor: ActorDep) -> Actor:
    actor.exigir_rol(Role.EVENT_OWNER)
    return actor


def cliente(actor: ActorDep) -> Actor:
    actor.exigir_rol(Role.CLIENT)
    return actor


def gestor(actor: ActorDep) -> Actor:
    """Admin or event-owner: the two roles allowed to manage events.

    A single dependency for two roles instead of chaining `admin` or
    `proveedor`, because the route that declares it should say "whoever may
    manage events" and not make every reader reconstruct that union. The
    ownership half of the rule is not here: it needs the target event, so it
    lives in `services.eventos`.
    """
    actor.exigir_rol(Role.ADMIN, Role.EVENT_OWNER)
    return actor


#: Identidad autenticada, sin exigir rol. Es lo que usan las rutas que solo
#: necesitan saber quien va, y el punto de partida de las que si lo exigen.
ActorDep = Annotated[Actor, Depends(actor_actual)]

AdminDep = Annotated[Actor, Depends(administrador)]
PropietarioDep = Annotated[Actor, Depends(proveedor)]
ClienteDep = Annotated[Actor, Depends(cliente)]
GestorDep = Annotated[Actor, Depends(gestor)]


__all__ = [
    "ActorDep",
    "AdminDep",
    "ClienteDep",
    "GestorDep",
    "PropietarioDep",
    "SettingsDep",
    "actor_actual",
    "administrador",
    "cliente",
    "configuracion_de",
    "gestor",
    "proveedor",
]
