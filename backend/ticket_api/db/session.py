"""Unidad de trabajo: una peticion, una transaccion.

- Si la vista confirma, se escribe.
- Si la vista lanza, se revierte.
- Si la vista no dice nada, no se escribe.

Ese tercer caso es el que evita el fallo clasico: dar por confirmado porque la
peticion llego al final. Aqui la omision se revierte, no se confirma.

Por que la confirmacion la pide la vista y no un `finally` de la dependencia
----------------------------------------------------
La version obvia es poner `commit` despues del `yield`. No funciona: FastAPI
cierra las dependencias `yield` **despues de enviar la respuesta**, asi que una
violacion de clave ajena o un `NOT NULL` pendiente deían al cliente un 200 con
los datos sin guardar. La transaccion fallaba y nadie se enteraba hasta contar
los registros.

Con `commit` explicito, el fallo ocurre dentro de la vista, donde todavia se
puede responder con un 409. Por eso `UnitOfWork.commit()` es el unico que
confirma y el teardown solo revierte y cierra.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from ticket_api.db.engine import get_sessionmaker


class UnitOfWork:
    """Frontera de transaccion de una peticion.

    Envuelve una sesion en vez de exponerla: `commit` queda como la unica
    operacion que escribe, y no se puede llegar a la sesion sin pasar por ella.
    """

    def __init__(self, sesion: Session) -> None:
        self.sesion = sesion

    def commit(self) -> None:
        """Confirma. Lanza si la base rechaza el cambio.

        El error sube a la vista, que es donde puede convertirse en una
        respuesta util; no se traga aqui para no reportar exito con datos
        perdidos.
        """
        self.sesion.commit()

    def rollback(self) -> None:
        self.sesion.rollback()


def get_unit_of_work() -> Iterator[UnitOfWork]:
    """Unidad de trabajo ligada a la peticion.

    El teardown revierte lo que quedo sin confirmar y cierra la sesion. Se hace
    en un `finally` y no en un `except` porque FastAPI no propaga el error de la
    vista lanzandolo dentro del generador: lo cierra con `GeneratorExit`, que
    hereda de `BaseException` y esquivaria un `except Exception`. Revvertir
    tambien lo ya confirmado es un no-op, asi que el mismo camino vale para los
    tres finales posibles.
    """
    unidad = UnitOfWork(get_sessionmaker()())
    try:
        yield unidad
    finally:
        unidad.rollback()
        unidad.sesion.close()


#: Dependencia de FastAPI. El alias evita repetir el `Depends` en cada firma y
#: deja un unico punto que las pruebas pueden sustituir.
DbUnit = Annotated[UnitOfWork, Depends(get_unit_of_work)]
