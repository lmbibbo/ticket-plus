"""Blueprint del area de proveedor: gestion de sus propios eventos y zonas.

La vista existia desde 6.2 para que la navegacion por rol tenga destino; desde
7.3 es el area de trabajo del proveedor: alta, edicion, publicacion y retiro de
sus eventos, y el alta, edicion y borrado de sus zonas. Todo pasa por el API de
gestion (`/api/v1/events`): la web no toca la base, y las reglas de negocio -
el estado del evento, el ownership, la ventana de fechas, el nombre repetido de
una zona- las decide el backend, que es quien las tiene escritas. Aqui solo se
pinta lo que el API devuelve y se repinta lo que veto.

Las acciones son POST y siguen el patron redirection-render de 7.2: la
operacion termina en un 303 al detalle, que vuelve a pedirlo al API. El token
CSRF no se comprueba aqui: desde 6.4 lo exige la guarda de `csrf.py`.

Un fallo de negocio se repinta donde ocurrio, con el detalle del `problem+json`:
el aviso para los rechazos transversales (publicar sin zonas, retirar dos veces,
una cuenta ajena) y los errores por campo junto al input que los produjo, que
es la diferencia entre "el nombre ya existe" pegado al campo y el mismo texto en
un bloque que obliga a ir adivinando. Los fallos de integracion (API caida,
timeout) no se tochan: los traduce el manejador global de la aplicacion.

Las zonas no se esconden por estado del evento: el API de 4.3 las deja operar
en cualquier estado, y una regla que solo existiera aqui seria una mentira que
el backend contradice.
"""

from __future__ import annotations

import uuid
from typing import Any

from flask import Blueprint, redirect, render_template, request, url_for
from werkzeug.wrappers import Response as WerkzeugResponse

from ticket_web.api_client import ApiProblemError
from ticket_web.auth import exigir_rol
from ticket_web.observability.middleware import set_problem_code
from ticket_web.sesion_api import cliente_api

#: Rutas del API de gestion. Como constantes y no como cadenas sueltas: si
#: cambian en el backend se cambian aqui, y el contrato del API es el unico
#: sitio que las nombra.
RUTA_EVENTOS = "/api/v1/events"
RUTA_MINOS = RUTA_EVENTOS + "/mine"
RUTA_EVENTO = RUTA_EVENTOS + "/{}"
RUTA_PUBLICAR = RUTA_EVENTO + "/publish"
RUTA_RETIRAR = RUTA_EVENTO + "/retire"
RUTA_ZONAS = RUTA_EVENTO + "/zones"
RUTA_ZONA = RUTA_ZONAS + "/{}"

#: Lo que el navegador manda en un `datetime-local` llega sin zona horaria. El
#: backend guarda instantes con zona, y un naive lo interpretaria Postgres segun
#: la sesion del servidor: se fija UTC aqui, que es como el resto del sistema
#: representa los instantes.
FORMATO_FORMA = "%Y-%m-%dT%H:%M"
ETIQUETAS_DE_ESTADO = {
    "draft": "Borrador",
    "published": "Publicado",
    "retired": "Retirado",
}

owner_bp = Blueprint("owner", __name__, url_prefix="/gestion")


@owner_bp.get("/eventos")
@exigir_rol("event-owner")
def eventos() -> str | tuple[str, int]:
    """Listado de los eventos propios, con el alta colgando de la misma pagina.

    El proveedor necesita ver tambien sus borradores -que no estan en ningun
    catalogo- y los retirados, que dejan de estar en venta pero siguen siendo
    suyo. La lista la decide el API (`/mine`), no un filtro de aqui.

    El endpoint se llama `eventos` y no `lista`: es el nombre que 6.2 le dio y
    al que apuntan tanto el mapa de destinos por rol como la navegacion de
    `base.html`; renombrarlo romperia el login de todo proveedor.
    """
    return _lista()


@owner_bp.post("/eventos")
@exigir_rol("event-owner")
def crear() -> WerkzeugResponse | str | tuple[str, int]:
    """Alta de un evento en borrador, a traves del API.

    El API no acepta el estado en el cuerpo: un evento nace borrador por
    definicion, y la publicacion es un paso aparte que exige zonas. Un 422 del
    API se repinta en el formulario con sus errores por campo.
    """
    form = _datos_del_formulario()
    try:
        respuesta = cliente_api().post(RUTA_EVENTOS, json=_cuerpo_del_evento(form))
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _lista(
            aviso_texto=exc.problem.detail if not exc.problem.field_errors() else None,
            errores=exc.problem.field_errors(),
            form=form,
        )
    return redirect(url_for("owner.detalle", evento_id=respuesta.data["id"]), code=303)


