"""Projection of domain rows to what travels over HTTP.

It lives outside the routers because more than one returns each row — accounts
in the identity and admin routers, events and zones in the management router
and, in 4.5, in the catalog router. Duplicating the projection in each one
would be two field lists to keep in sync, and one forgotten field is enough for
two routers to answer differently.

Each projection is explicit, which is why there are several: what an admin
managing an event may see is not what a visitor in the catalog may see, and a
single function with parameters would move that decision into every call site.
"""

from __future__ import annotations

from ticket_api.models import Event, User, Zone
from ticket_api.schemas.eventos import EventoConZonasResponse, EventoResponse, ZonaResponse
from ticket_api.schemas.identity import UsuarioResponse


def usuario_publico(usuario: User) -> UsuarioResponse:
    """Proyeccion explicita del modelo a la respuesta.

    Se escribe campo a campo en vez de usar `model_validate` con
    `from_attributes`: es el unico sitio donde se decide que de una fila de
    `users` se puede ver, y anadir una columna a la tabla no puede hacer que
    aparezca sola en la respuesta.
    """
    return UsuarioResponse(
        id=usuario.id,
        email=usuario.email,
        role=usuario.role,
        status=usuario.status,
        created_at=usuario.created_at,
    )


def evento_gestion(evento: Event) -> EventoResponse:
    """Projection used by the management routes: everything the owner and the
    admin need to see, and nothing else.

    Same reason as `usuario_publico`: written field by field, so a new column
    on `events` cannot leak into a response. The public catalog gets its own
    projection (4.5) — `owner_id` must not reach a visitor.
    """
    return EventoResponse(
        id=evento.id,
        owner_id=evento.owner_id,
        name=evento.name,
        description=evento.description,
        starts_at=evento.starts_at,
        ends_at=evento.ends_at,
        status=evento.status,
        created_at=evento.created_at,
        updated_at=evento.updated_at,
    )


def evento_gestion_con_zonas(evento: Event) -> EventoConZonasResponse:
    """The same management projection plus the zones, for the reads of 7.3.

    Sorted by name, case-insensitively: the relationship carries no order, and
    a list that changes order between renders is a page that looks unstable
    even when every row is correct. The tiebreak by id keeps two zones with
    names that fold together (which the unique index does not allow, but a
    legacy row could) from flipping between visits.
    """
    base = evento_gestion(evento)
    zonas = sorted(evento.zones, key=lambda z: (z.name.casefold(), str(z.id)))
    return EventoConZonasResponse(
        **base.model_dump(),
        zones=[zona_gestion(zona) for zona in zonas],
    )


def zona_gestion(zona: Zone) -> ZonaResponse:
    """Projection for the zone routes: label, capacity and price.

    Same field-by-field rule as the others. `event_id` is included because the
    caller built the URL from it and the response should confirm which event
    the zone ended up under.
    """
    return ZonaResponse(
        id=zona.id,
        event_id=zona.event_id,
        name=zona.name,
        capacity=zona.capacity,
        price=zona.price,
        created_at=zona.created_at,
        updated_at=zona.updated_at,
    )


__all__ = ["evento_gestion", "evento_gestion_con_zonas", "usuario_publico", "zona_gestion"]
