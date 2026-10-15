"""Tokens de acceso firmados y refresh tokens opacos.

Dos mecanismos distintos para dos problemas distintos:

- El **token de acceso** es un JWT Ed25519 de vida corta. Lo porta el cliente en
  cada peticion y por eso tiene que ser pequeno y no consultar la base. Se
  **valida por firma**, y por eso no se puede revocar por si solo: mientras no
  expire sigue valiendo aunque se revoque la sesion. De ahi la vida corta.
- El **refresh token** es un valor opaco de 256 bits que solo la base conoce a
  traves de su SHA-256. Se_presenta una vez por renovacion, asi que su unico
  trabajo es permitir que la API consulte la sesion y decida.

**Por que Ed25519 y no RS256.** Firma de 64 bytes y verificacion en un solo
paso, frente a las miles de operaciones modulares de RSA de tamaño equivalente.
Con RSA se acaba eligiendo una clave pequena para no pagar la CPU, y ahi empieza
el problema. Ed25519 no obliga a elegir.

**Por que el hash del refresh es SHA-256 y no Argon2id.** Es el mismo argumento
que en `models/identity.py`: Argon2id protege de un atacante que *adivina* la
contrasena, y aqui no hay nada que adivinar. El token tiene 256 bits de entropia
elegidos por el servidor. Un hash rapido con su indice es lo correcto, y el coste
de CPU de Argon2id aqui solo se pagaria en cada renovacion.

**Por que no hay `iss` ni `aud`.** El diseno fija el payload en `sub`, `role`,
`sid`, `iat`, `exp` y `jti`, y no se cambia aqui. No es una omision: esta API es
la unica que firma y la unica que verifica, asi que no hay ambito al que un token
debe pertenecer. Anadir `aud` seria defensa contra una arquitectura que todavia
no existe, y que se podria anadir sin romper lo emitido.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Final

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ticket_api.config import Settings

#: Fuente del "ahora". Inyectarla permite probar caducidades sin esperar y sin
#: que un `sleep` haga la suite lenta o fragile.
Reloj = Callable[[], datetime]

#: Identificador del algoritmo en la cabecera JWT. Fijo, no configurable: permitir
#: elegirlo por entorno es permitir que alguien despliegue `none`.
ALGORITMO: Final = "EdDSA"

#: Bytes de entropia del refresh token. 32 bytes = 256 bits, mas de lo que
#: permite adivinar un valor aunque la base este expuesta entera y el atacante
#: sepa el algoritmo de derivacion.
REFRESH_ENTROPY: Final = 32

#: Claims que no pueden faltar en un token firmado por esta API. Un token sin
#: `sid` no se puede contrastar con una sesion, y sin `exp` no caduca: aceptarlo
#: seria aceptar un token valido para siempre.
CLAIMS_REQUERIDAS: Final[tuple[str, ...]] = ("sub", "role", "sid", "iat", "exp", "jti")


class ErrorDeToken(ValueError):
    """El token no se puede aceptar. La razon no se distingue hacia fuera."""


class TokenExpiradoError(ErrorDeToken):
    """El token es correcto y ya no sirve.

    Se distingue de `ErrorDeToken` para registrarlo distinto: un caducado es
    senal de que el cliente esta haciendo su trabajo, y uno invalido puede ser un
    intento o una clave cambiada sin avisar.
    """


@dataclass(frozen=True, slots=True)
class ClaimsAcceso:
    """Lo que el token de acceso afirma, ya validado."""

    usuario_id: uuid.UUID
    rol: str
    sesion_id: uuid.UUID
    emitido_en: datetime
    expira_en: datetime
    jti: str


def nuevo_refresh_token() -> str:
    """Valor opaco de alta entropia.

    `token_urlsafe` y no `hexdigest`: el token viaja en una cabecera y en un
    formulario, y base64url no necesita escapes.
    """
    return secrets.token_urlsafe(REFRESH_ENTROPY)


def hash_de_refresh(token: str) -> str:
    """SHA-256 en hexadecimal del refresh token.

    Es lo unico que se guarda. La base de datos no sirve para suplantar a nadie
    con ella, aunque este expuesta.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@lru_cache(maxsize=4)
