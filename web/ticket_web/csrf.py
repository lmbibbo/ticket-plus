"""Token CSRF de la web.

Lo que protege son los formularios que cambian estado, no las peticiones GET: un
enlace a `/auth/salir` por GET permitiria cerrar la sesion de otra persona con
una imagen en una pagina ajena.

El token vive en la sesion del servidor y viaja en un campo oculto del
formulario. Que este en el servidor y no en la cookie es lo que lo ata a la sesion
concreta: si fuera una cookie, un atacante queiese leerla -o que se la cumpliera
al visitante- tendria tambien el token del formulario.

Se compara en tiempo constante con `secrets.compare_digest`. La comparacion no
es un detalle menor: comparar con `==` devuelve en cuanto encuentra una diferencia
y filtra, caracter a caracter, cuanto de un token acertado lleva alguien.
"""

from __future__ import annotations

import secrets
from typing import Final

from flask import Flask, Request, render_template, request, session
from werkzeug.wrappers import Response as WerkzeugResponse

from ticket_web.observability.middleware import set_problem_code

#: Bytes de entropia del token. 32 es el tamano que recomienda la guia de OWASP
#: para tokens antifalsificacion; por debajo, adivinar uno a uno sale barato.
ENTROPIA: Final = 32

#: Clave de sesion donde se guarda el token emitido, y nombre del campo que
#: viaja en los formularios. Los dos son "csrf_token" a proposito: la sesion y el
#: formulario se nombran igual porque son la misma credencial vista desde sus dos
#: lados, y llamarlos distinto solo invitaria a comparar uno con el otro por
#: nombre en vez de por valor.
CLAVE_TOKEN: Final = "csrf_token"  # noqa: S105 - es el nombre del campo, no un valor
CAMPO_TOKEN: Final = "csrf_token"  # noqa: S105 - es el nombre del campo, no un valor

#: Metodos que pueden cambiar estado y por tanto exigen token.
METODOS_CON_ESTADO: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def token_csrf() -> str:
    """Token de la sesion, emitiendo uno si todavia no hay.

    Se emite una sola vez y se reutiliza: un token por formulario obligaria a
    tenerlos todos en la pagina, y uno por sesion es igual de seguro mientras la
    sesion no cambie.
    """
    existente = session.get(CLAVE_TOKEN)
    if isinstance(existente, str) and existente:
        return existente
    nuevo = secrets.token_urlsafe(ENTROPIA)
    session[CLAVE_TOKEN] = nuevo
    return nuevo


def comprobar_csrf(request: Request) -> bool:
    """Si el token que llega en el formulario es el de esta sesion."""
    enviado = request.form.get(CAMPO_TOKEN, request.headers.get("X-CSRF-Token", ""))
    esperado = session.get(CLAVE_TOKEN)
    if not isinstance(esperado, str) or not enviado:
        return False
    return secrets.compare_digest(enviado, esperado)


def csrf_rechazado() -> tuple[str, int]:
    """Respuesta a una peticion sin token valido.

    No se ejecuta ninguna operacion. Un 400 y no un 403: el token ausente o
    incorrecto es una peticion mal formada, no una peticion sin permiso, y el
    visitante que recargue la pagina -&gt; recibe uno nuevo -&gt; lo supera.
    """
    set_problem_code("csrf_failed")
    return (render_template("error/csrf.html"), 400)


def registrar_csrf(app: Flask) -> None:
    """Publica el token en las plantillas y exige su comprobacion.

    La comprobacion va en `before_request` y no en cada vista porque la lista de
    operaciones que cambian estado crece con cada fase (7.2, 7.3, 7.5) y una
    vista nueva que olvide el comprobador seria una vulnerabilidad silenciosa:
    aqui la regla esta escrita una vez y una ruta nueva no puede saltarsela.
    Las vistas no llaman a `comprobar_csrf`; solo hacen falta para pruebas y
    para las peticiones HTMX que mandan el token en cabecera.
    """
    app.jinja_env.globals["csrf_token"] = token_csrf

    @app.before_request
    def _exigir_token() -> WerkzeugResponse | str | tuple[str, int] | None:
        if request.method not in METODOS_CON_ESTADO:
            return None
        if not comprobar_csrf(request):
            return csrf_rechazado()
        return None


__all__ = [
    "CAMPO_TOKEN",
    "CLAVE_TOKEN",
    "ENTROPIA",
    "METODOS_CON_ESTADO",
    "comprobar_csrf",
    "csrf_rechazado",
    "registrar_csrf",
    "token_csrf",
]