@owner_bp.get("/eventos/<uuid:evento_id>")
@exigir_rol("event-owner")
def detalle(evento_id: uuid.UUID) -> str | tuple[str, int]:
    """La pagina de trabajo: el evento, sus zonas y las acciones que su estado
    permite."""
    return _detalle(evento_id)


@owner_bp.post("/eventos/<uuid:evento_id>")
@exigir_rol("event-owner")
def editar(evento_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Edicion del evento en borrador.

    El formulario manda los cuatro campos siempre: es una edicion, no un
    parche, y mandar solo lo que cambio dejaria al backend sin forma de saber
    que una descripcion vacia significa borrarla y no omitirla.
    """
    form = _datos_del_formulario()
    try:
        cliente_api().patch(
            RUTA_EVENTO.format(evento_id), json=_cuerpo_del_evento(form)
        )
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _detalle(
            evento_id,
            aviso_texto=exc.problem.detail if not exc.problem.field_errors() else None,
            errores=exc.problem.field_errors(),
            form=form,
        )
    return redirect(url_for("owner.detalle", evento_id=evento_id), code=303)


@owner_bp.post("/eventos/<uuid:evento_id>/publicar")
@exigir_rol("event-owner")
def publicar(evento_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Pasa el evento a `published`; el API exige al menos una zona."""
    return _accion(evento_id, RUTA_PUBLICAR.format(evento_id))


@owner_bp.post("/eventos/<uuid:evento_id>/retirar")
@exigir_rol("event-owner")
def retirar(evento_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Saca el evento del catalogo. Solo desde `published`, y es terminal."""
    return _accion(evento_id, RUTA_RETIRAR.format(evento_id))


@owner_bp.post("/eventos/<uuid:evento_id>/zonas")
@exigir_rol("event-owner")
def crear_zona(evento_id: uuid.UUID) -> WerkzeugResponse | str | tuple[str, int]:
    """Alta de una zona bajo el evento."""
    form = _datos_del_formulario()
    try:
        cliente_api().post(RUTA_ZONAS.format(evento_id), json=_cuerpo_de_zona(form))
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _detalle(
            evento_id,
            aviso_texto=exc.problem.detail if not exc.problem.field_errors() else None,
            errores_zona=exc.problem.field_errors(),
            form=form,
        )
    return redirect(url_for("owner.detalle", evento_id=evento_id), code=303)


@owner_bp.post("/eventos/<uuid:evento_id>/zonas/<uuid:zona_id>")
@exigir_rol("event-owner")
def editar_zona(
    evento_id: uuid.UUID, zona_id: uuid.UUID
) -> WerkzeugResponse | str | tuple[str, int]:
    """Edicion de una zona. Igual que el evento: el formulario manda todo."""
    form = _datos_del_formulario()
    try:
        cliente_api().patch(
            RUTA_ZONA.format(evento_id, zona_id),
            json=_cuerpo_de_zona(form),
        )
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _detalle(
            evento_id,
            aviso_texto=exc.problem.detail if not exc.problem.field_errors() else None,
            errores_zona=exc.problem.field_errors(),
            zona_abierta=zona_id,
            form=form,
        )
    return redirect(url_for("owner.detalle", evento_id=evento_id), code=303)


@owner_bp.post("/eventos/<uuid:evento_id>/zonas/<uuid:zona_id>/eliminar")
@exigir_rol("event-owner")
def eliminar_zona(
    evento_id: uuid.UUID, zona_id: uuid.UUID
) -> WerkzeugResponse | str | tuple[str, int]:
    """Borra la zona; el API la protege si ya tiene entradas asignadas."""
    try:
        cliente_api().delete(RUTA_ZONA.format(evento_id, zona_id))
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _detalle(evento_id, aviso_texto=exc.problem.detail)
    return redirect(url_for("owner.detalle", evento_id=evento_id), code=303)


def _accion(evento_id: uuid.UUID, ruta: str) -> WerkzeugResponse | str | tuple[str, int]:
    """Corre publicar/retirar y redirige; el veto del API se repinta en el detalle."""
    try:
        cliente_api().post(ruta)
    except ApiProblemError as exc:
        set_problem_code(exc.code)
        return _detalle(evento_id, aviso_texto=exc.problem.detail)
    return redirect(url_for("owner.detalle", evento_id=evento_id), code=303)


def _lista(
    aviso_texto: str | None = None,
    errores: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
) -> str | tuple[str, int]:
    """Listado actual, con el alta repoblada si su envio fue rechazado."""
    try:
        respuesta = cliente_api().get(RUTA_MINOS)
    except ApiProblemError as exc:
        # Un 401 aqui significa que la sesion del proveedor ya no vale. Se
        # pinta el aviso con el estado del fallo; el proximo ciclo de
        # renovacion la cerrara.
        set_problem_code(exc.code)
        return (
            render_template(
                "owner/eventos.html",
                eventos=[],
                aviso_texto=exc.problem.detail,
                errores={},
                form={},
            ),
            exc.status,
        )
    return render_template(
        "owner/eventos.html",
        eventos=respuesta.data,
        aviso_texto=aviso_texto,
        errores=errores or {},
        form=form or {},
        etiquetas=ETIQUETAS_DE_ESTADO,
    )


def _detalle(
    evento_id: uuid.UUID,
    aviso_texto: str | None = None,
    errores: dict[str, str] | None = None,
    errores_zona: dict[str, str] | None = None,
    zona_abierta: uuid.UUID | None = None,
    form: dict[str, str] | None = None,
) -> str | tuple[str, int]:
    """Detalle actual del evento con sus zonas.

    `errores` pinta junto a los campos del evento; `errores_zona` junto a los
    del formulario de zona, y `zona_abierta` dice cual de las formas de edicion
    los muestra, para que una zona no herede el error de otra. `form` repuebla
    lo que el usuario acaba de escribir cuando el API lo veto.
    """
    try:
        respuesta = cliente_api().get(RUTA_EVENTO.format(evento_id))
    except ApiProblemError as exc:
        # Un 403/404 aqui es un evento ajeno o inexistente: la pagina entera se
        # pinta con el motivo y el enlace de vuelta, no un listado que no
        # corresponde a este fallo.
        set_problem_code(exc.code)
        return (
            render_template(
                "owner/evento_detalle.html",
                evento=None,
                aviso_texto=exc.problem.detail,
                errores={},
                errores_zona={},
                zona_abierta=None,
                form={},
                etiquetas=ETIQUETAS_DE_ESTADO,
            ),
            exc.status,
        )
    evento = respuesta.data
    return render_template(
        "owner/evento_detalle.html",
        evento=evento,
        aviso_texto=aviso_texto,
        errores=errores or {},
        errores_zona=errores_zona or {},
        zona_abierta=zona_abierta,
        form=form or {},
        etiquetas=ETIQUETAS_DE_ESTADO,
    )


def _datos_del_formulario() -> dict[str, str]:
    """Los campos del formulario enviado, en texto, para repintarlos tal cual.

    No se valida nada aqui: si el dato esta mal el API lo dira con su codigo y
    su mensaje, y repetir la validacion en dos sitios es como para que una de
    las dos deje de coincidir sin que ninguna se entere.
    """
    return {
        clave: request.form.get(clave, "").strip()
        for clave in ("name", "description", "starts_at", "ends_at", "capacity", "price")
    }


def _cuerpo_del_evento(form: dict[str, str]) -> dict[str, Any]:
    """Body para crear/editar el evento.

    La descripcion vacia viaja como `null`: es la unica forma que el API tiene
    de borrar el texto ya publicado, y un string vacio lo dejaria en el sitio.
    Los instantes se mandan con zona horaria UTC para que el backend no tenga
    que adivinarla.
    """
    return {
        "name": form.get("name", ""),
        "description": form.get("description") or None,
        "starts_at": _instante_para_api(form.get("starts_at", "")),
        "ends_at": _instante_para_api(form.get("ends_at", "")),
    }


def _cuerpo_de_zona(form: dict[str, str]) -> dict[str, Any]:
    """Body para crear/editar la zona. El backend decide si el numero sirve."""
    return {
        "name": form.get("name", ""),
        "capacity": form.get("capacity", ""),
        "price": form.get("price", ""),
    }


def _instante_para_api(texto: str) -> str:
    """`datetime-local` sin zona horaria a un ISO con UTC explicito."""
    if not texto:
        return texto
    if len(texto) == 16:
        return texto + ":00+00:00"
    return texto


__all__ = [
    "RUTA_EVENTO",
    "RUTA_EVENTOS",
    "RUTA_MINOS",
    "RUTA_PUBLICAR",
    "RUTA_RETIRAR",
    "RUTA_ZONA",
    "RUTA_ZONAS",
    "owner_bp",
]
