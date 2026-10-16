"""Contratos Pydantic v2 de las peticiones y respuestas de `/api/v1`.

Solo viven aqui los cuerpos que cruzan el borde HTTP. Los modelos de SQLAlchemy
no se usan como contrato: mezclarlos obliga al cliente a conocer el esquema de
la base y filtra campos que no tienen por que viajar -el hash de la contrasena
es el caso obvio-.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from ticket_api.models import Role, UserStatus

#: Roles que el registro publico acepta. `admin` no esta y no debe añadirse:
#: una cuenta de administrador se crea desde la administracion, no desde un
#: formulario abierto a internet.
#:
#: Son cadenas y no miembros de `Role` a proposito. Un `Literal` de miembros de
#: un `StrEnum` pone el `repr` de Python en el mensaje de error de Pydantic
#: ("Input should be <Role.CLIENT: 'client'>"), y ese texto va a parar al
#: formulario del navegador.
RolPublico = Literal["client", "event-owner"]

#: Estado con el que nace cada rol. El cliente entra directamente; el proveedor
#: tiene que pasar por la aprobacion de 3.8. Que la regla este aqui y no en la
#: vista es lo que impide que un endpoint nuevo la aplique por su cuenta.
ESTADO_INICIAL: dict[Role, UserStatus] = {
    Role.CLIENT: UserStatus.ACTIVE,
    Role.EVENT_OWNER: UserStatus.PENDING,
}

#: Longitudes de la contrasena. El minimo evita que se registre con una
#: contrasena de un caracter, y el maximo acota lo que hay que hashear: el
#: Argon2id de esta instalacion cuesta lo mismo para 12 caracteres que para
#: 1000, asi que no hay razon para aceptar mas.
PASSWORD_MINIMO: int = 12
PASSWORD_MAXIMO: int = 128


class RegistroRequest(BaseModel):
    """Cuerpo de `POST /api/v1/auth/register`.

    `extra="forbid"` no es un capricho de rigor: un campo de mas en un formulario
    de registro es casi siempre un campo interno olvidado, y aceptarlo en
    silencio esconde ese error hasta que alguien lo encuentra en los datos
    guardados.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="Correo de la cuenta. Se normaliza antes de guardarse.")
    password: str = Field(
        min_length=PASSWORD_MINIMO,
        max_length=PASSWORD_MAXIMO,
        description="Contrasena en texto claro. Se hashea y no se guarda ni se registra.",
    )
    role: RolPublico = Field(
        default=Role.CLIENT.value,
        description="Rol pedido en el registro publico. `admin` no es registrable.",
    )


class UsuarioResponse(BaseModel):
    """Como se ve una cuenta por fuera.

    No lleva `password_hash`. El hash no es un dato que el cliente necesite para
    nada, y devolverlo convertiria cada respuesta de la API en una copia de la
    tabla de contrasenas.
    """

    id: uuid.UUID
    email: str
    role: str
    status: str
    created_at: datetime


class LoginRequest(BaseModel):
    """Cuerpo de `POST /api/v1/auth/login`.

    La contrasena no lleva minimo de longitud, a diferencia del registro. Un 422
    por "contrasena demasiado corta" contaria la politica de contrasenas a quien
    prueba una a una, y ademas revelaria que ese correo pasa la validacion antes
    de comprobar la credencial. Aqui cualquier fallo tiene que ser el mismo 401.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="Correo de la cuenta. Se normaliza antes de buscarlo.")
    password: str = Field(description="Contrasena en texto claro. No se registra en ningun log.")


class RefreshRequest(BaseModel):
    """Cuerpo de `POST /api/v1/auth/refresh` y de `POST /api/v1/auth/logout`."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(
        min_length=1,
        description="Refresh token opaco emitido por login o por una renovacion anterior.",
    )


class TokensResponse(BaseModel):
    """Par de tokens que devuelve login y refresh.

    `token_type` viaja como campo y con valor fijo en vez de dejarse al cliente:
    es lo que espera un cliente OAuth2 y evita que cada integracion lo asuma.
    """

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - no es una credencial, es el esquema OAuth2
    expires_in: int = Field(description="Segundos que le quedan al token de acceso.")
    refresh_token: str = Field(
        description="Token opaco de renovacion. No se guarda en claro: solo su hash."
    )
