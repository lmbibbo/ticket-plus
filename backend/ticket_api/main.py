"""Aplicacion FastAPI del dominio.

Es la unica aplicacion autorizada a leer y escribir datos de dominio. Flask
habla con esta por HTTP interno y nunca importa este paquete.
"""

from __future__ import annotations

from fastapi import FastAPI

from ticket_api.api.admin import router as admin_router
from ticket_api.api.auth import router as auth_router
from ticket_api.api.eventos import router as eventos_router
from ticket_api.config import Settings, get_settings
from ticket_api.errors.problem import register_exception_handlers
from ticket_api.observability.logging import configure_logging
from ticket_api.observability.middleware import RequestContextMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la instancia de la API."""
    resolved = settings or get_settings()
    configure_logging(resolved.app_name, resolved.log_level)

    app = FastAPI(
        title="ticket-plus API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # El middleware envuelve todo, incluida la respuesta de error, para que el
    # `request_id` que se registra sea el mismo que se devuelve en la cabecera.
    app.add_middleware(RequestContextMiddleware, app_name=resolved.app_name)
    register_exception_handlers(app)

    # La sesion se expone como dependencia sobrescribible en vez de resolverse
    # en el arranque: las pruebas inyectan la suya y asi pueden comprobar la
    # frontera de transaccion sin tocar la base de desarrollo.
    app.state.settings = resolved

    # Los routers van despues de los manejadores de excepcion: al incluir un
    # router no se puede volver a registrar un manejador para `Exception`, y
    # estos no registran ninguno.
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(eventos_router)

    @app.get("/health", tags=["operacion"])
    async def health() -> dict[str, str]:
        """Comprobacion de vida para el despliegue y el ensayo de rollback."""
        return {"status": "ok", "app": resolved.app_name}

    @app.get("/api/v1/ping", tags=["operacion"])
    async def ping() -> dict[str, str]:
        """Sonda minima que la web usa para verificar el contrato y la correlacion."""
        return {"pong": resolved.app_name}

    return app


app = create_app()
