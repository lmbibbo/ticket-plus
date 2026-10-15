"""3.9 - La suite de identidad cubre lo que dice la especificacion, en PostgreSQL.

Esta suite no prueba funcionalidad: mira la propia suite. Es la tarea que pide
comprobar *que* se cubre, y hacerla a mano -contando pruebas de memoria- no deja
constancia ni falla cuando alguien renombra una clase y se lleva por delante la
cobertura de una capacidad.

Tres comprobaciones distintas:

**Que el motor es PostgreSQL.** La especificacion lo pide y SQLite no sirve para
comprobarlo: no tiene los mismos `CHECK`, ni el mismo aislamiento, ni
`gen_random_uuid()`. Una suite verde sobre SQLite no dice nada de la de
produccion.

**Que cada capacidad tiene pruebas.** Se recoge la suite con
`--collect-only` y se busca un patron por capacidad. Recoger en vez de leer el
codigo fuente con `ast` es a proposito: recoge lo que pytest ejecutaria de verdad,
parametrizaciones incluidas.

**Que no aparece SQLite.** Ni como motor de pruebas ni como cadena de conexion en
un modulo. Se busca el texto en los archivos, que es como se colaria: por un
`create_engine("sqlite://")` en una prueba, por un `import sqlite3` o por un DSN
de ejemplo en un docstring.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import Engine

from ticket_api.tests.conftest import url_de_pruebas

RAIZ = Path(__file__).resolve().parents[3]
RUTA_PRUEBAS = Path(__file__).resolve().parent
PYTEST = [sys.executable, "-m", "pytest"]

#: Modulos que forman la suite de identidad. Si uno se mueve o se renombra, esto
#: falla antes de que nadie note que le falta una capacidad.
MODULOS_DE_IDENTIDAD = (
    "test_identity_models.py",
    "test_migrations.py",
    "test_api_registro.py",
    "test_tokens.py",
    "test_api_auth.py",
    "test_autorizacion.py",
    "test_api_admin.py",
)

#: Capacidad de la especificacion y patron que tiene que cumplir su cobertura.
#: Los patrones son de `pytest -q --collect-only`, que nombra `archivo::clase`.
#:
#: Apuntan a un sitio y solo a uno. Un patron con alternativas -"este modulo o
#: cualquiera que mentione X"- se cumple mientras quede *algo*, y con el primero
#: que se renombre deja de avisar justo cuando la capacidad se queda sin pruebas.
CAPACIDADES: dict[str, str] = {
    "registro": r"test_api_registro\.py::",
    "aprobacion": r"test_api_admin\.py::Test(Aprobacion|ListadoDePendientes)::",
    "autenticacion": r"test_api_auth\.py::TestInicioDeSesion::",
    "renovacion": r"test_api_auth\.py::TestRenovacion::",
    "revocacion": r"test_api_auth\.py::TestCierreDeSesion::",
    "suspension": r"test_api_admin\.py::TestSuspension::",
    "autorizacion": r"test_autorizacion\.py::Test(MatrizDeRoles|PropiedadDelRecurso)::",
}


@pytest.fixture(scope="module")
def nodos_recogidos() -> list[str]:
    """Identificadores de todo lo que pytest ejecutaria en la suite de identidad."""
    rutas = [str(RUTA_PRUEBAS / modulo) for modulo in MODULOS_DE_IDENTIDAD]
    # La orden es un literal y las rutas salen de la constante de arriba, sin nada
    # que venga de fuera. S603 esta aqui para recordar mirar la llamada, y en esta
    # no hay nada que mirar.
    salida = subprocess.run(  # noqa: S603
        [*PYTEST, "--collect-only", "-q", *rutas],
        capture_output=True,
        text=True,
        check=True,
        cwd=RAIZ,
    )
    return [linea for linea in salida.stdout.splitlines() if "::" in linea]


class TestElMotorEsPostgreSQL:
    def test_el_dsn_de_pruebas_es_de_postgres(self) -> None:
        dsn = url_de_pruebas()

        assert dsn.startswith("postgresql"), f"la suite corre contra {dsn}"

    def test_el_motor_que_se_usa_es_postgres(self, engine: Engine) -> None:
        """No basta el DSN: el motor ya construido tiene que ser el esperado.

        Un motor de SQLite cacheado en un modulo pasaria la comprobacion del DSN
        y haria que todo lo demas pasara sin comprobar nada.
        """
        assert engine.dialect.name == "postgresql", engine.dialect.name

    def test_la_version_de_postgres_es_la_que_se_usa(
        self, engine: Engine
    ) -> None:
        """Se deja constancia de contra que se corre, no solo de que es Postgres.

        Un fallo de integridad o un `CHECK` que se ignoran solo en versiones
        concretas no se detectarian con una version inventada.
        """
        from sqlalchemy import text

        with engine.connect() as conexion:
            assert conexion.execute(text("SHOW server_version")).scalar_one()


class TestCoberturaDeLaEspecificacion:
    def test_los_modulos_de_identidad_existen(self) -> None:
        for modulo in MODULOS_DE_IDENTIDAD:
            assert (RUTA_PRUEBAS / modulo).is_file(), f"falta {modulo}"

    @pytest.mark.parametrize(("capacidad", "patron"), sorted(CAPACIDADES.items()))
    def test_cada_capacidad_tiene_pruebas(
        self, nodos_recogidos: list[str], capacidad: str, patron: str
    ) -> None:
        """Cada capacidad de la especificacion tiene al menos una prueba viva.

        El fallo de esto no es "falta cobertura" en abstracto: nombra la
        capacidad y el patron, que es justo lo que hay que ir a escribir.
        """
        encontrados = [nodo for nodo in nodos_recogidos if re.search(patron, nodo)]

        assert encontrados, f"ninguna prueba cubre {capacidad} (patron: {patron})"

    def test_la_suite_de_identidad_no_esta_vacia(self, nodos_recogidos: list[str]) -> None:
        """Un modulo que se deja vacio sin querer recogeria cero pruebas y las
        comprobaciones de arriba pasarian por falta de ruido."""
        assert len(nodos_recogidos) > 100, f"solo se recogieron {len(nodos_recogidos)} pruebas"


class TestNoApareceSQLite:
    def test_ningun_modulo_de_pruebas_habla_de_sqlite(self) -> None:
        """Se busca el texto, no una llamada concreta.

        `sqlite://` podria entrar por un `create_engine`, por un DSN de ejemplo
        en un docstring o por un `import sqlite3`; los tres son la misma falta de
        rigor contra la especificacion.
        """
        # Este modulo queda fuera: habla de la palabra prohibida por definicion.
        sospechosos = [
            (ruta.name, numero)
            for ruta in sorted(RUTA_PRUEBAS.glob("*.py"))
            if ruta.name != Path(__file__).name
            for numero, linea in enumerate(ruta.read_text().splitlines(), start=1)
            if "sqlite" in linea.lower()
        ]

        assert sospechosos == [], f"mencion de sqlite: {sospechosos}"

    def test_el_proyecto_no_tiene_una_base_sqlite_en_el_almacen(self) -> None:
        """Un `.db` o un `.sqlite3` en el repositorio seria una base de datos
        de verdad en algun sitio, y la suite passaria sin escribir en ella."""
        restos = _bases_sqlite()

        assert restos == [], f"hay una base SQLite en el repositorio: {restos}"


#: Directorios que no se recorren. Las entornos y las cachas de herramientas no
#: son codigo del proyecto: mypy guarda sus indices en `cache.N.db` y recorrerlos
#: haria que esta comprobacion fallara -y fuera lenta- por culpa de una cache.
SIN_RECORRER = frozenset(
    {
        ".git",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
    }
)


def _bases_sqlite() -> list[str]:
    """Rutas de archivos que son una base SQLite."""
    encontradas: list[str] = []
    for carpeta, subcarpetas, archivos in os.walk(RAIZ):
        subcarpetas[:] = [una for una in subcarpetas if una not in SIN_RECORRER]
        for nombre in archivos:
            if Path(nombre).suffix in {".db", ".sqlite", ".sqlite3"}:
                encontradas.append(str(Path(carpeta, nombre).relative_to(RAIZ)))
    return encontradas
