"""Cliente HTTP de la web hacia FastAPI.

Un unico `httpx.Client` con pool de conexiones para toda la vida del proceso:
abrir una conexion por peticion multiplicaria el tiempo de la compra por el
coste del handshake TLS en cada salto.

Reglas que aplica:

- Propaga el `request_id` de la peticion web, para que los dos saltos queden
  correlacionados.
- No reenvia ninguna cabecera del navegador. No es una lista de exclusion: la
  peticion se construye desde cero, asi que la cookie de sesion y la
  autenticacion de servicio no pueden filtrarse por accidente.
- Traduce `application/problem+json` a un `ApiProblem` para que las vistas
  puedan mapear el error a un formulario o a un fragmento HTMX.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

import httpx

from ticket_web.observability.middleware import REQUEST_ID_HEADER, current_request_id

DEFAULT_TIMEOUT_SECONDS: Final = 5.0

#: Clave con la que el cliente de la API se guarda en `app.extensions`, para que
#: las vistas lo alcancen sin recibirlo por parametro.
API_CLIENT_KEY: Final = "api_client"


class ApiFailure(StrEnum):
    """Por que no se pudo completar la llamada.

    Se distingue el timeout de la caida porque la accion que corresponde no es
    la misma: un timeout suele resolverse solo al reintentar, una caida no, y
    ofrecer "reintentar" en el segundo caso hace perder el tiempo del usuario.
    """

    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    UNREADABLE = "unreadable"


class ApiUnavailableError(RuntimeError):
    """La API no respondio, excedio el timeout o devolvio algo ilegible.

    Siempre es recuperable: la vista responde 503 y ofrece reintentar. Nunca
    lleva el motivo de httpx, que puede contener nombres de host, URLs
    internas o cabeceras de autenticacion.
    """

    def __init__(self, fallo: ApiFailure, *, status: int | None = None) -> None:
        super().__init__(fallo.value)
        self.fallo = fallo
        self.status = status

    @property
    def code(self) -> str:
        """Codigo estable para la bitacora y la respuesta de la web."""
        return f"api_{self.fallo.value}"


@dataclass(frozen=True, slots=True)
class ApiProblem:
    """Un `problem+json` recibido de la API."""

    status: int
    code: str
    title: str
    detail: str
    errors: list[dict[str, str]]

    def field_errors(self) -> dict[str, str]:
        """Errores por campo, listos para repintar un formulario.

        Si la API manda dos errores para el mismo campo se queda el primero: al
        pintar un formulario solo cabe un mensaje por campo, y el Ultimo sería el
        mas especifico y perdería el contexto del primero.
        """
        por_campo: dict[str, str] = {}
        for error in self.errors:
            campo = error.get("field", "").strip()
            mensaje = error.get("message", "").strip()
            if campo and mensaje and campo not in por_campo:
                por_campo[campo] = mensaje
        return por_campo

    def mensaje_para_fragmento(self) -> str:
        """Texto unico para un fragmento HTMX que no distingue campos.

        Con errores por campo se nombra el primero: un fragmento que solo dice
        "datos invalidos" obliga al usuario a adivinar que campo corregir.
        """
        campos = self.field_errors()
        if campos:
            primero_campo, primero_mensaje = next(iter(campos.items()))
            return f"{primero_campo}: {primero_mensaje}"
        return self.detail or self.title

    @property
    def es_error_de_campos(self) -> bool:
        """Si el error se puede atribuir a datos del formulario."""
        return bool(self.field_errors())


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """Respuesta exitosa de la API, ya deserializada.

    Se conservan las cabeceras porque `X-Request-ID` es la prueba de que el
    identificador sobrevivio al salto: sin ella no se puede comprobar la
    correlacion desde fuera.
    """

    status: int
    data: Any
    headers: httpx.Headers


class ApiProblemError(Exception):
    """Error de negocio devuelto por la API.

    Envuelve el `problem+json` en vez de ser el problema: las vistas necesitan
    capturarla y leer `.code`, `.status` y `.errors`.
    """

    def __init__(self, problem: ApiProblem) -> None:
        super().__init__(f"{problem.code}: {problem.detail}")
        self.problem = problem

    @property
    def code(self) -> str:
        return self.problem.code

    @property
    def status(self) -> int:
        return self.problem.status

    @property
    def errors(self) -> list[dict[str, str]]:
        return self.problem.errors


class ApiLike(Protocol):
    """Lo que la web necesita de la API.

    `create_app` depende de este contrato y no de `ApiClient`: asi la aplicacion
    quedaparable con un cliente de prueba sin casts, y cambiar de transporte o
    de biblioteca HTTP no obliga a tocar las vistas.
    """

    def get(self, path: str, **kwargs: Any) -> ApiResponse: ...

    def post(self, path: str, **kwargs: Any) -> ApiResponse: ...

    def patch(self, path: str, **kwargs: Any) -> ApiResponse: ...

    def delete(self, path: str, **kwargs: Any) -> ApiResponse: ...

    def close(self) -> None: ...


class ApiClient:
    """Envoltura del cliente HTTP con propagacion de correlacion."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
        auth_headers: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=transport,
            headers={"accept": "application/json"},
            follow_redirects=False,
        )
        #: Cabeceras de autenticacion, pedidas en cada peticion y no al construir:
        #: el token de acceso caduca y se renueva mientras el proceso vive, y una
        #: copia guardada al arranque seria un cliente que envejece en silencio.
        self._auth_headers: Callable[[], dict[str, str]] = auth_headers or _sin_auth

    def close(self) -> None:
        """Cierra el pool de conexiones."""
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> ApiResponse:
        """Llama a la API y traduce los fallos a excepciones propias.

        `request_id` se toma del contexto de la peticion web si no se pasa
        explicito, de modo que la correlacion no depende de que cada vista se
        acuerde de hacerlo.
        """
        correlation = request_id or current_request_id()
        try:
            raw = self._client.request(
                method,
                path,
                json=json,
                params=params,
                headers={REQUEST_ID_HEADER: correlation, **self._auth_headers()},
            )
        except httpx.TimeoutException as exc:
            raise ApiUnavailableError(ApiFailure.TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE) from exc

        if raw.status_code >= 400:
            raise _problem_to_raise(raw)

        return ApiResponse(
            status=raw.status_code,
            data=_decode(raw),
            headers=raw.headers,
        )

    def get(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> ApiResponse:
        return self.request("DELETE", path, **kwargs)


def _sin_auth() -> dict[str, str]:
    """Sin autenticacion: el cliente construido a mano, y las rutas publicas."""
    return {}


def _decode(raw: httpx.Response) -> Any:
    if not raw.content:
        return None
    try:
        return raw.json()
    except ValueError:
        return None


def _problem_to_raise(raw: httpx.Response) -> Exception:
    """Decide que excepcion corresponde a un 4xx/5xx.

    Un cuerpo que no es `problem+json` no es un error de negocio: significa que
    la API fallo de otra forma, y se traduce a un fallo recuperable para que la
    vista no exponga una respuesta que no entiende.

    Un 5xx siempre se trata como fallo recuperable, venga bien formado o no: la
    web no debe pintarle al usuario el detalle de un fallo del servidor, y
    tampoco tomarlo por un error de datos que el usuario pueda corregir.
    """
    cuerpo_ilegible = "application/problem+json" not in raw.headers.get("content-type", "")
    body = _decode(raw)
    if not isinstance(body, dict):
        return ApiUnavailableError(ApiFailure.UNREADABLE, status=raw.status_code)

    raw_errors = body.get("errors")
    errors: list[dict[str, str]] = []
    if isinstance(raw_errors, list):
        for item in raw_errors:
            if isinstance(item, dict):
                errors.append(
                    {
                        "field": str(item.get("field", "")),
                        "message": str(item.get("message", "")),
                    }
                )

    status = int(body.get("status", raw.status_code))
    if status >= 500:
        # El codigo se conserva en la bitacora, pero el mensaje que ve el
        # usuario lo redacta la web, no el backend.
        return ApiUnavailableError(ApiFailure.UNREACHABLE, status=status)

    if cuerpo_ilegible:
        return ApiUnavailableError(ApiFailure.UNREADABLE, status=raw.status_code)

    return ApiProblemError(
        ApiProblem(
            status=status,
            code=str(body.get("code", "internal_error")),
            title=str(body.get("title", "Error")),
            detail=str(body.get("detail", "")),
            errors=errors,
        )
    )
