"""Event lifecycle: create, edit, publish and retire, plus its zones.

Authorization is split in two on purpose, and the split mirrors the one in
`deps.py`:

- The **role** check lives in the route dependency (`GestorDep`): admin or
  event-owner, decided once for every route, so a new route cannot forget it.
- The **ownership** check lives here, because it can only be decided once the
  target event is loaded: it compares `event.owner_id` with the caller. The
  admin bypasses it, and which routes apply that bypass is a rule of this
  module, not of each view — `Actor.exigir_duenno` deliberately does not know
  about admins so that "the admin is exempt" cannot drift between endpoints.

Three state-machine decisions, all taken by the model or by the spec:

**Only drafts are edited.** `Event.es_editable` already says it, and the spec's
"estado editable" refers to it. Editing a published event would change what
visitors see without going through validation again, and editing a retired one
would rewrite history of an event that already left the catalog.

**Publishing requires at least one zone.** The spec scenario is explicit: a
draft *with zones, prices and capacity* becomes published. An event with no
zones is a title with nothing to buy, and putting it in the catalog would show
a detail page with no availability. Prices and capacities are already non-negative
by table `CHECK`s, so the zone count is the only condition left.

**Retire only from `published`.** Retirement removes an event from the catalog,
and a draft was never in it: retiring one would mark it as "was shown" when it
was not. `retired` is terminal — the model docstring records that a retired
event is not re-publishable, because it may already have sold tickets.

Ownership and state checks run in that order after the 404: a caller who is not
the owner gets `forbidden_not_owner` without learning whether the event exists
in a usable state, and a caller who is the owner learns which state it is in.

**Zones deliberately carry no state gate.** The requirement says "add and update
zones of an event" without conditioning it on the event's state, and the only
guard the spec demands is on deletion: a zone with assigned tickets cannot be
removed. Gating edits on `draft` would also make that guard unreachable — a
draft has no tickets, because sales only happen on published events — and the
contract already carries `zone_has_tickets` for exactly this case. Zone
operations are therefore: ownership (or admin), then data validation, then the
ticket check on delete.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import FieldError, ProblemException
from ticket_api.models import Event, EventStatus, Role, Ticket, Zone
from ticket_api.models.events import ZONE_NAME_MAXIMO, normalizar_nombre
from ticket_api.schemas.eventos import (
    EventoCreacionRequest,
    EventoEdicionRequest,
    ZonaCreacionRequest,
    ZonaEdicionRequest,
)
from ticket_api.services.autorizacion import Actor

#: Fields that the table declares `NOT NULL`. A `PATCH` sending `null` for any
#: of them would fail on commit with an integrity error; caught here it becomes
#: a field error the form can paint next to the input.
CAMPOS_OBLIGATORIOS: tuple[str, ...] = ("name", "starts_at", "ends_at")
CAMPOS_OBLIGATORIOS_DE_ZONA: tuple[str, ...] = ("name", "capacity", "price")

#: Unique index on `(event_id, lower(btrim(name)))`. The name is compared with
#: the same expression the index uses, so `General` and `general` collide in the
#: check and in the database the same way.
INDICE_NOMBRE_DE_ZONA: str = "uq_zones_event_nombre"

#: FK `tickets.zone_id` `ON DELETE RESTRICT` — the domain rule of 4.3 made
#: constraint. It fires when a ticket slips in between the count and the delete,
#: which is a real race and not a hypothetical one.
FK_ENTRADA_EN_ZONA: str = "tickets_zone_id_fkey"


def _no_encontrado(evento_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=404,
        code=ErrorCode.NOT_FOUND,
        title="Evento no encontrado",
        detail="No hay ningun evento con ese identificador.",
    )


def _estado_invalido(detalle: str) -> ProblemException:
    """409, not 403: the data is fine, the request does not apply to this state.

    Same reasoning as approving an already-active account in
    `services.administracion` — the web maps `invalid_state_transition` to a
    message about the current state instead of about permissions.
    """
    return ProblemException(
        status=409,
        code=ErrorCode.INVALID_STATE_TRANSITION,
        title="Operacion no aplicable",
        detail=detalle,
    )


def _sin_zonas() -> ProblemException:
    return ProblemException(
        status=409,
        code=ErrorCode.EVENT_NOT_PUBLISHABLE,
        title="Evento no publicable",
        detail="El evento necesita al menos una zona antes de publicarse.",
    )


def _dato_invalido(campo: str, mensaje: str) -> ProblemException:
    return ProblemException(
        status=422,
        code=ErrorCode.VALIDATION_ERROR,
        title="Solicitud invalida",
        detail=mensaje,
        errors=[FieldError(field=campo, message=mensaje)],
    )


def _cargar(unidad: UnitOfWork, evento_id: uuid.UUID) -> Event:
    evento = unidad.sesion.get(Event, evento_id)
    if evento is None:
        raise _no_encontrado(evento_id)
    return evento


def listar_para_gestion(unidad: UnitOfWork, actor: Actor) -> list[Event]:
    """Every event the caller may manage: their own, or all of them for the
    admin.

    Drafts and retired events are included on purpose — this is the management
    list, not the catalog, and the provider has to reach the drafts it cannot
    see anywhere else. Newest first: without an explicit order the database
    may answer in any order, and a list that reshuffles on every render looks
    broken even when it is correct.
    """
    consulta = select(Event)
    if not actor.es_uno_de(Role.ADMIN):
        consulta = consulta.where(Event.owner_id == actor.usuario_id)
    consulta = consulta.order_by(Event.created_at.desc(), Event.id)
    return list(unidad.sesion.scalars(consulta))


def cargar_para_gestion(unidad: UnitOfWork, actor: Actor, evento_id: uuid.UUID) -> Event:
    """The event with its zones, after the same ownership rule as the mutations.

    The zones come with the row because every caller of this read renders
    them: leaving the relationship lazy would fire one query per zone, and the
    session is the one place that can load them together.
    """
    evento = unidad.sesion.scalars(
        select(Event)
        .where(Event.id == evento_id)
        .options(selectinload(Event.zones))
    ).one_or_none()
    if evento is None:
        raise _no_encontrado(evento_id)
    _exigir_gestion(actor, evento)
    return evento


def _exigir_gestion(actor: Actor, evento: Event) -> None:
    """Owner or admin, checked after the event exists.

    `exigir_duenno` answers 403 with `forbidden_not_owner` rather than 404: a
    "not found" for someone else's event would confirm its existence to a
    caller who could then probe for it. The admin branch is written here so
    that every management route shares one exemption instead of each deciding.
    """
    if not actor.es_uno_de(Role.ADMIN):
        actor.exigir_duenno(evento.owner_id)


def _comprobar_ventana(inicio: datetime, fin: datetime) -> None:
    """`ends_at > starts_at`, before anything is written.

    The table has the same `CHECK`, but letting it fire would surface as an
    integrity error on commit — a 500 with no field. Checking here keeps it a
    422 pointing at `ends_at`.
    """
    if fin <= inicio:
        raise _dato_invalido(
            "ends_at", "La fecha de fin tiene que ser posterior a la de inicio."
        )


def crear(unidad: UnitOfWork, actor: Actor, datos: EventoCreacionRequest) -> Event:
    """New event in `draft`, owned by the caller.

    The window is checked before construction because the model validator for
    `name` raises during `Event(...)`, and two different failures should not
    share one error path.
    """
    _comprobar_ventana(datos.starts_at, datos.ends_at)
    try:
        evento = Event(
            owner_id=actor.usuario_id,
            name=datos.name,
            description=datos.description,
            starts_at=datos.starts_at,
            ends_at=datos.ends_at,
        )
    except ValueError as fallo:
        # `Event`'s only validator raises `ValueError` for the name (blank or
        # over the limit once normalized). Any other exception keeps its type
        # and becomes a 500, which is what an unmodelled defect should do.
        raise _dato_invalido("name", str(fallo)) from fallo
    unidad.sesion.add(evento)
    unidad.commit()
    return evento


def editar(
    unidad: UnitOfWork,
    actor: Actor,
    evento_id: uuid.UUID,
    datos: EventoEdicionRequest,
) -> Event:
    """Apply a partial update to a draft owned by the caller (or any, for admin)."""
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    if not evento.es_editable:
        raise _estado_invalido(
            f"El evento solo se edita en borrador: esta en {evento.status}."
        )

    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise _dato_invalido("body", "La peticion no incluye ningun cambio.")
    for campo in CAMPOS_OBLIGATORIOS:
        if campo in cambios and cambios[campo] is None:
            raise _dato_invalido(campo, f"El campo {campo} no admite ser nulo.")

    # The merged window: the check needs the stored value when the patch moves
    # only one of the two dates, which is why it cannot live in the schema.
    _comprobar_ventana(
        cambios.get("starts_at", evento.starts_at),
        cambios.get("ends_at", evento.ends_at),
    )

    try:
        for campo, valor in cambios.items():
            setattr(evento, campo, valor)
    except ValueError as fallo:
        raise _dato_invalido("name", str(fallo)) from fallo

    unidad.commit()
    return evento


def publicar(unidad: UnitOfWork, actor: Actor, evento_id: uuid.UUID) -> Event:
    """`draft` -> `published`, once the event has at least one zone."""
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    if evento.status == EventStatus.PUBLISHED.value:
        raise _estado_invalido("El evento ya esta publicado.")
    if evento.status == EventStatus.RETIRED.value:
        raise _estado_invalido("Un evento retirado no vuelve al catalogo.")
    if not evento.zones:
        raise _sin_zonas()
    evento.status = EventStatus.PUBLISHED.value
    unidad.commit()
    return evento


def retirar(unidad: UnitOfWork, actor: Actor, evento_id: uuid.UUID) -> Event:
    """`published` -> `retired`, terminal."""
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    if evento.status == EventStatus.RETIRED.value:
        raise _estado_invalido("El evento ya esta retirado.")
    if evento.status == EventStatus.DRAFT.value:
        raise _estado_invalido(
            "Solo se retira un evento publicado: un borrador nunca estuvo en el catalogo."
        )
    evento.status = EventStatus.RETIRED.value
    unidad.commit()
    return evento


def _zona_no_encontrada() -> ProblemException:
    """404 scoped to the event in the path.

    A zone that exists but belongs to another event answers as if it did not
    exist *under this event*: the caller already knows the event id, and the
    alternative would confirm the id of a zone of somebody else's event.
    """
    return ProblemException(
        status=404,
        code=ErrorCode.NOT_FOUND,
        title="Zona no encontrada",
        detail="No hay ninguna zona con ese identificador en ese evento.",
    )


def _zona_con_entradas() -> ProblemException:
    """409, not 403: the request is well-formed but does not apply — the zone
    already carries inventory, which the spec says must be preserved."""
    return ProblemException(
        status=409,
        code=ErrorCode.ZONE_HAS_TICKETS,
        title="Zona con entradas asignadas",
        detail="La zona ya tiene entradas asignadas y no se puede eliminar.",
    )


def _nombre_de_zona_ocupado() -> ProblemException:
    """422 with the error pinned to `name`.

    Not 409: unlike a duplicate email there is no dedicated code in the
    contract, and the fix belongs to the field the form submitted — another
    label. `validation_error` keeps the web's field mapping working without
    inventing a code that downstream clients would have to learn.
    """
    mensaje = "Ya existe una zona con ese nombre en este evento."
    return ProblemException(
        status=422,
        code=ErrorCode.VALIDATION_ERROR,
        title="Solicitud invalida",
        detail=mensaje,
        errors=[FieldError(field="name", message=mensaje)],
    )


def _constraint_de(fallo: IntegrityError) -> str | None:
    diag = getattr(fallo.orig, "diag", None)
    return getattr(diag, "constraint_name", None)


def _cargar_zona(unidad: UnitOfWork, evento: Event, zona_id: uuid.UUID) -> Zone:
    zona = unidad.sesion.get(Zone, zona_id)
    if zona is None or zona.event_id != evento.id:
        raise _zona_no_encontrada()
    return zona


def _cuenta_entradas(unidad: UnitOfWork, zona_id: uuid.UUID) -> int:
    return int(
        unidad.sesion.scalar(
            select(func.count()).select_from(Ticket).where(Ticket.zone_id == zona_id)
        )
        or 0
    )


def _nombre_de_zona(datos: str) -> str:
    """Normalize the label before anything touches the session.

    Doing it here and not by catching the model's `ValueError` keeps the field
    attribution exact: `Zone` validates the name *and* the price, and a bare
    `except ValueError` would have to guess which one failed. The schema already
    bounds the price, so a `ValueError` from it can only mean a rule this layer
    does not model — and that must surface as a 500, not as a mislabelled field
    error.
    """
    try:
        return normalizar_nombre(datos, ZONE_NAME_MAXIMO, "nombre de la zona")
    except ValueError as fallo:
        raise _dato_invalido("name", str(fallo)) from fallo


def crear_zona(
    unidad: UnitOfWork,
    actor: Actor,
    evento_id: uuid.UUID,
    datos: ZonaCreacionRequest,
) -> Zone:
    """New zone on an event the caller manages.

    The uniqueness of the name is left to the index and translated on commit
    rather than pre-checked: unlike Argon2id in registration there is nothing
    expensive to skip, and one code path cannot disagree with itself about what
    counts as a duplicate.
    """
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    zona = Zone(
        event_id=evento.id,
        name=_nombre_de_zona(datos.name),
        capacity=datos.capacity,
        price=datos.price,
    )
    unidad.sesion.add(zona)
    try:
        unidad.commit()
    except IntegrityError as fallo:
        if _constraint_de(fallo) == INDICE_NOMBRE_DE_ZONA:
            raise _nombre_de_zona_ocupado() from fallo
        raise
    return zona


def editar_zona(
    unidad: UnitOfWork,
    actor: Actor,
    evento_id: uuid.UUID,
    zona_id: uuid.UUID,
    datos: ZonaEdicionRequest,
) -> Zone:
    """Partial update of a zone: label, capacity or price."""
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    zona = _cargar_zona(unidad, evento, zona_id)

    cambios = datos.model_dump(exclude_unset=True)
    if not cambios:
        raise _dato_invalido("body", "La peticion no incluye ningun cambio.")
    for campo in CAMPOS_OBLIGATORIOS_DE_ZONA:
        if campo in cambios and cambios[campo] is None:
            raise _dato_invalido(campo, f"El campo {campo} no admite ser nulo.")
    if isinstance(cambios.get("name"), str):
        cambios["name"] = _nombre_de_zona(cambios["name"])

    for campo, valor in cambios.items():
        setattr(zona, campo, valor)

    try:
        unidad.commit()
    except IntegrityError as fallo:
        if _constraint_de(fallo) == INDICE_NOMBRE_DE_ZONA:
            raise _nombre_de_zona_ocupado() from fallo
        raise
    return zona


def eliminar_zona(
    unidad: UnitOfWork, actor: Actor, evento_id: uuid.UUID, zona_id: uuid.UUID
) -> None:
    """Delete a zone, unless it already has entries.

    The count is the fast, explanatory check; the `RESTRICT` FK is what makes
    the rule true when a sale commits in between the two, and translating its
    failure keeps that race a 409 instead of a 500. Either way the zone and its
    entries survive.
    """
    evento = _cargar(unidad, evento_id)
    _exigir_gestion(actor, evento)
    zona = _cargar_zona(unidad, evento, zona_id)
    if _cuenta_entradas(unidad, zona.id):
        raise _zona_con_entradas()
    unidad.sesion.delete(zona)
    try:
        unidad.commit()
    except IntegrityError as fallo:
        if _constraint_de(fallo) == FK_ENTRADA_EN_ZONA:
            raise _zona_con_entradas() from fallo
        raise


__all__ = [
    "cargar_para_gestion",
    "crear",
    "crear_zona",
    "editar",
    "editar_zona",
    "eliminar_zona",
    "listar_para_gestion",
    "publicar",
    "retirar",
]
