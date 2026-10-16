"""Rutas de `/api/v1/auth`.

El router no decide nada de dominio: valida el cuerpo con Pydantic, pide la
unidad de trabajo, llama al servicio y traduce a la respuesta publica. Las reglas
-que rol puede registrarse, con que estado nace, que es un correo duplicado, que
solo las cuentas activas emiten tokens- estan en `ticket_api.services`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from ticket_api.api.deps import SettingsDep
from ticket_api.api.proyecciones import usuario_publico
from ticket_api.config import Settings
from ticket_api.db.session import DbUnit
from ticket_api.schemas.identity import (
    LoginRequest,
    RefreshRequest,
    RegistroRequest,
    TokensResponse,
    UsuarioResponse,
)
from ticket_api.services import registro
from ticket_api.services.autenticacion import autenticar
from ticket_api.services.sesiones import ServicioDeSesiones, TokensEmitidos

router = APIRouter(prefix="/api/v1/auth", tags=["identidad"])


def _a_tokens(emitido: TokensEmitidos, ajustes: Settings) -> TokensResponse:
    """Proyeccion del par de tokens a la respuesta publica.

    `expires_in` se cuenta desde la configuracion y no restando fechas: el valor
    publicado es la vida que el emisor dio al token, y calcularlo por diferencia
    redondearia a un segundo menos en cada respuesta.
    """
    return TokensResponse(
        access_token=emitido.acceso,
        expires_in=ajustes.access_token_ttl_seconds,
        refresh_token=emitido.refresh,
    )


@router.post(
    "/register",
    response_model=UsuarioResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registro publico de clientes y proveedores",
)
def registrar_cuenta(datos: RegistroRequest, unidad: DbUnit) -> UsuarioResponse:
    """Crea una cuenta a partir de un formulario publico.

    Un cliente nace activo y un proveedor nace pendiente de aprobacion. El rol
    `admin` no se puede pedir aqui: Pydantic lo rechaza antes de llegar al
    servicio, asi que no hay ninguna cuenta de administrador creada por esta via.
    """
    return usuario_publico(registro.registrar(unidad, datos))


@router.post(
    "/login",
    response_model=TokensResponse,
    summary="Inicio de sesion con correo y contrasena",
)
def iniciar_sesion(datos: LoginRequest, unidad: DbUnit, ajustes: SettingsDep) -> TokensResponse:
    """Devuelve tokens y abre una sesion.

    No dice si el correo existe ni si la cuenta esta aprobada: los tres fallos
    devolvieron el mismo 401 desde `autenticar`. Aqui solo se traduce lo emitido.
    """
    usuario = autenticar(unidad, datos.email, datos.password)
    emitido = ServicioDeSesiones(unidad, ajustes).abrir(usuario)
    return _a_tokens(emitido, ajustes)


@router.post(
    "/refresh",
    response_model=TokensResponse,
    summary="Renovacion del token de acceso",
)
def renovar_sesion(datos: RefreshRequest, unidad: DbUnit, ajustes: SettingsDep) -> TokensResponse:
    """Rota el refresh y emite un access token nuevo.

    Los 401 que puede devolver son distinguibles por `code` a proposito: la web
    usa `refresh_token_reused` para cerrar la sesion local y `session_revoked` para
    volver a pedir un login.
    """
    emitido = ServicioDeSesiones(unidad, ajustes).renovar(datos.refresh_token)
    return _a_tokens(emitido, ajustes)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cierre de sesion",
)
def cerrar_sesion(datos: RefreshRequest, unidad: DbUnit, ajustes: SettingsDep) -> Response:
    """Revoca el refresh y no dice si habia algo que revocar.

    Un 204 tambien cuando el token no existe. Al cerrar sesion no hay nada que
    el cliente pueda hacer con un error, y responder distinto revelaria que el
    token estaba bien formado: un atacante podria comprobar tokens probados
    hasta dar con uno vivo.
    """
    ServicioDeSesiones(unidad, ajustes).revocar(datos.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
