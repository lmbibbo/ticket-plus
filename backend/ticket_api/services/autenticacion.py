"""Autenticacion por credenciales.

El login es la unica operacion que convierte un par correo/contrasena en una
sesion. Vive en un servicio y no en la vista porque la regla que lo hace seguro
-todo fallo se ve igual desde fuera- tiene que valer para cualquier camino que
termine autenticando, incluido el que se anada mas adelante.

Tres decisiones que no son obvias:

**Un solo error para todos los fallos.** Cuenta inexistente, contrasena mala y
cuenta sin aprobar devuelven el mismo `invalid_credentials`. Con un codigo
distinto para cada caso, el login se convierte en un buscador de correos
registrados y de proveedores pendientes de aprobacion.

**El tiempo tambien es una respuesta.** Un correo que no existe no tiene hash
que verificar y responde en microsegundos; uno que si existe tarda lo que cuesta
Argon2id. La diferencia es medible desde fuera, asi que cuando no hay cuenta que
verificar se verifica contra un hash senuelo y se gasta el mismo tiempo.

**La cuenta tiene que estar activa aqui.** La especificacion dice que una cuenta
suspendida no emite tokens; comprobarlo en el servicio garantiza que el 401
generico salga tambien en ese caso, en vez del `account_not_active` que la
renovacion usa cuando el token ya venia emitido.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import User, UserStatus, normalizar_email
from ticket_api.security.passwords import hash_password, verify_password


def _credenciales_invalidas() -> ProblemException:
    """401 generico, identico para todos los fallos del login."""
    return ProblemException(
        status=401,
        code=ErrorCode.INVALID_CREDENTIALS,
        title="Credenciales invalidas",
        detail="El correo o la contrasena no son correctos.",
    )


@lru_cache(maxsize=1)
def _hash_senuelo() -> str:
    """Hash de una contrasena que nadie conoce, para gastar el mismo tiempo.

    Se calcula una vez por proceso: el coste de un Argon2id de mas no se nota
    frente al de la peticion, y guardarlo en una constante obligaria a pegar en
    el codigo un hash que caduca cuando cambien los parametros.
    """
    return hash_password("senuelo-de-tiempo-constante-sin-uso")


def _por_correo(sesion: Session, email: str) -> User | None:
    return sesion.scalar(select(User).where(User.email == normalizar_email(email)))


def autenticar(unidad: UnitOfWork, email: str, password: str) -> User:
    """Devuelve la cuenta activa con esas credenciales, o falla con 401.

    No confirma ni escribe nada: abrir la sesion es responsabilidad de quien
    llama, que es quien sabe si el login debe emitir uno o varios tokens.
    """
    usuario = _por_correo(unidad.sesion, email)
    if usuario is None:
        # Se verifica contra el senuelo para que este camino tarde lo mismo que
        # el de una cuenta que si existe.
        verify_password(_hash_senuelo(), password)
        raise _credenciales_invalidas()

    if not verify_password(usuario.password_hash, password):
        raise _credenciales_invalidas()

    if usuario.status != UserStatus.ACTIVE.value:
        raise _credenciales_invalidas()

    return usuario


__all__ = ["autenticar"]
