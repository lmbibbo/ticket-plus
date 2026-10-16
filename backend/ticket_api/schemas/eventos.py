"""Pydantic v2 contracts for the event routes under `/api/v1/events`.

Same rule as the identity schemas: only what crosses the HTTP boundary lives
here, and the SQLAlchemy model is never used as a contract. Two validations are
decided at this layer because they never depend on stored state:

**Timezone awareness.** `starts_at` and `ends_at` are `timestamp with time zone`
columns. A naive datetime has no instant behind it, so PostgreSQL would have to
invent one from the server's `TimeZone`; two events created with the same
payload could land an hour apart depending on the host. Rejecting naive input
here turns that silent drift into a field error.

**Length bounds.** The name limits are the model's own constants: importing them
keeps the message the API returns and the limit the table enforces from ever
drifting apart. The window check (`ends_at > starts_at`) is *not* done here: a
`PATCH` may move only one of the two dates, and the comparison needs the value
currently stored, so it belongs to the service that can see both.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ticket_api.models.events import (
    ESCALA_PRECIO,
    EVENT_NAME_MAXIMO,
    PRECIO_MAXIMO,
    PRECISION_PRECIO,
    ZONE_NAME_MAXIMO,
)

#: Ceiling of the `capacity` column: PostgreSQL `Integer` is 4 bytes signed, and
#: a bigger value would pass every domain rule and die on insert as a 500 with
#: an "integer out of range" from the server.
MAXIMO_CAPACIDAD: int = 2_147_483_647


class EventoCreacionRequest(BaseModel):
    """Body of `POST /api/v1/events`.

    The event is born as a draft and owned by whoever is calling: there is no
    `owner_id` in the body on purpose. Letting the caller pick an owner would
    turn "create my event" into "create an event attributed to someone else",
    and the admin does not need it either, because an event it owns is still an
    event it can manage.

    `extra="forbid"` follows the identity schemas: an unknown field in a
    management form is a typo that would otherwise be dropped in silence.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=EVENT_NAME_MAXIMO,
        description="Event title as shown in the catalog. Normalized before storage.",
    )
    description: str | None = Field(
        default=None,
        description="Optional free text. `null` means no description.",
    )
    starts_at: AwareDatetime = Field(description="Window start, timezone required.")
    ends_at: AwareDatetime = Field(description="Window end, timezone required.")


class EventoEdicionRequest(BaseModel):
    """Body of `PATCH /api/v1/events/{evento_id}`: a partial update.

    Absent fields keep their value; `null` is rejected for `name` and the dates
    because they are non-nullable in the table, and accepting `null` here would
    only move the failure from a field error to a 500 on commit. `description`
    is the exception: `null` clears it, which is the only way to remove text
    that was already published.

    An empty body carries no intent and is rejected by the service rather than
    answered as a success: a form that lost its fields should not report that
    it saved them.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=EVENT_NAME_MAXIMO,
        description="New title. Omit it to keep the current one.",
    )
    description: str | None = Field(
        default=None,
        description="New description. Omit it to keep it, send `null` to clear it.",
    )
    starts_at: AwareDatetime | None = Field(
        default=None,
        description="New window start, timezone required. Omit it to keep it.",
    )
    ends_at: AwareDatetime | None = Field(
        default=None,
        description="New window end, timezone required. Omit it to keep it.",
    )


class EventoResponse(BaseModel):
    """How an event travels over HTTP from the management routes.

    `owner_id` is here because these routes exist for the owner and for the
    admin, both of whom need to see who owns the event. The public catalog of
    4.5 gets its own projection: exposing the owner to visitors would leak an
    internal identifier for no benefit.
    """

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    description: str | None
    starts_at: datetime
    ends_at: datetime
    status: str
    created_at: datetime
    updated_at: datetime


class ZonaCreacionRequest(BaseModel):
    """Body of `POST /api/v1/events/{evento_id}/zones`.

    Capacity and price carry their bounds here instead of relying on the table
    `CHECK`s: a negative capacity or a price with three decimals must come back
    as a field error the form can paint, not as an integrity violation — and for
    `price` the model's validator would otherwise raise `ValueError` during
    construction, which is a 500.

    `capacity` of zero is valid and deliberate: a zone closed to sales
    represents itself that way, and the table says so in its own note.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=ZONE_NAME_MAXIMO,
        description="Zone label within the event. Compared case-insensitively.",
    )
    capacity: int = Field(
        ge=0,
        le=MAXIMO_CAPACIDAD,
        description="Total entries the zone may ever assign. Zero means closed.",
    )
    price: Decimal = Field(
        ge=0,
        le=PRECIO_MAXIMO,
        max_digits=PRECISION_PRECIO,
        decimal_places=ESCALA_PRECIO,
        description="Non-negative price per entry, at most two decimals.",
    )


class ZonaEdicionRequest(BaseModel):
    """Body of `PATCH /api/v1/events/{evento_id}/zones/{zona_id}`.

    Same partial-update rules as the event edit: absent fields keep their
    value, `null` is rejected because the columns are not nullable, and an empty
    body is not a successful save.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=ZONE_NAME_MAXIMO,
        description="New label. Omit it to keep the current one.",
    )
    capacity: int | None = Field(
        default=None,
        ge=0,
        le=MAXIMO_CAPACIDAD,
        description="New capacity. Omit it to keep it.",
    )
    price: Decimal | None = Field(
        default=None,
        ge=0,
        le=PRECIO_MAXIMO,
        max_digits=PRECISION_PRECIO,
        decimal_places=ESCALA_PRECIO,
        description="New price. Omit it to keep it.",
    )


class ZonaResponse(BaseModel):
    """How a zone travels over the management routes.

    There is no "availability" field: available units are a derived number that
    belongs to 4.4, and publishing a field that the table cannot answer yet
    would make the contract lie until then.
    """

    id: uuid.UUID
    event_id: uuid.UUID
    name: str
    capacity: int
    price: Decimal
    created_at: datetime
    updated_at: datetime


class EventoConZonasResponse(EventoResponse):
    """Management detail: the event plus its zones.

    The provider's page of 7.3 edits zones from the event itself, so the read
    that feeds it has to carry them; one call per zone would be N round trips
    and a page that can half-render. Still not the public projection: this one
    keeps `owner_id`, because it is only ever answered to the owner or the
    admin. The catalog of 4.5 gets its own, without the owner.
    """

    zones: list[ZonaResponse]