def _clave_privada(base64_der: str) -> Ed25519PrivateKey:
    """Carga la clave de firma y la cachea por valor.

    Cachear por valor y no "la clave del proceso" es lo que permite que las
    pruebas usen la suya sin que una se pise con otra, y que un despliegue que
    rota la clave en caliente no tenga que reiniciar para notar el cambio.
    """
    try:
        crudo = base64.b64decode(base64_der, validate=True)
    except (binascii.Error, ValueError) as fallo:
        raise ErrorDeToken("la clave de firma no es base64 valido") from fallo
    try:
        clave = serialization.load_der_private_key(crudo, password=None)
    except (ValueError, TypeError) as fallo:
        raise ErrorDeToken("la clave de firma no es una clave privada PKCS#8 legible") from fallo
    if not isinstance(clave, Ed25519PrivateKey):
        raise ErrorDeToken("la clave de firma no es Ed25519")
    return clave


def clave_de_firma(ajustes: Settings) -> Ed25519PrivateKey:
    """Clave privada de firma del proceso."""
    return _clave_privada(ajustes.jwt_private_key_b64)


class EmisorDeTokens:
    """Firma y valida tokens de acceso, y mide lo que viven.

    Solo criptografia: no toca la base ni sabe que existe una sesion. Eso lo
    hace el servicio, y por eso se puede probar con un reloj False sin levantar
    PostgreSQL.
    """

    def __init__(self, ajustes: Settings, reloj: Reloj | None = None) -> None:
        self._ajustes = ajustes
        self._reloj = reloj if reloj is not None else (lambda: datetime.now(UTC))
        self._clave = clave_de_firma(ajustes)

    @property
    def vida_de_acceso(self) -> timedelta:
        return timedelta(seconds=self._ajustes.access_token_ttl_seconds)

    @property
    def vida_de_refresh(self) -> timedelta:
        return timedelta(seconds=self._ajustes.refresh_token_ttl_seconds)

    def _ahora(self) -> datetime:
        momento = self._reloj()
        # Un `datetime` naive no se puede convertir a marca de tiempo sin que
        # `timestamp()` asuma horario local, y el resultado seria un token
        # caducado o vigente segun donde corra el proceso. Se fija UTC.
        return momento if momento.tzinfo is not None else momento.replace(tzinfo=UTC)

    def firmar_acceso(
        self, usuario_id: uuid.UUID, rol: str, sesion_id: uuid.UUID
    ) -> tuple[str, datetime]:
        """Devuelve el token y la fecha en que caduca."""
        ahora = self._ahora()
        expira = ahora + self.vida_de_acceso
        payload: dict[str, Any] = {
            "sub": str(usuario_id),
            "role": rol,
            "sid": str(sesion_id),
            "iat": int(ahora.timestamp()),
            "exp": int(expira.timestamp()),
            "jti": str(uuid.uuid4()),
        }
        token = jwt.encode(payload, self._clave, algorithm=ALGORITMO)
        return token, expira

    def verificar_acceso(self, token: str) -> ClaimsAcceso:
        """Valida firma y caducidad, y devuelve lo que el token afirma.

        `verify_signature` con una lista de un solo algoritmo, no el que venga en
        la cabecera: aceptar el que el token dice es la forma clasica de que un
        token valide con `none` o con un algoritmo asimetrico.

        La caducidad se comprueba aqui y no en la biblioteca, con el reloj de
        este emisor y no con el de la maquina. Es lo que hace que "emitir" y
        "verificar" compartan la misma nocion de ahora: si la caducidad la
        midiera el reloj real, un token emitido con el reloj inyectado -que es
        como se prueban los quince minutos de vida- se veria caducado o vigente
        segun la hora a la que corra la suite.
        """
        try:
            payload = jwt.decode(
                token,
                self._clave.public_key(),
                algorithms=[ALGORITMO],
                options={"require": list(CLAIMS_REQUERIDAS), "verify_exp": False},
            )
        except jwt.InvalidTokenError as fallo:
            raise ErrorDeToken("el token de acceso no es valido") from fallo

        try:
            claims = ClaimsAcceso(
                usuario_id=uuid.UUID(payload["sub"]),
                rol=payload["role"],
                sesion_id=uuid.UUID(payload["sid"]),
                emitido_en=datetime.fromtimestamp(payload["iat"], tz=UTC),
                expira_en=datetime.fromtimestamp(payload["exp"], tz=UTC),
                jti=payload["jti"],
            )
        except (KeyError, ValueError, TypeError, OverflowError, OSError) as fallo:
            # Firma correcta con claims mal formados: un token que alguien ha
            # firmado con nuestra clave y un payload roto. No es un token nuestro.
            raise ErrorDeToken("el token de acceso tiene claims ilegibles") from fallo

        if claims.expira_en <= self._ahora():
            raise TokenExpiradoError("el token de acceso caduco")
        return claims
