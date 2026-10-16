"""Identidad de quien hace la peticion y reglas de autorizacion.

**Por que hay un `Actor` y no basta el token.** El token lleva el rol, pero la
peticion protegida no se fia de el: vuelve a mirar la sesion y la cuenta. Un
`event-owner` suspendido conserva su token de acceso en vigor hasta que este
corta -quince minutos-, y "una suspension tiene efecto inmediato" no se cumple
comprobando solo el JWT.

**Por que el rol se lee de la base y no del token.** Por lo mismo: si un rol
cambia, tiene que cambiar en la peticion siguiente, no en el proximo login.

**Por que `exigir_duenno` no devuelve un 404.** Denegar el acceso a un evento
ajeno respondiendo "no existe" confirmaria que existe. Se responde 403 con un
codigo propio, `forbidden_not_owner`, que la web puede distinguir de un 403 por
rol sin que la respuesta revele nada del recurso.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from ticket_api.config import Settings
from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import AuthSession, Role, User, UserStatus
from ticket_api.security.tokens import EmisorDeTokens, ErrorDeToken


@dataclass(frozen=True, slots=True)
class Actor:
    """Quien hace la peticion, ya comprobado contra la base."""

    usuario_id: uuid.UUID
    rol: Role
    sesion_id: uuid.UUID

    def es_uno_de(self, *roles: Role) -> bool:
        return self.rol in roles

    def es_duenno(self, duenno_id: uuid.UUID) -> bool:
        return self.usuario_id == duenno_id

    def exigir_rol(self, *roles: Role) -> None:
        """Deja pasar solo si el actor tiene alguno de los roles indicados.

        Se acepta mas de uno para no encadenar dependencias: `admin` tambien
        tiene acceso a lo del proveedor, y decidir eso en un solo sitio evita
        que dos rutas lo decidan distinto.
        """
        if not self.es_uno_de(*roles):
            raise ProblemException(
                status=403,
                code=ErrorCode.FORBIDDEN,
                title="Acceso denegado",
                detail="Tu rol no permite esta operacion.",
            )

    def exigir_duenno(self, duenno_id: uuid.UUID) -> None:
        """Deja pasar solo si el actor es el duenno del recurso.

        Un `admin` tambien tiene que poder operar sobre recursos ajenos, asi que
        se comprueba aqui el caso general y lo llama a la ruta: quien decide si
        el administrador esquiva el ownership es la ruta, no este metodo.
        """
        if not self.es_duenno(duenno_id):
            raise ProblemException(
                status=403,
                code=ErrorCode.FORBIDDEN_NOT_OWNER,
                title="Acceso denegado",
                detail="El recurso no pertenece a tu cuenta.",
            )


def _no_autenticado(codigo: ErrorCode, detalle: str) -> ProblemException:
    return ProblemException(status=401, code=codigo, title="No autenticado", detail=detalle)


def identidad_actual(unidad: UnitOfWork, token: str, ajustes: Settings) -> Actor:
    """Devuelve el actor del token, o falla con 401.

    El mensaje no distingue entre token inexistente, firma invalida, sesion
    caducada y cuenta suspendida: quien no puede autenticarse no necesita saber
    por que, y un 401 mas especifico solo ayuda a probar tokens.
    """
    try:
        claims = EmisorDeTokens(ajustes).verificar_acceso(token)
    except ErrorDeToken as fallo:
        raise _no_autenticado(
            ErrorCode.UNAUTHENTICATED, "El token de acceso no es valido."
        ) from fallo

    ahora = datetime.now(UTC)
    registro = unidad.sesion.get(AuthSession, claims.sesion_id)
    # Una sesion que no existe o que pertenece a otra cuenta es un token
    # fabricado con una firma valida; en los dos casos el 401 es el mismo.
    if registro is None or registro.user_id != claims.usuario_id:
        raise _no_autenticado(
            ErrorCode.UNAUTHENTICATED, "El token de acceso no corresponde a ninguna sesion."
        )
    if registro.esta_revocada or registro.esta_vencida(ahora):
        raise _no_autenticado(
            ErrorCode.SESSION_REVOKED, "La sesion ha caducado. Inicia sesion de nuevo."
        )

    usuario = unidad.sesion.get(User, claims.usuario_id)
    if usuario is None or usuario.status != UserStatus.ACTIVE.value:
        raise _no_autenticado(
            ErrorCode.ACCOUNT_NOT_ACTIVE, "La cuenta no esta activa."
        )

    return Actor(usuario_id=usuario.id, rol=Role(usuario.role), sesion_id=registro.id)


__all__ = ["Actor", "identidad_actual"]
