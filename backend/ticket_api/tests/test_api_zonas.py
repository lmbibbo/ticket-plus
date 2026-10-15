"""4.3 - Zone management over the API: label, capacity, price, and the
deletion guard.

Two things are verified here, and the second is the reason the task exists:

1. A zone is created and edited through the event it belongs to, with the
   ownership rule of 4.2 applying unchanged — a provider manages *its* events'
   zones, the admin manages everyone's, a client manages none.
2. **A zone with an assigned entry cannot be deleted.** The zone survives, the
   entry survives, and the answer is 409 `zone_has_tickets`. This is the one
   destructive-looking operation in the module where the spec draws a line:
   entries are already somebody's purchase history, and deleting their zone
   would orphan them.

Deletion is guarded with a count *and* translated from the FK failure: the
count is what produces the explanatory error in the normal path, and
`tickets_zone_id_fkey` (`ON DELETE RESTRICT`) is what keeps a sale that commits
in between from turning the race into a 500.

What is deliberately **not** gated is the event's state. The requirement says
"add and update zones of an event" with no condition on it, and gating on
`draft` would make the deletion guard unreachable — only a published event can
have tickets, and publishing requires at least one zone. So zones of published
events are editable and deletable-if-empty; that is pinned below as a decision,
not an oversight.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.db.base import Base
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE
from ticket_api.models import Event, EventStatus, Role, User, UserStatus, Zone
from ticket_api.models.sales import Order, Ticket
from ticket_api.security.passwords import hash_password
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

CONTRASENA = "contrasena-de-prueba-1"

INICIO = datetime(2027, 6, 1, 18, 0, tzinfo=UTC)
FIN = datetime(2027, 6, 1, 23, 0, tzinfo=UTC)

LOGIN = "/api/v1/auth/login"
EVENTOS = "/api/v1/events"
ZONAS = EVENTOS + "/{}/zones"
ZONA = ZONAS + "/{}"

#: Valid creation payload; tests override single fields from it.
CUERPO: dict[str, Any] = {"name": "General", "capacity": 100, "price": "25.00"}


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Real schema before and after each test: these operations write."""
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


