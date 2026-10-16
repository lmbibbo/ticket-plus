"""Fixtures compartidas por las suites del backend y de la web.

Vive en la raiz porque las dos suites necesitan la misma captura de bitacora y
no debe haber dos copias de la misma logica de test.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Any

import pytest

#: Lineas JSON capturadas de una bitacora de acceso.
CapturedLogs = list[dict[str, Any]]
#: Varias bitacoras a la vez, indexadas por nombre de logger.
ManyAccessLogs = AbstractContextManager[dict[str, CapturedLogs]]


@contextmanager
def collect_access(logger_name: str) -> Iterator[CapturedLogs]:
    """Acumula las lineas JSON que emite un logger de acceso.

    El formato de la linea es responsabilidad de la aplicacion, no del test:
    aqui solo se comprueba que lo emitido es JSON parseable.
    """
    lines: CapturedLogs = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            text = record.getMessage()
            if not text.strip():
                return
            lines.append(json.loads(text))

    handler = _Collector()
    logger = logging.getLogger(logger_name)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@pytest.fixture
def capture_access() -> Callable[[str], AbstractContextManager[CapturedLogs]]:
    """Devuelve una fabrica de context managers que capturan una bitacora."""

    @contextmanager
    def _factory(logger_name: str) -> Iterator[CapturedLogs]:
        with collect_access(logger_name) as lines:
            yield lines

    return _factory


@pytest.fixture
def capture_many() -> Callable[[list[str]], ManyAccessLogs]:
    """Captura varias bitacoras a la vez, indexadas por nombre de logger.

    La prueba de correlacion necesita las dos lineas en el mismo ambito: por
    separado no se podrian comparar, y el fallo que importa es precisamente el
    de que no coincidan.
    """

    @contextmanager
    def _factory(logger_names: list[str]) -> Iterator[dict[str, CapturedLogs]]:
        with ExitStack() as stack:
            captured = {name: stack.enter_context(collect_access(name)) for name in logger_names}
            yield captured

    return _factory
