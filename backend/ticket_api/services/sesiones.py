"""Emision, renovacion y revocacion de sesiones.

La sesion es la fila de `auth_sessions`, no el token. Cada renovacion crea una
fila nueva y apunta a la anterior, de modo que la cadena completa queda
registrada y se puede responder a dos preguntas que el token por si solo no
contesta: *este refresh ya se uso* y *de que cadena viene*.

Las tres reglas que hacen que esto sea seguro, y por que estan aqui y no en la
vista:

**Cada renovacion rota el token.** Un refresh que sirve mas de una vez es un
refresh robado, porque el que lo robo y el dueño lo presentan a la vez. Al rotar,
el segundo en llegar recibe un token revocado y el sistema lo detecta.

**La reutilizacion revoca la cadena, no solo el token.** Si alguien reutiliza un
token viejo, no se sabe cual de los dos en conflicto es el atacante. Revocar la
familia entera es la unica respuesta que no deja al atacante dentro ni expulsa
sin querer a la victima: las dos cosas a la vez.

**Un `IntegrityError` nunca sale como exito.** Todo el trabajo -marcar el
anterior, crear el nuevo, confirmar- va en la misma transaccion que emite el
token. Si confirmara y la escritura fallara, el cliente tendria un token
imposible de revocar sin tocar la base a mano.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ticket_api.config import Settings
from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import AuthSession, User, UserStatus
from ticket_api.security.tokens import (
    EmisorDeTokens,
    Reloj,
    hash_de_refresh,
    nuevo_refresh_token,
)


@dataclass(frozen=True, slots=True)
class TokensEmitidos:
    """Lo que se devuelve a quien acaba de autenticarse o renovar.

    El `refresh` viaja en claro y hay que devolverlo: es la mitad de la promesa.
    Lo que no se devuelve nunca es el hash, que es como se guarda.
    """

    sesion_id: uuid.UUID
    acceso: str
    acceso_expira_en: datetime
    refresh: str
    refresh_expira_en: datetime


def _no_autenticado(codigo: ErrorCode, titulo: str, detalle: str) -> ProblemException:
    """Problema 401 con el codigo estable que el cliente necesita.

    Todos los fallos de autenticacion comparten forma y se distinguen por `code`.
    No es decoracion: la web usa `refresh_token_reused` para cerrar la sesion
    local y `invalid_credentials` para mostrar un formulario.
    """
    return ProblemException(status=401, code=codigo, title=titulo, detail=detalle)


def _buscar_por_refresh(sesion: Session, refresh: str) -> AuthSession | None:
    return sesion.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == hash_de_refresh(refresh))
    )


class ServicioDeSesiones:
    """Emite, renueva y revoca sesiones de un usuario."""

    def __init__(self, unidad: UnitOfWork, ajustes: Settings, reloj: Reloj | None = None) -> None:
        self._unidad = unidad
        self._tokens = EmisorDeTokens(ajustes, reloj)
        self._reloj = reloj if reloj is not None else (lambda: datetime.now(UTC))

    def _ahora(self) -> datetime:
        momento = self._reloj()
        return momento if momento.tzinfo is not None else momento.replace(tzinfo=UTC)

    def _crear_sesion(
        self, usuario: User, familia: uuid.UUID, anterior: AuthSession | None
    ) -> tuple[AuthSession, str]:
        """Fila nueva con un refresh nuevo, y el token en claro, en la misma unidad."""
        refresh = nuevo_refresh_token()
        registro = AuthSession(
            # El id se pone aqui y no se deja que lo ponga el `flush`. El vinculo
            # `replaced_by_id` se escribe antes de confirmar, y un id que todavia
            # no existe -el Python lo genera al insertar- lo escribiria como
            # `NULL` y la deteccion de reutilizacion dejaria de funcionar sin
            # que nada fallara.
            id=uuid.uuid4(),
            user_id=usuario.id,
            refresh_token_hash=hash_de_refresh(refresh),
            family_id=familia,
            replaced_by_id=None,
            expires_at=self._ahora() + self._tokens.vida_de_refresh,
        )
        self._unidad.sesion.add(registro)
        # El `INSERT` va antes que el `UPDATE` del vinculo: la clave foranea de
        # `replaced_by_id` exige que la fila nueva exista, y el ORM no ordena las
        # dos escrituras por si solo porque no hay ninguna relacion que declare
        # esa dependencia. Sin este `flush`, la base rechaza la rotacion con una
        # violacion de clave foranea.
        self._unidad.sesion.flush()
        if anterior is not None:
            # El vinculo se escribe en los dos sentidos: `replaced_by_id` es lo
            # que permite detectar la reutilizacion, y la sesion anterior es la
            # que hay que revocar al hacerlo.
            anterior.replaced_by_id = registro.id
            anterior.revoked_at = self._ahora()
        return registro, refresh

    def _emitir(self, usuario: User, registro: AuthSession, refresh: str) -> TokensEmitidos:
        acceso, expira = self._tokens.firmar_acceso(usuario.id, usuario.role, registro.id)
        return TokensEmitidos(
            sesion_id=registro.id,
            acceso=acceso,
            acceso_expira_en=expira,
            refresh=refresh,
            refresh_expira_en=registro.expires_at,
        )

    def abrir(self, usuario: User) -> TokensEmitidos:
        """Sesion nueva para un usuario recien autenticado.

        La comprobacion de `active` esta aqui y no en la vista, porque 3.6 la
        necesita para login y esta para una renovacion: dejarla en la vista la
        haria presente una vez de cada dos.
        """
        if usuario.status != UserStatus.ACTIVE.value:
            raise _no_autenticado(
                ErrorCode.ACCOUNT_NOT_ACTIVE,
                "Cuenta no activa",
                "La cuenta no esta activa.",
            )
        registro, refresh = self._crear_sesion(usuario, familia=uuid.uuid4(), anterior=None)
        self._unidad.commit()
        return self._emitir(usuario, registro, refresh)

    def renovar(self, refresh: str) -> TokensEmitidos:
        """Rota el refresh y emite un token de acceso nuevo.

        La sesion anterior se marca como reemplazada dentro de esta misma
        transaccion: si la creacion de la nueva fallara, la anterior seguiria
        viva y el cliente tendria un refresh que ya no puede usar.
        """
        ahora = self._ahora()
        anterior = _buscar_por_refresh(self._unidad.sesion, refresh)
        if anterior is None:
            raise _no_autenticado(
                ErrorCode.INVALID_CREDENTIALS,
                "Refresh token invalido",
                "El refresh token no corresponde a ninguna sesion.",
            )

        if anterior.esta_revocada:
            self._detectar_reutilizacion(anterior)
        if anterior.esta_vencida(ahora):
            raise _no_autenticado(
                ErrorCode.SESSION_REVOKED,
                "Sesion caducada",
                "La sesion ha caducado. Inicia sesion de nuevo.",
            )

        usuario = self._unidad.sesion.get(User, anterior.user_id)
        if usuario is None or usuario.status != UserStatus.ACTIVE.value:
            # Una cuenta suspendida o borrada no renueva. Se cierra la cadena
            # entera en vez de solo este eslabon: dejarla abierta permitiria
            # reintentar con el siguiente token de la misma familia.
            self._revocar_familia(anterior.family_id, ahora)
            self._unidad.commit()
            raise _no_autenticado(
                ErrorCode.ACCOUNT_NOT_ACTIVE,
                "Cuenta no activa",
                "La cuenta no esta activa.",
            )

        nuevo, refresh_nuevo = self._crear_sesion(
            usuario, familia=anterior.family_id, anterior=anterior
        )
        self._unidad.commit()
        return self._emitir(usuario, nuevo, refresh_nuevo)

    def revocar(self, refresh: str) -> bool:
        """Cierre de sesion explicito. Devuelve si habia algo que revocar.

        No lanza si el refresh no existe: cerrar sesion dos veces no es un error,
        y un 401 en el segundo cierre obligaria al navegador a distinguir entre
        "cerrado" y "no habia nada", que es el mismo estado. El segundo cierre
        devuelve `False` porque la sesion ya estaba revocada, y no porque no se
        haya encontrado.
        """
        ahora = self._ahora()
        registro = _buscar_por_refresh(self._unidad.sesion, refresh)
        if registro is None:
            return False
        cambio = registro.revocar(ahora)
        self._unidad.commit()
        return cambio

    def _detectar_reutilizacion(self, anterior: AuthSession) -> NoReturn:
        """Revoca la familia entera y falla.

        Se llega aqui con una sesion revocada. Si fue un cierre de sesion
        normal, la cadena ya esta muerta y el error que toca es "sesion
        revocada". Si tiene sucesion, es que alguien esta reutilizando un token
        ya rotado, y la respuesta no es un 401 mas: es cortar la cadena entera.
        """
        ahora = self._ahora()
        if anterior.replaced_by_id is not None:
            self._revocar_familia(anterior.family_id, ahora)
            self._unidad.commit()
            raise _no_autenticado(
                ErrorCode.REFRESH_TOKEN_REUSED,
                "Refresh token reutilizado",
                "Se ha detectado la reutilizacion de un refresh token. "
                "La sesion se ha cerrado.",
            )
        raise _no_autenticado(
            ErrorCode.SESSION_REVOKED,
            "Sesion revocada",
            "La sesion se cerro. Inicia sesion de nuevo.",
        )

    def _revocar_familia(self, familia: uuid.UUID, ahora: datetime) -> None:
        """Cierra todos los eslabones vivos de una cadena.

        Se hace con una sentencia y no recorriendo filas ya cargadas: la familia
        puede tener mas eslabones de los que esta sesion conoce, y una sesion
        que se queda abierta despues de detectar una reutilizacion seria
        precisamente el agujero que se acaba de abrir.
        """
        self._unidad.sesion.execute(
            update(AuthSession)
            .where(AuthSession.family_id == familia, AuthSession.revoked_at.is_(None))
            .values(revoked_at=ahora)
        )


#: El servicio se construye por peticion y no es un singleton: lleva la unidad de
#: trabajo, que es por peticion. Lo que si es del proceso es el reloj y la clave
#: de firma, y los dos viven dentro de `EmisorDeTokens`.
__all__ = ["ServicioDeSesiones", "TokensEmitidos"]
