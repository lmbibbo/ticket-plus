"""Motor SQLAlchemy 2 y fabrica de sesiones.

Un solo motor por proceso. La razon de que no se cree uno por peticion es
economica: un pool mantiene conexiones abiertas y calibradas, y abrir una por
peticion paga el handshake TLS en cada salto.

Los parametros del pool no son ajustes de gusto:

- `pool_pre_ping` evita que una conexion que el servidor cerro en silencio
  (reinicio, timeout del proxy) se entregue como si siguiera sana.
- `pool_recycle` recorta las conexiones mas antiguas que el limite del
  servidor: sin esto, la primera peticion tras un corte falla aunque el pool
  entregue esa conexion como nueva.

Los dos juntos son la combinacion habitual: uno detecta la conexion rota y el
otro evita llegar a ese estado.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.config import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    """Crea un motor con la configuracion del proceso."""
    resolved = settings or get_settings()
    return create_engine(
        resolved.database_url,
        pool_pre_ping=True,
        pool_size=resolved.db_pool_size,
        max_overflow=resolved.db_max_overflow,
        pool_recycle=resolved.db_pool_recycle_seconds,
        future=True,
        echo=resolved.sql_echo,
    )


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Fabrica de sesiones ligadas a un motor.

    `expire_on_commit=False` evita que SQLAlchemy marque los objetos como
    expirados al confirmar. Sin el, leer un atributo despues del commit obliga a
    una consulta nueva y, si la transaccion de la peticion ya se cerro, falla
    con `DetachedInstanceError`.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Motor cacheado del proceso."""
    return build_engine()


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    """Fabrica de sesiones cacheada del proceso."""
    return build_sessionmaker(get_engine())


def reset_engine_cache() -> None:
    """Cierra el motor cacheado y vacia la cache.

    Sin esto, una prueba que cambia la URL seguiria usando el motor de la
    anterior. Solo hace falta en pruebas y en el apagado ordenado.
    """
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
