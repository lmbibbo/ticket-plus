"""Configuracion de la aplicacion web, leida del entorno.

La web no tiene DSN: no habla con la base de datos. Solo conoce la URL interna
de la API y sus propias credenciales de servicio.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuracion del proceso web."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_base_url: str = Field(
        default="http://127.0.0.1:8000",
        description="URL interna de FastAPI. Nunca se expone al navegador.",
    )
    api_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Timeout de las llamadas web -> API. Agotarlo es un error recuperable.",
    )

    app_name: str = Field(default="web", description="Valor del campo `app` en los logs.")
    log_level: str = Field(
        default="INFO",
        description="Nivel minimo que llega a la salida estandar.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuracion cacheada del proceso."""
    return Settings()
