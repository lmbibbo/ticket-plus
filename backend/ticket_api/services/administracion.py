"""Administracion de cuentas: aprobacion de proveedores y suspension.

Vive en un servicio y no en las rutas por la misma razon que el resto de
autenticacion: quien llama es el router, que solo traduce. Y porque estas dos
operaciones tocan el estado de una cuenta que no es del que las pide, la regla
de quien puede hacerlas no puede depender de que la ruta se acuerde de
comprobarla.

Tres decisiones que la especificacion fija y que conviene dejar escritas:

**Solo se aprueban proveedores pendientes.** Un cliente nace activo y un
proveedor nace pendiente; aprobar a un cliente, o aprobar dos veces, no es un
error de permiso sino una peticion que no aplica al estado actual, y por eso es
409 y no 403.

**Suspender revoca las sesiones en la misma transaccion.** Un token de acceso
vive quince minutos: si la suspension solo cambiara el estado de la cuenta, el
suspendido seguiria entrando durante esa ventana. La revocacion va antes del
`commit` precisamente para que no haya un instante en que la cuenta figure
suspendida con sus sesiones vivas.

**Quien opera no entra aqui.** El administrador se comprueba en la dependencia de
la ruta, y su identidad la registra el middleware de acceso. Estas funciones no
reciben el actor porque no tienen ninguna decision que tomar sobre el: si lo
recibian, el 403 dependeria de que cada ruta pase el dato y no se olvide.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import AuthSession, Role, User, UserStatus


def _no_encontrado(usuario_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=404,
        code=ErrorCode.NOT_FOUND,
        title="Cuenta no encontrada",
        detail="No hay ninguna cuenta con ese identificador.",
    )


def _estado_invalido(detalle: str) -> ProblemException:
    return ProblemException(
        status=409,
        code=ErrorCode.INVALID_STATE_TRANSITION,
        title="Operacion no aplicable",
        detail=detalle,
    )


def _cuenta(sesion: Session, usuario_id: uuid.UUID) -> User:
    usuario = sesion.get(User, usuario_id)
    if usuario is None:
        raise _no_encontrado(usuario_id)
    return usuario


def listar_pendientes(unidad: UnitOfWork) -> list[User]:
    """Proveedores a la espera de aprobacion, del mas antiguo al mas reciente.

    Se filtra por rol y por estado a la vez. Solo por estado devolveria clientes
    recien registrados -que nacen `active`, pero no en esta fase- y solo por rol
    devolveria proveedores ya aprobados, que ya no tienen nada que aprobar.
    """
    filas = unidad.sesion.scalars(
        select(User)
        .where(User.role == Role.EVENT_OWNER.value, User.status == UserStatus.PENDING.value)
        .order_by(User.created_at, User.id)
    ).all()
    return list(filas)


def aprobar_proveedor(unidad: UnitOfWork, usuario_id: uuid.UUID) -> User:
    """Activa una cuenta de proveedor pendiente.

    Desde aqui, la cuenta puede iniciar sesion. No emite tokens: el login sigue
    siendo la unica via, para que aprobar no significa "dar una sesion a quien
    este mirando la pantalla del administrador".
    """
    usuario = _cuenta(unidad.sesion, usuario_id)
    if usuario.role != Role.EVENT_OWNER.value:
        raise _estado_invalido("Solo se aprueban cuentas de proveedor.")
    if usuario.status != UserStatus.PENDING.value:
        raise _estado_invalido(f"La cuenta ya no esta pendiente: esta en {usuario.status}.")
    usuario.status = UserStatus.ACTIVE.value
    unidad.commit()
    return usuario


def suspender_cuenta(unidad: UnitOfWork, usuario_id: uuid.UUID) -> User:
    """Suspende una cuenta y le revoca las sesiones vivas.

    La revocacion es por cuenta y no por familia: una cuenta puede tener varias
    cadenas abiertas -cada login abre la suya- y suspender tiene que cortarlas
    todas. Se hace con una sentencia, como la revocacion de 3.5, porque las
    filas abiertas pueden ser mas de las que esta sesion conoce.
    """
    usuario = _cuenta(unidad.sesion, usuario_id)
    if usuario.status == UserStatus.DELETED.value:
        raise _estado_invalido("Una cuenta dada de baja no se puede suspender.")
    if usuario.status == UserStatus.SUSPENDED.value:
        raise _estado_invalido("La cuenta ya esta suspendida.")

    usuario.status = UserStatus.SUSPENDED.value
    unidad.sesion.execute(
        update(AuthSession)
        .where(AuthSession.user_id == usuario_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    unidad.commit()
    return usuario


__all__ = ["aprobar_proveedor", "listar_pendientes", "suspender_cuenta"]
