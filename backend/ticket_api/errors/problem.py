"""Errores en formato `application/problem+json` (RFC 9457).

Regla de seguridad que atraviesa todo el modulo: una respuesta 4xx describe el
error del solicitante, una 5xx no describe nada del servidor. Un `detail` de
5xx es siempre generico; el motivo real vive solo en la bitacora del proceso.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from ticket_api.errors.codes import ErrorCode

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"
PROBLEM_TYPE_BASE: Final = "/api/v1/problems"

_STATUS_TO_CODE: Final[dict[int, ErrorCode]] = {
    400: ErrorCode.MALFORMED_REQUEST,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    422: ErrorCode.VALIDATION_ERROR,
}

logger = logging.getLogger(__name__)


class FieldError(BaseModel):
    """Error asociado a un campo concreto de un formulario."""

    field: str = Field(description="Ruta del campo dentro del cuerpo de la peticion.")
    message: str = Field(description="Mensaje seguro para mostrar al usuario.")


class Problem(BaseModel):
    """Cuerpo de una respuesta `application/problem+json`."""

    type: str = Field(description="Referencia estable al tipo de problema.")
    title: str = Field(description="Resumen corto y estable del problema.")
    status: int = Field(description="Codigo de estado HTTP.")
    detail: str = Field(description="Explicacion del problema en esta peticion.")
    code: str = Field(description="Codigo de negocio estable para decisiones del cliente.")
    instance: str | None = Field(default=None, description="Ruta que produjo el problema.")
    errors: list[FieldError] | None = Field(default=None, description="Errores por campo.")


class ProblemException(Exception):
    """Error de dominio que se traduce a un `problem+json`."""

    def __init__(
        self,
        *,
        status: int,
        code: ErrorCode,
        title: str,
        detail: str,
        errors: list[FieldError] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors


def problem_response(
    *,
    request: Request,
    status: int,
    code: ErrorCode,
    title: str,
    detail: str,
    errors: list[FieldError] | None = None,
) -> JSONResponse:
    """Construye la respuesta `problem+json` de una peticion.

    No fija `X-Request-ID`: esa cabecera es del `RequestContextMiddleware` y
    anadirla aqui la duplicaria en toda respuesta de error. El codigo si se
    deja en el estado de la peticion, para que el middleware lo registre junto
    al estado y la duracion.
    """
    request.state.problem_code = code.value
    payload = Problem(
        type=f"{PROBLEM_TYPE_BASE}/{code.value}",
        title=title,
        status=status,
        detail=detail,
        code=code.value,
        instance=request.url.path,
        errors=errors,
    )
    return JSONResponse(
        status_code=status,
        content=payload.model_dump(exclude_none=True),
        media_type=PROBLEM_CONTENT_TYPE,
    )


def build_internal_problem(request: Request) -> JSONResponse:
    """Respuesta 5xx sin detalle interno.

    La invoca el `RequestContextMiddleware` en vez de registrarse como
    manejador de FastAPI: un manejador de `Exception` lo ejecuta el
    `ServerErrorMiddleware`, que es exterior al middleware y por tanto emitiria
    la respuesta sin la cabecera de correlacion.
    """
    return problem_response(
        request=request,
        status=500,
        code=ErrorCode.INTERNAL_ERROR,
        title="Error interno",
        detail="Ocurrio un error interno.",
    )


def _field_path(loc: tuple[Any, ...]) -> str:
    """Convierte la ruta de un error de Pydantic en algo presentable en un formulario."""
    parts = [str(part) for part in loc if part != "body"]
    return ".".join(parts) if parts else "body"


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los manejadores que garantizan un unico esquema de error."""

    @app.exception_handler(ProblemException)
    async def handle_problem(request: Request, exc: ProblemException) -> JSONResponse:
        return problem_response(
            request=request,
            status=exc.status,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            errors=exc.errors,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            FieldError(
                field=_field_path(tuple(err.get("loc", ()))),
                message=str(err.get("msg", "")),
            )
            for err in exc.errors()
        ]
        return problem_response(
            request=request,
            status=422,
            code=ErrorCode.VALIDATION_ERROR,
            title="Solicitud invalida",
            detail="Uno o mas campos no superaron la validacion.",
            errors=errors,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return problem_response(
            request=request,
            status=exc.status_code,
            code=code,
            title="Solicitud no procesable" if exc.status_code < 500 else "Error interno",
            detail=str(exc.detail) if exc.status_code < 500 else "Ocurrio un error interno.",
        )

    # No se registra un manejador para `Exception`: los 5xx los convierte
    # `RequestContextMiddleware` para que conserven la correlacion. Anadirlo aqui
    # haria que el `ServerErrorMiddleware` respondiera por fuera del middleware y
    # la respuesta perdiera la cabecera `X-Request-ID`.
