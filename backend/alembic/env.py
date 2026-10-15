"""Entorno de Alembic.

El DSN no se copia en `alembic.ini`: sale de la misma configuracion que usa la
aplicacion, de modo que migraciones y runtime nunca pueden apuntar a bases
distintas por accidente.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# `Base` no conoce ninguna tabla hasta que se importa un modelo. Sin este
# import, `target_metadata` queda vacio y `autogenerate` propone una migracion
# que no crea nada, que es el fallo mas silencioso que tiene Alembic.
import ticket_api.models  # noqa: F401
from ticket_api.config import get_settings
from ticket_api.db.base import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` no es cosmetico: con el default, quien
    # haya construido sus loggers antes de aplicar migraciones se queda sin
    # emitir. Pasa en un despliegue que migra al arrancar, y en las pruebas,
    # donde una suite que corre migraciones apaga los logs de acceso de las
    # aplicaciones que ya estaban configuradas.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """DSN efectivo: la variable de entorno gana sobre `alembic.ini`."""
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
