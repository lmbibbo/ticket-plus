"""Creacion de la primera cuenta de administrador.

**Por que hace falta.** El registro publico de 3.4 no admite el rol `admin` a
proposito: un formulario abierto a internet que puede crear administradores no es
un registro, es una Puerta abierta. Pero sin alguna via de arranque, la
administracion de 3.8 no tendria contra quien probar ni quien la usaria la primera
vez, y el unico camino que queda seria editar la base a mano.

**Por que es una consola y no una ruta.** Una ruta mas, con su dependencia, que
daria de administracion al primero que llegue. Esta solo corre con acceso a la
maquina y a la configuracion -un despliegue, un `docker exec`- y quien ya tiene
lo que hace falta.

**Por que pide la contrasena y no la genera.** Una contrasena inventada que se
imprime en un log de arranque acaba en un historial de terminal compartido. Quien
la elige es quien la va a recordar.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from getpass import getpass

from ticket_api.config import get_settings
from ticket_api.db.engine import get_sessionmaker
from ticket_api.db.session import UnitOfWork
from ticket_api.errors.problem import ProblemException
from ticket_api.models import Role, User, UserStatus, normalizar_email
from ticket_api.schemas.identity import PASSWORD_MAXIMO, PASSWORD_MINIMO
from ticket_api.security.passwords import hash_password
from ticket_api.services.registro import comprobar_y_confirmar

COMANDO = "crear-admin"


def _validar_contrasena(contrasena: str) -> None:
    if not PASSWORD_MINIMO <= len(contrasena) <= PASSWORD_MAXIMO:
        raise SystemExit(
            f"La contrasena debe tener entre {PASSWORD_MINIMO} y {PASSWORD_MAXIMO} caracteres."
        )


def crear_admin(unidad: UnitOfWork, email: str, contrasena: str) -> User:
    """Crea la cuenta de administrador. Falla si el correo ya esta en uso.

    Reutiliza la comprobacion de duplicado del registro en vez de repetirla: el
    indice unico de `users` es el mismo y dos comprobaciones distintas del mismo
    email solo pueden discrepar.
    """
    _validar_contrasena(contrasena)
    return comprobar_y_confirmar(
        unidad,
        User(
            email=normalizar_email(email),
            password_hash=hash_password(contrasena),
            role=Role.ADMIN.value,
            # Nace `active` y no `pending`: no hay nadie que pueda aprobar al
            # primer administrador, y una cadena de aprobaciones para el primer
            # usuario deja el sistema sin administrador hasta que alguien se la
            # salta.
            status=UserStatus.ACTIVE.value,
        ),
    )


def _parser() -> argparse.ArgumentParser:
    raiz = argparse.ArgumentParser(
        prog="python -m ticket_api.cli",
        description="Utilidades de arranque que no son rutas de la API.",
    )
    comandos = raiz.add_subparsers(dest="comando", required=True)
    crear = comandos.add_parser(
        COMANDO,
        help="Crea la primera cuenta de administrador.",
        description="Crea una cuenta de administrador activa.",
    )
    crear.add_argument("email", help="Correo de la cuenta de administrador.")
    crear.add_argument(
        "--contrasena",
        help=(
            "Contrasena. Si se omite, se pide sin eco. Preferible: un argumento "
            "en la linea de comandos queda en el historial del shell."
        ),
    )
    return raiz


def main(argumentos: Sequence[str] | None = None) -> int:
    opciones = _parser().parse_args(argumentos)
    contrasena = opciones.contrasena or getpass("Contrasena del administrador: ")
    _validar_contrasena(contrasena)

    get_settings()  # Falla aqui, y no al abrir la base, si falta configuracion.
    unidad = UnitOfWork(get_sessionmaker()())
    try:
        try:
            usuario = crear_admin(unidad, opciones.email, contrasena)
        except ProblemException as problema:
            print(f"No se pudo crear el administrador: {problema.detail}", file=sys.stderr)
            return 1
    finally:
        unidad.rollback()
        unidad.sesion.close()
    print(f"Administrador creado: {usuario.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
