"""Rutas de administracion de cuentas bajo `/api/v1/admin`.

Cada una depende de `AdminDep`, que es la unica forma de saber si quien llama es
administrador: la comprobacion esta en la dependencia y no al principio de cada
funcion, asi que anadir una ruta nueva no obliga a acordarse de escribirla.

La identidad de quien opera no se usa para autorizar nada -eso ya lo ha hecho la
dependencia- sino que la deja en el estado de la peticion para que el middleware
la escriba en la bitacora.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from ticket_api.api.deps import AdminDep
from ticket_api.api.proyecciones import usuario_publico
from ticket_api.db.session import DbUnit
from ticket_api.schemas.identity import UsuarioResponse
from ticket_api.services import administracion

router = APIRouter(prefix="/api/v1/admin", tags=["administracion"])


@router.get(
    "/users",
    response_model=list[UsuarioResponse],
    summary="Listado de cuentas de proveedor pendientes de aprobacion",
)
def listar_pendientes(_: AdminDep, unidad: DbUnit) -> list[UsuarioResponse]:
    """Solo cuentas de proveedor en `pending`.

    El filtro esta en el servicio y no aqui: la lista que se devuelve es la que
    dibuja la vista de 7.2, y filtrar en dos sitios haria que una de las dos
    dejara de listar lo mismo sin que ninguna se enterara.
    """
    return [usuario_publico(usuario) for usuario in administracion.listar_pendientes(unidad)]


@router.post(
    "/users/{usuario_id}/approve",
    response_model=UsuarioResponse,
    summary="Aprobacion de una cuenta de proveedor",
)
def aprobar(usuario_id: uuid.UUID, _: AdminDep, unidad: DbUnit) -> UsuarioResponse:
    """Pasa la cuenta a `active`.

    No emite tokens. Aprobar quita una restriccion; el siguiente login del
    proveedor es lo que abre su sesion, con las mismas reglas que cualquier otro.
    """
    return usuario_publico(administracion.aprobar_proveedor(unidad, usuario_id))


@router.post(
    "/users/{usuario_id}/suspend",
    response_model=UsuarioResponse,
    summary="Suspension de una cuenta",
)
def suspender(usuario_id: uuid.UUID, _: AdminDep, unidad: DbUnit) -> UsuarioResponse:
    """Suspende la cuenta y le revoca las sesiones vivas.

    La revocacion es parte de la operacion y no un efecto posterior: cuando vuelve
    la respuesta, el token del suspendido ya no abre ni una peticion.
    """
    return usuario_publico(administracion.suspender_cuenta(unidad, usuario_id))


__all__ = ["router"]
