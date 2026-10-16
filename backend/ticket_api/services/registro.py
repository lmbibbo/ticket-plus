"""Registro de cuentas.

La regla que decide el estado inicial de una cuenta vive aqui y no en la vista:
es la unica razon por la que un proveedor nace `pending` y un cliente nace
`active`, y duplicarla por endpoint seria la forma corta de que uno de los dos
se quede sin ella.

Por que el email se comprueba dos veces
---------------------------------------
Primero se busca, para poder responder con un error de campo -que es lo que el
formulario sabe pintar- y para no gastar 64 MiB de Argon2id en un registro que
va a fallar. Y despues se deja que la base decida, porque entre la busqueda y
el `INSERT` cabe otra peticion con el mismo correo. La segunda comprobacion no es
redundante: es la unica que sobrevive a la carrera, y `uq_users_email` es la que
hace que la segunda persona reciba un error y no una cuenta duplicada.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import FieldError, ProblemException
from ticket_api.models import Role, User, normalizar_email
from ticket_api.schemas.identity import ESTADO_INICIAL, RegistroRequest
from ticket_api.security.passwords import hash_password

#: Restriccion que traduce el conflicto de correo a un error de campo. El nombre
#: se compara con el de la base y no con el texto del error de PostgreSQL, que
#: cambia entre versiones y ademas sale traducido al idioma de la instalacion.
RESTRICCION_EMAIL: str = "uq_users_email"

#: Mensaje del campo. Confirma que el correo esta en uso, que es lo que pide la
#: especificacion y lo que la demo de F2 muestra como error de campo. Elmitirlo
#: -respondiendo siempre "si el registro se completo"- obligaria al solicitante a
#: descubrir el conflicto con un correo de recuperacion, y ese camino no existe en
#: este cambio. Que el mensaje sea el mismo para un duplicado y para un
#: registro correcto es lo que evita el atacante que prueba correos ajenos.
DETALLE_EMAIL_DUPLICADO: str = "Ese correo ya esta registrado."


def _email_duplicado() -> ProblemException:
    """Problema 409 con el error anclado al campo `email`.

    Un 409 y no un 422: el correo es valido, lo que choca es que ya existe. La
    web mapea `email_already_registered` al campo del formulario.
    """
    return ProblemException(
        status=409,
        code=ErrorCode.EMAIL_ALREADY_REGISTERED,
        title="Correo ya registrado",
        detail=DETALLE_EMAIL_DUPLICADO,
        errors=[FieldError(field="email", message=DETALLE_EMAIL_DUPLICADO)],
    )


def _es_conflicto_de_email(fallo: IntegrityError) -> bool:
    """Distingue la violacion de `uq_users_email` de cualquier otra integridad.

    Un `IntegrityError` que no sea ese -un `NOT NULL`, una referencia rota- es un
    fallo del servidor y tiene que llegar como 500: disfrazarlo de correo
    duplicado esconde el defecto real y hace que se reintente sin causa.
    """
    diagnostico = getattr(fallo.orig, "diag", None)
    return diagnostico is not None and diagnostico.constraint_name == RESTRICCION_EMAIL


def _rol_y_estado(role: Role) -> str:
    """Estado con el que nace un rol. `KeyError` significa un rol nuevo sin regla."""
    return ESTADO_INICIAL[role].value


def _email_ya_registrado(sesion: Session, email: str) -> bool:
    """Busca una cuenta con ese correo.

    Va aparte de `registrar` para que las pruebas puedan anularla y llegar a la
    rama de la carrera, que de otro modo no se puede provocar en serie.
    """
    return sesion.scalar(select(User.id).where(User.email == email)) is not None


def comprobar_y_confirmar(unidad: UnitOfWork, usuario: User) -> User:
    """Anade una cuenta ya construida y la confirma.

    Es el unico camino por el que se inserta en `users`, tambien para el
    administrador inicial de `ticket_api.cli`. Que el conflicto de correo se
    traduzca al mismo 409 en los dos casos importa: la consola informa con el
    mismo texto que el registro, y no hace falta probar dos veces lo mismo.
    """
    if _email_ya_registrado(unidad.sesion, usuario.email):
        raise _email_duplicado()

    unidad.sesion.add(usuario)
    try:
        unidad.commit()
    except IntegrityError as fallo:
        # Tras un error de integridad la transaccion esta muerta: sin este
        # rollback, el siguiente `flush` de la misma peticion falla con
        # "current transaction is aborted" y el error real se pierde.
        unidad.rollback()
        if _es_conflicto_de_email(fallo):
            raise _email_duplicado() from fallo
        raise
    return usuario


def registrar(unidad: UnitOfWork, datos: RegistroRequest) -> User:
    """Crea la cuenta y confirma.

    El `commit` va aqui y no en la vista porque es el unico sitio donde se sabe
    que la operacion es completa. Si lo dejara para despues, una violacion de
    `uq_users_email` llegaria al cliente como un 201 con una cuenta que no
    existe.
    """
    rol = Role(datos.role)
    return comprobar_y_confirmar(
        unidad,
        User(
            email=normalizar_email(datos.email),
            password_hash=hash_password(datos.password),
            role=rol.value,
            status=_rol_y_estado(rol),
        ),
    )