@pytest.fixture
def cliente(
    app: Any,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """HTTP client against the API with the test engine behind it."""
    monkeypatch.setattr("ticket_api.db.session.get_sessionmaker", lambda: session_factory)
    with TestClient(app, raise_server_exceptions=False) as instancia:
        yield instancia


def _cuenta(
    sesion: Session,
    email: str,
    role: str = Role.CLIENT.value,
    status: str = UserStatus.ACTIVE.value,
) -> User:
    usuario = User(
        email=email,
        password_hash=hash_password(CONTRASENA),
        role=role,
        status=status,
    )
    sesion.add(usuario)
    sesion.commit()
    return usuario


def _entrar(cliente: TestClient, email: str, password: str = CONTRASENA) -> dict[str, Any]:
    respuesta = cliente.post(LOGIN, json={"email": email, "password": password})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


def _con(emitido: dict[str, Any]) -> dict[str, str]:
    """`Authorization` header from an already-done login."""
    return {"Authorization": f"Bearer {emitido['access_token']}"}


def _proveedor(cliente: TestClient, sesion: Session) -> dict[str, str]:
    _cuenta(sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value)
    return _con(_entrar(cliente, "proveedor@ejemplo.com"))


def _jefe(cliente: TestClient, sesion: Session) -> dict[str, str]:
    _cuenta(sesion, "jefe@ejemplo.com", role=Role.ADMIN.value)
    return _con(_entrar(cliente, "jefe@ejemplo.com"))


def _persona(cliente: TestClient, sesion: Session) -> dict[str, str]:
    _cuenta(sesion, "persona@ejemplo.com")
    return _con(_entrar(cliente, "persona@ejemplo.com"))


def _evento(
    sesion: Session,
    duenio: User,
    *,
    nombre: str = "Feria de primavera",
    estado: str = EventStatus.DRAFT.value,
) -> Event:
    evento = Event(
        owner_id=duenio.id,
        name=nombre,
        starts_at=INICIO,
        ends_at=FIN,
        status=estado,
    )
    sesion.add(evento)
    sesion.commit()
    return evento


def _zona(
    sesion: Session,
    evento: Event,
    *,
    nombre: str = "General",
    capacidad: int = 100,
    precio: str = "25.00",
) -> Zone:
    zona = Zone(event_id=evento.id, name=nombre, capacity=capacidad, price=Decimal(precio))
    sesion.add(zona)
    sesion.commit()
    return zona


def _entrada(sesion: Session, evento: Event, zona: Zone, comprador: User) -> Ticket:
    """One sold ticket, inserted the way 4.4/5.x will insert it.

    Straight into the database because buying is not built yet: this task
    guards against the row existing, not against the checkout flow, and
    building the purchase API here would make 4.3 unreviewable as its own
    change — the same reason 4.2 inserted zones instead of building them.
    """
    pedido = Order(
        client_id=comprador.id,
        event_id=evento.id,
        idempotency_key="compra-de-prueba",
        request_hash="a" * 64,
        total=Decimal("50.00"),
    )
    sesion.add(pedido)
    sesion.flush()
    entrada = Ticket(
        order_id=pedido.id,
        zone_id=zona.id,
        client_id=comprador.id,
        number=1,
        nonce="nonce-de-prueba",
    )
    sesion.add(entrada)
    sesion.commit()
    return entrada


def _zona_en_base(sesion: Session, zona_id: uuid.UUID) -> Zone | None:
    """Fresh read: the factory commits without expiring, so the identity map
    would happily return the row as it was before the API changed it."""
    sesion.expire_all()
    return sesion.get(Zone, zona_id)


def _total_zonas(sesion: Session) -> int:
    sesion.expire_all()
    return int(sesion.scalar(select(func.count()).select_from(Zone)) or 0)


def _problema(respuesta: Any) -> dict[str, Any]:
    assert respuesta.status_code >= 400, respuesta.text
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


class TestCreacion:
    def test_el_proveedor_crea_una_zona_en_su_evento(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a provider with its own event / WHEN it adds a zone / THEN
        201 with the values persisted under that event."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO, headers=cabeceras
        )

        assert respuesta.status_code == 201, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["name"] == "General"
        assert cuerpo["capacity"] == 100
        # The model sends Decimal to JSON as a string, so the contract shows
        # the price as text and the web must parse it.
        assert cuerpo["price"] == "25.00"
        assert cuerpo["event_id"] == str(evento.id)
        guardada = _zona_en_base(sesion, uuid.UUID(cuerpo["id"]))
        assert guardada is not None
        assert guardada.event_id == evento.id

    def test_el_administrador_crea_una_zona_ajena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO, headers=cabeceras
        )

        assert respuesta.status_code == 201, respuesta.text

    def test_un_cliente_no_crea_zonas(self, cliente: TestClient, sesion: Session) -> None:
        """GIVEN a client / WHEN it tries / THEN denied and no zone exists."""
        cabeceras = _persona(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden"
        assert _total_zonas(sesion) == 0

    def test_otro_proveedor_no_crea_zonas_en_evento_ajeno(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        assert _total_zonas(sesion) == 0

    def test_sin_autenticar_no_crea(self, cliente: TestClient, sesion: Session) -> None:
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(ZONAS.format(evento.id), json=CUERPO)

        assert _problema(respuesta)["code"] == "unauthenticated"
        assert _total_zonas(sesion) == 0

    def test_un_evento_inexistente_es_404(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)

        respuesta = cliente.post(ZONAS.format(uuid.uuid4()), json=CUERPO, headers=cabeceras)

        assert _problema(respuesta)["code"] == "not_found"
        assert _total_zonas(sesion) == 0

    def test_una_capacidad_negativa_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Checked in the schema so the error carries the field; a negative
        capacity is a zone that hands out more entries than it has."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"capacity": -1}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["code"] == "validation_error"
        assert cuerpo["errors"][0]["field"] == "capacity"
        assert _total_zonas(sesion) == 0

    def test_un_precio_negativo_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A paid event that refunds every sale is not a price."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"price": "-0.01"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "price"
        assert _total_zonas(sesion) == 0

    def test_un_precio_con_tres_decimales_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Money here has two decimals, like the column. Rounding silently
        would invent money that nobody agreed to."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"price": "10.555"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "price"
        assert _total_zonas(sesion) == 0

    def test_un_nombre_duplicado_distinto_en_mayusculas_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """`General` and `general` are the same label to a human, and the
        unique index compares them with the same expression it stores."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        _zona(sesion, evento, nombre="General")

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"name": "general"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "name"
        assert _total_zonas(sesion) == 1

    def test_un_nombre_de_solo_espacios_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Whitespace passes the schema's `min_length` — the model's
        normalizer is what rejects it, and that failure must arrive as a field
        error instead of a 500 from the validator."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"name": "   "}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "name"
        assert _total_zonas(sesion) == 0

    def test_un_campo_desconocido_se_rechaza(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A forgotten field in a management form is a typo; dropping it in
        silence would hide the error until someone finds it in the data."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"available": 5}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "validation_error"
        assert _total_zonas(sesion) == 0

    def test_una_capacidad_cero_es_valida(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Zero is how a zone closes itself to sales; the table says so in its
        own note, and rejecting it here would invent a rule."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id), json=CUERPO | {"capacity": 0}, headers=cabeceras
        )

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["capacity"] == 0

    def test_el_nombre_se_guarda_normalizado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Padding and inner runs of spaces collapse, exactly as in the event
        title: one label, stored once."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(
            ZONAS.format(evento.id),
            json=CUERPO | {"name": "  Platea   Alta  "},
            headers=cabeceras,
        )

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["name"] == "Platea Alta"


class TestEdicion:
    def test_se_actualizan_capacidad_y_precio(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a provider with a zone / WHEN it edits capacity and price /
        THEN both are persisted."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id),
            json={"capacity": 40, "price": "19.90"},
            headers=cabeceras,
        )

        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["capacity"] == 40
        assert cuerpo["price"] == "19.90"
        guardada = _zona_en_base(sesion, zona.id)
        assert guardada is not None
        assert guardada.capacity == 40
        assert guardada.price == Decimal("19.90")

    def test_una_edicion_parcial_conserva_lo_que_no_se_manda(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"name": "Platea"}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["name"] == "Platea"
        assert respuesta.json()["capacity"] == 100
        assert respuesta.json()["price"] == "25.00"

    def test_el_administrador_edita_una_zona_ajena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"capacity": 50}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text

    def test_otro_proveedor_no_edita(self, cliente: TestClient, sesion: Session) -> None:
        """GIVEN another provider / WHEN it edits a zone it does not own /
        THEN `forbidden_not_owner` and the row is unchanged."""
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"capacity": 1}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        guardada = _zona_en_base(sesion, zona.id)
        assert guardada is not None
        assert guardada.capacity == 100

    def test_un_cliente_no_edita(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _persona(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"capacity": 1}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden"
        guardada = _zona_en_base(sesion, zona.id)
        assert guardada is not None
        assert guardada.capacity == 100

    def test_renombrar_a_un_nombre_existente_no_pasa(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Two labels the unique index sees as equal: rejected at commit and
        translated to the field, with the row untouched."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        _zona(sesion, evento, nombre="Platea")
        otra = _zona(sesion, evento, nombre="General")

        respuesta = cliente.patch(
            ZONA.format(evento.id, otra.id), json={"name": "platea"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "name"
        guardada = _zona_en_base(sesion, otra.id)
        assert guardada is not None
        assert guardada.name == "General"

    def test_se_puede_renombrar_a_su_propio_nombre(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """An edit that changes nothing is still a valid save: the index
        compares a row against the others, not against itself."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"name": "General"}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text

    def test_editar_la_capacidad_a_nulo_no_pasa(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """`null` for a NOT NULL column becomes a field error here, not an
        integrity error on commit."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"capacity": None}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "capacity"

    def test_una_edicion_vacia_no_reporta_exito(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A form that lost its fields must not answer "saved"."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(ZONA.format(evento.id, zona.id), json={}, headers=cabeceras)

        assert _problema(respuesta)["code"] == "validation_error"

    def test_una_capacidad_negativa_en_edicion_no_se_aplica(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.patch(
            ZONA.format(evento.id, zona.id), json={"capacity": -5}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "validation_error"
        guardada = _zona_en_base(sesion, zona.id)
        assert guardada is not None
        assert guardada.capacity == 100

    def test_una_zona_de_otro_evento_es_404(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """The zone exists but under a different event: under *this* URL it
        must answer as not found, so the caller learns nothing about the other
        event's inventory."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, nombre="Primer evento")
        otro = _evento(sesion, duenio, nombre="Segundo evento")
        ajena = _zona(sesion, otro)

        respuesta = cliente.patch(
            ZONA.format(evento.id, ajena.id), json={"capacity": 5}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "not_found"
        guardada = _zona_en_base(sesion, ajena.id)
        assert guardada is not None
        assert guardada.capacity == 100


class TestEliminacion:
    def test_se_elimina_una_zona_sin_entradas(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a zone nobody bought from / WHEN the owner deletes it / THEN
        204 and the row is gone."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.delete(ZONA.format(evento.id, zona.id), headers=cabeceras)

        assert respuesta.status_code == 204, respuesta.text
        assert _zona_en_base(sesion, zona.id) is None

    def test_no_se_elimina_una_zona_con_entradas_asignadas(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """The task's requirement: a zone with an assigned entry cannot be
        removed. The zone and the entry both survive — an entry is somebody's
        purchase, and deleting the zone under it would orphan the record."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)
        comprador = _cuenta(sesion, "comprador@ejemplo.com")
        entrada = _entrada(sesion, evento, zona, comprador)

        respuesta = cliente.delete(ZONA.format(evento.id, zona.id), headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "zone_has_tickets"
        assert _zona_en_base(sesion, zona.id) is not None
        sesion.expire_all()
        assert sesion.get(Ticket, entrada.id) is not None

    def test_el_administrador_elimina_una_zona_ajena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.delete(ZONA.format(evento.id, zona.id), headers=cabeceras)

        assert respuesta.status_code == 204, respuesta.text
        assert _zona_en_base(sesion, zona.id) is None

    def test_otro_proveedor_no_elimina(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)
        zona = _zona(sesion, evento)

        respuesta = cliente.delete(ZONA.format(evento.id, zona.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        assert _zona_en_base(sesion, zona.id) is not None

    def test_un_cliente_no_elimina(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _persona(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        zona = _zona(sesion, evento)

        respuesta = cliente.delete(ZONA.format(evento.id, zona.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden"
        assert _zona_en_base(sesion, zona.id) is not None

    def test_una_zona_inexistente_es_404(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.delete(ZONA.format(evento.id, uuid.uuid4()), headers=cabeceras)

        assert _problema(respuesta)["code"] == "not_found"

    def test_una_zona_de_otro_evento_no_se_elimina(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Same scoping as the edit: under this event's URL, somebody else's
        zone does not exist."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, nombre="Primer evento")
        otro = _evento(sesion, duenio, nombre="Segundo evento")
        ajena = _zona(sesion, otro)

        respuesta = cliente.delete(ZONA.format(evento.id, ajena.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "not_found"
        assert _zona_en_base(sesion, ajena.id) is not None

    def test_se_puede_quitar_una_zona_sin_entradas_de_un_evento_publicado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Pins the deliberate absence of a state gate (see the module
        docstring): the requirement conditions deletion on entries, not on the
        event's status, and a published event may well retire a zone nobody
        bought from while it is still open for the other zones."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.PUBLISHED.value)
        _zona(sesion, evento, nombre="Carpa")
        vacia = _zona(sesion, evento, nombre="Cesped")

        respuesta = cliente.delete(ZONA.format(evento.id, vacia.id), headers=cabeceras)

        assert respuesta.status_code == 204, respuesta.text
        assert _zona_en_base(sesion, vacia.id) is None
        assert _total_zonas(sesion) == 1
