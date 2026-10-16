"""Blueprint del area de administracion: gestion de cuentas de proveedor.

La vista existia desde 6.2 para dar destino a la navegacion por rol; desde 7.2
lista las cuentas pendientes y emite las dos acciones que el API de
administracion permite sobre ellas: aprobar y suspender. El listado es el del
API y no uno montado aqui: `GET /api/v1/admin/users` es quien decide que es
"pendiente", y filtrar en dos sitios haria que una de las dos listas dejara de
listar lo mismo sin que ninguna se enterara.

Las acciones son POST y siguen el patron redirection-render: la operacion
termina en un 303 al listado, que vuelve a pedirlo al API. Un refresco del
navegador no vuelve a aprobar nada, y lo que se muestra siempre es el estado
que acaba de devolver el servidor. El token CSRF no se comprueba aqui: desde
6.4 lo exige la guarda de `csrf.py` para toda ruta que cambia estado.

Un fallo de la operacion -una cuenta que otro administrador aprobo primero, un
id que ya no existe- se repinta como aviso sobre el listado refrescado, con el
detalle que devuelve el API: son mensajes operativos para quien administrador,
y esconderlos solo haria que la lista mostrara algo que la API acaba de
contradecir. Los fallos de integracion (API caida, timeout) no se tocan: los
traduce el manejador global de la aplicacion.
"""

from __future__ import annotations

import uuid

from flask import Blueprint, redirect, render_template, url_for
from werkzeug.wrappers import Response as WerkzeugResponse

from ticket_web.api_client import ApiProblemError
from ticket_web.auth import exigir_rol
from ticket_web.observability.middleware import set_problem_code
from ticket_web.sesion_api import cliente_api

#: Rutas del API de administracion. Van como constantes y no como cadenas
#: sueltas en las vistas: si cambian en el backend se cambian aqui, y el
#: contrato del API es el unico sitio que las nombra.
RUTA_PENDIENTES = "/api/v1/admin/users"
RUTA_APROBAR = RUTA_PENDIENTES + "/{}/approve"
RUTA_SUSPENDER = RUTA_PENDIENTES + "/{}/suspend"

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.get("/usuarios")
@exigir_rol("admin")
def usuarios() -> str | tuple[str, int]:
    """Listado de proveedores pendientes, con aprobar y suspender en cada uno."""
    return _pagina()


@admin_bp.post("/usuarios/<uuid:usuario_id>/aprobar")
@exigir_rol("admin")
def aprobar(usuario_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Pasa la cuenta a `active` y vuelve al listado."""
    return _ejecutar(RUTA_APROBAR.format(usuario_id))


@admin_bp.post("/usuarios/<uuid:usuario_id>/suspender")
@exigir_rol("admin")
def suspender(usuario_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Suspende la cuenta -revocando sus sesiones vivas- y vuelve al listado."""
    return _ejecutar(RUTA_SUSPENDER.format(usuario_id))


def _ejecutar(ruta: str) -> WerkzeugResponse | str | tuple[str, int]:
    """Corre una accion y redirige; un `problem+json` se repinta como aviso."""
    try:
        cliente_api().post(ruta)
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _pagina(aviso_texto=exc.problem.detail)
    return redirect(url_for("admin.usuarios"), code=303)


def _pagina(aviso_texto: str | None = None) -> str | tuple[str, int]:
    """Listado actual, con el aviso opcional de una accion que no pudo ser."""
    try:
        respuesta = cliente_api().get(RUTA_PENDIENTES)
    except ApiProblemError as exc:
        # Un 401 aqui significa que la sesion del administrador ya no vale
        # (revocada o suspendida entre peticiones). Se pinta el aviso con el
        # estado del fallo; el proximo ciclo de renovacion la cerrara.
        set_problem_code(exc.code)
        return (
            render_template(
                "admin/usuarios.html", pendientes=[], aviso_texto=exc.problem.detail
            ),
            exc.status,
        )
    return render_template(
        "admin/usuarios.html", pendientes=respuesta.data, aviso_texto=aviso_texto
    )


__all__ = [
    "RUTA_APROBAR",
    "RUTA_PENDIENTES",
    "RUTA_SUSPENDER",
    "admin_bp",
]
