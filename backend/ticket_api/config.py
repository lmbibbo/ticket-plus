"""Configuración del backend, leída del entorno.

Una sola fuente de verdad: las variables llegan por entorno o por el archivo
`.env` de la raíz del repositorio. No hay defaults silenciosos para la base de
datos: si `DATABASE_URL` falta, la aplicación no arranca.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuración del proceso de API."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        description="DSN de SQLAlchemy hacia PostgreSQL. Obligatorio: no hay default."
    )
    test_database_url: str | None = Field(
        default=None,
        description="DSN de la base de pruebas. Requerido por las fixtures de pytest.",
    )

    app_name: str = Field(default="backend", description="Valor del campo `app` en los logs.")
    log_level: str = Field(
        default="INFO",
        description="Nivel mínimo que llega a la salida estándar.",
    )

    sql_echo: bool = Field(
        default=False,
        description="Volca el SQL de SQLAlchemy. Nunca debe activarse en produccion: "
        "el SQL puede contener valores de columnas sensibles.",
    )

    db_pool_size: int = Field(
        default=5,
        ge=1,
        description="Conexiones que mantiene abiertas el pool. Subirlo sin subir el "
        "limite del servidor solo cambia donde se produce el error.",
    )
    db_max_overflow: int = Field(
        default=5,
        ge=0,
        description="Conexiones adicionales por encima del pool, hasta el limite del "
        "servidor. Con 0, una peticion que exceda el pool espera en vez de fallar.",
    )
    db_pool_recycle_seconds: int = Field(
        default=1800,
        ge=30,
        description="Edad maxima de una conexion antes de renovarla. Debe quedar por "
        "debajo del limite de conexiones del servidor o del proxy.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuracion cacheada del proceso."""
    return Settings()
