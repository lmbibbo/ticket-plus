"""Routes for event and zone management under `/api/v1/events`.

Thin like the other routers: validate the body, resolve the identity through
`GestorDep`, call the service, project the row. The role rule is not written
here either — the dependency carries it — and ownership is decided inside the
service, which is the only place that has already loaded the event.

Zones hang off the event path on purpose: a zone without an event means
nothing, and the nested id is what lets the service verify "this zone belongs
to that event" with one comparison.

The public catalog of 4.5 will add `GET /api/v1/events` on this same prefix
with a different projection: visitors see published events, never `owner_id`.
That route is deliberately not built here, because 4.2/4.3 are about managing
events, not about reading them for the public.

The reads that ARE here are the management ones, added for the provider views
of 7.3: `GET /mine` (what the caller may manage, drafts included) and
`GET /{evento_id}` (one event with its zones, after the ownership rule). They
cannot live behind the future catalog route — that one must never show a
draft, which is exactly what the provider's page exists to show.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from ticket_api.api.deps import GestorDep
from ticket_api.api.proyecciones import evento_gestion, evento_gestion_con_zonas, zona_gestion
from ticket_api.db.session import DbUnit
from ticket_api.schemas.eventos import (
    EventoConZonasResponse,
    EventoCreacionRequest,
    EventoEdicionRequest,
    EventoResponse,
    ZonaCreacionRequest,
    ZonaEdicionRequest,
    ZonaResponse,
)
from ticket_api.services import eventos

router = APIRouter(prefix="/api/v1/events", tags=["eventos"])


@router.get(
    "/mine",
    response_model=list[EventoConZonasResponse],
    summary="Eventos que el llamante puede gestionar",
)
def listar_mios(actor: GestorDep, unidad: DbUnit) -> list[EventoConZonasResponse]:
    """The caller's own events, any status; the admin sees every event.

    Registered before `/{evento_id}` so that the literal path wins the match
    against the uuid converter: `mine` is not a uuid, and letting the parameter
    route answer it would turn a working URL into a 422.
    """
    filas = eventos.listar_para_gestion(unidad, actor)
    return [evento_gestion_con_zonas(evento) for evento in filas]


@router.get(
    "/{evento_id}",
    response_model=EventoConZonasResponse,
    summary="Detalle de gestion de un evento con sus zonas",
)
def detalle(evento_id: uuid.UUID, actor: GestorDep, unidad: DbUnit) -> EventoConZonasResponse:
    """One event with its zones, for the owner or the admin.

    Ownership is the service's rule, not the router's: the same 403
    `forbidden_not_owner` that the mutations answer, so probing an id tells a
    stranger nothing new.
    """
    return evento_gestion_con_zonas(eventos.cargar_para_gestion(unidad, actor, evento_id))


@router.post(
    "",
    response_model=EventoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Creacion de un evento en estado draft",
)
def crear_evento(
    datos: EventoCreacionRequest, actor: GestorDep, unidad: DbUnit
) -> EventoResponse:
    """Creates a draft owned by the caller.

    201 and not 200: an event was added, and the `Location`-less body carries
    the id the owner needs for the next call (adding zones, editing, publishing).
    """
    return evento_gestion(eventos.crear(unidad, actor, datos))


@router.patch(
    "/{evento_id}",
    response_model=EventoResponse,
    summary="Edicion de un evento en estado draft",
)
def editar_evento(
    evento_id: uuid.UUID,
    datos: EventoEdicionRequest,
    actor: GestorDep,
    unidad: DbUnit,
) -> EventoResponse:
    """Partial update of a draft the caller owns (admin: any event)."""
    return evento_gestion(eventos.editar(unidad, actor, evento_id, datos))


@router.post(
    "/{evento_id}/publish",
    response_model=EventoResponse,
    summary="Publicacion de un evento draft",
)
def publicar_evento(evento_id: uuid.UUID, actor: GestorDep, unidad: DbUnit) -> EventoResponse:
    """Moves a zoned draft into the catalog."""
    return evento_gestion(eventos.publicar(unidad, actor, evento_id))


@router.post(
    "/{evento_id}/retire",
    response_model=EventoResponse,
    summary="Retiro de un evento publicado",
)
def retirar_evento(evento_id: uuid.UUID, actor: GestorDep, unidad: DbUnit) -> EventoResponse:
    """Removes a published event from the catalog. Terminal state."""
    return evento_gestion(eventos.retirar(unidad, actor, evento_id))


@router.post(
    "/{evento_id}/zones",
    response_model=ZonaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Alta de una zona de un evento",
)
def crear_zona(
    evento_id: uuid.UUID,
    datos: ZonaCreacionRequest,
    actor: GestorDep,
    unidad: DbUnit,
) -> ZonaResponse:
    """Adds a zone (label, capacity, price) to an event the caller manages.

    Nested under the event because a zone is meaningless without one, and
    because that is what makes ownership a single check instead of two: load
    the event, verify the caller may manage it, then verify the zone belongs
    to *that* event.
    """
    return zona_gestion(eventos.crear_zona(unidad, actor, evento_id, datos))


@router.patch(
    "/{evento_id}/zones/{zona_id}",
    response_model=ZonaResponse,
    summary="Actualizacion de una zona",
)
def editar_zona(
    evento_id: uuid.UUID,
    zona_id: uuid.UUID,
    datos: ZonaEdicionRequest,
    actor: GestorDep,
    unidad: DbUnit,
) -> ZonaResponse:
    """Partial update of a zone: label, capacity or price."""
    return zona_gestion(eventos.editar_zona(unidad, actor, evento_id, zona_id, datos))


@router.delete(
    "/{evento_id}/zones/{zona_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminacion de una zona sin entradas",
)
def eliminar_zona(
    evento_id: uuid.UUID, zona_id: uuid.UUID, actor: GestorDep, unidad: DbUnit
) -> Response:
    """Deletes a zone with no assigned entries; 409 `zone_has_tickets` otherwise."""
    eventos.eliminar_zona(unidad, actor, evento_id, zona_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
