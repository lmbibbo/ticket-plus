"""4.2 - Creating, editing, publishing and retiring events over the API.

What is verified is the state machine and the ownership rule, not the shape of
the response: that a new event is born `draft` and owned by the caller, that
only a draft is editable, that publishing needs zones, that `retired` is
terminal, and above all that **a request denied by ownership or by role leaves
the database untouched** — a 403 accompanied by a state change is a security
defect, not a contract defect.

Zones are inserted straight into the database because zone management is 4.3:
publishing needs the precondition ("a draft with zones, prices and capacity"),
and building the zone API here would make 4.3 unreviewable as its own change.
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
from ticket_api.security.passwords import hash_password
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

CONTRASENA = "contrasena-de-prueba-1"

#: A window far enough in the future that no run can age past it.
INICIO = datetime(2027, 6, 1, 18, 0, tzinfo=UTC)
FIN = datetime(2027, 6, 1, 23, 0, tzinfo=UTC)

LOGIN = "/api/v1/auth/login"
EVENTOS = "/api/v1/events"
PUBLICAR = EVENTOS + "/{}/publish"
RETIRAR = EVENTOS + "/{}/retire"


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


def _zona(sesion: Session, evento: Event) -> Zone:
    zona = Zone(event_id=evento.id, name="General", capacity=100, price=Decimal("25.00"))
    sesion.add(zona)
    sesion.commit()
    return zona


def _estado_en_base(sesion: Session, evento_id: uuid.UUID) -> str:
    """Fresh read: the factory commits without expiring, so the identity map
    would happily return the row as it was before the API changed it."""
    sesion.expire_all()
    evento = sesion.get(Event, evento_id)
    assert evento is not None
    return evento.status


def _total_eventos(sesion: Session) -> int:
    sesion.expire_all()
    return int(sesion.scalar(select(func.count()).select_from(Event)) or 0)


def _problema(respuesta: Any) -> dict[str, Any]:
    assert respuesta.status_code >= 400, respuesta.text
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


def _cuerpo(
    inicio: str = "2027-06-01T18:00:00Z",
    fin: str = "2027-06-01T23:00:00Z",
) -> dict[str, Any]:
    return {"name": "Feria de primavera", "starts_at": inicio, "ends_at": fin}


class TestCreacion:
    def test_el_proveedor_crea_un_evento_en_borrador(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a provider / WHEN it creates an event / THEN it is `draft` and
        belongs to that account."""
        cabeceras = _proveedor(cliente, sesion)

        respuesta = cliente.post(EVENTOS, json=_cuerpo(), headers=cabeceras)

        assert respuesta.status_code == 201, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["status"] == EventStatus.DRAFT.value
        assert cuerpo["name"] == "Feria de primavera"
        propietario = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert propietario is not None
        assert cuerpo["owner_id"] == str(propietario.id)
        assert _estado_en_base(sesion, uuid.UUID(cuerpo["id"])) == EventStatus.DRAFT.value

    def test_el_nombre_se_guarda_normalizado(self, cliente: TestClient, sesion: Session) -> None:
        """Padding and inner runs of spaces collapse; the title is stored once."""
        cabeceras = _proveedor(cliente, sesion)
        datos = _cuerpo() | {"name": "  Fiesta   Primavera  "}

        respuesta = cliente.post(EVENTOS, json=datos, headers=cabeceras)

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["name"] == "Fiesta Primavera"

    def test_el_administrador_tambien_puede_crear(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)

        respuesta = cliente.post(EVENTOS, json=_cuerpo(), headers=cabeceras)

        assert respuesta.status_code == 201, respuesta.text
        jefe = sesion.scalar(select(User).where(User.email == "jefe@ejemplo.com"))
        assert jefe is not None
        assert respuesta.json()["owner_id"] == str(jefe.id)

    def test_un_cliente_no_crea_eventos(self, cliente: TestClient, sesion: Session) -> None:
        """GIVEN a client / WHEN it tries to create / THEN denied and no event exists."""
        cabeceras = _persona(cliente, sesion)

        respuesta = cliente.post(EVENTOS, json=_cuerpo(), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden"
        assert _total_eventos(sesion) == 0

    def test_sin_autenticar_no_crea(self, cliente: TestClient, sesion: Session) -> None:
        respuesta = cliente.post(EVENTOS, json=_cuerpo())

        assert _problema(respuesta)["code"] == "unauthenticated"
        assert _total_eventos(sesion) == 0

    def test_una_fecha_de_fin_anterior_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """The window is checked before anything is written, and the error
        points at the field so the form can paint it."""
        cabeceras = _proveedor(cliente, sesion)
        datos = _cuerpo(inicio="2027-06-01T23:00:00Z", fin="2027-06-01T18:00:00Z")

        respuesta = cliente.post(EVENTOS, json=datos, headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["code"] == "validation_error"
        assert cuerpo["errors"][0]["field"] == "ends_at"
        assert _total_eventos(sesion) == 0

    def test_un_nombre_solo_de_espacios_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Whitespace passes the schema's `min_length` — the model's
        normalizer is what rejects it, and that failure must arrive as a field
        error instead of a 500 from the validator."""
        cabeceras = _proveedor(cliente, sesion)
        datos = _cuerpo() | {"name": "   "}

        respuesta = cliente.post(EVENTOS, json=datos, headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "name"
        assert _total_eventos(sesion) == 0

    def test_una_fecha_sin_zona_horaria_no_se_acepta(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A naive datetime has no instant behind it; PostgreSQL would place it
        using the server's timezone and two equal payloads could differ."""
        cabeceras = _proveedor(cliente, sesion)
        datos = _cuerpo(inicio="2027-06-01T18:00:00", fin="2027-06-01T23:00:00")

        respuesta = cliente.post(EVENTOS, json=datos, headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["code"] == "validation_error"
        assert _total_eventos(sesion) == 0

    def test_un_campo_desconocido_se_rechaza(self, cliente: TestClient, sesion: Session) -> None:
        """A forgotten field in a management form is a typo; dropping it in
        silence would hide the error until someone finds it in the data."""
        cabeceras = _proveedor(cliente, sesion)
        datos = _cuerpo() | {"status": "published"}

        respuesta = cliente.post(EVENTOS, json=datos, headers=cabeceras)

        assert _problema(respuesta)["code"] == "validation_error"
        assert _total_eventos(sesion) == 0

    def test_no_se_puede_encolar_el_estado_en_la_creacion(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Draft is not a request parameter: publishing is a separate,
        authorized step, so `status` is simply not part of the contract."""
        cabeceras = _proveedor(cliente, sesion)

        respuesta = cliente.post(EVENTOS, json=_cuerpo(), headers=cabeceras)

        assert respuesta.json()["status"] == EventStatus.DRAFT.value


class TestEdicion:
    def test_el_dueno_edita_su_borrador(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}",
            json={"name": "Feria de otono", "description": "Tercera edicion"},
            headers=cabeceras,
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["name"] == "Feria de otono"
        sesion.expire_all()
        guardado = sesion.get(Event, evento.id)
        assert guardado is not None
        assert guardado.name == "Feria de otono"
        assert guardado.description == "Tercera edicion"

    def test_una_edicion_parcial_conserva_lo_que_no_se_manda(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, nombre="Feria vieja")

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Feria nueva"}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["name"] == "Feria nueva"
        assert respuesta.json()["starts_at"].startswith("2027-06-01")

    def test_la_descripcion_se_puede_vaciar(self, cliente: TestClient, sesion: Session) -> None:
        """`null` clears the description; it is the one nullable field."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        sesion.expire_all()
        guardado = sesion.get(Event, evento.id)
        assert guardado is not None
        guardado.description = "texto a borrar"
        sesion.commit()

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"description": None}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["description"] is None

    def test_el_administrador_edita_un_evento_ajeno(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN an admin and someone else's event / WHEN it edits / THEN allowed."""
        cabeceras = _jefe(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Corregido"}, headers=cabeceras
        )

        assert respuesta.status_code == 200, respuesta.text

    def test_otro_proveedor_no_edita(self, cliente: TestClient, sesion: Session) -> None:
        """GIVEN another provider / WHEN it edits an event it does not own /
        THEN `forbidden_not_owner` and the row is unchanged."""
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Secuestrado"}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        sesion.expire_all()
        guardado = sesion.get(Event, evento.id)
        assert guardado is not None
        assert guardado.name == "Feria de primavera"

    def test_un_cliente_no_edita(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _persona(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Nada"}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "forbidden"
        sesion.expire_all()
        guardado = sesion.get(Event, evento.id)
        assert guardado is not None
        assert guardado.name == "Feria de primavera"

    def test_no_se_edita_un_evento_publicado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A published event is what visitors see; editing it behind their back
        would skip the validation that publishing went through."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.PUBLISHED.value)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Cambiado"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, evento.id) == EventStatus.PUBLISHED.value

    def test_no_se_edita_un_evento_retirado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.RETIRED.value)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "Cambiado"}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, evento.id) == EventStatus.RETIRED.value

    def test_una_edicion_que_invierte_la_ventana_no_se_aplica(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Moving only `ends_at` backwards still has to compare against the
        stored `starts_at`, which is exactly why the window is checked in the
        service and not in the schema."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"ends_at": "2027-06-01T17:00:00Z"}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "ends_at"
        assert _estado_en_base(sesion, evento.id) == EventStatus.DRAFT.value

    def test_un_nombre_de_solo_espacios_tampoco_se_edita(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": "  "}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "validation_error"
        sesion.expire_all()
        guardado = sesion.get(Event, evento.id)
        assert guardado is not None
        assert guardado.name == "Feria de primavera"

    def test_una_edicion_vacia_no_reporta_exito(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """A form that lost its fields must not answer "saved"."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(EVENTOS + f"/{evento.id}", json={}, headers=cabeceras)

        assert _problema(respuesta)["code"] == "validation_error"

    def test_editar_el_nombre_a_nulo_no_pasa(self, cliente: TestClient, sesion: Session) -> None:
        """`null` for a NOT NULL column becomes a field error here, not an
        integrity error on commit."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.patch(
            EVENTOS + f"/{evento.id}", json={"name": None}, headers=cabeceras
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["errors"][0]["field"] == "name"

    def test_un_evento_inexistente_es_404(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)

        respuesta = cliente.patch(
            EVENTOS + f"/{uuid.uuid4()}", json={"name": "Nada"}, headers=cabeceras
        )

        assert _problema(respuesta)["code"] == "not_found"


class TestPublicacion:
    def test_publicar_pasa_de_borrador_a_publicado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a draft with zones / WHEN the owner publishes / THEN
        `published`."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        _zona(sesion, evento)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == EventStatus.PUBLISHED.value
        assert _estado_en_base(sesion, evento.id) == EventStatus.PUBLISHED.value

    def test_el_administrador_publica_un_evento_ajeno(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        _zona(sesion, evento)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert _estado_en_base(sesion, evento.id) == EventStatus.PUBLISHED.value

    def test_no_se_publica_sin_zonas(self, cliente: TestClient, sesion: Session) -> None:
        """The spec's scenario is a draft *with zones, prices and capacity*; a
        title with nothing to buy must not reach the catalog."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "event_not_publishable"
        assert _estado_en_base(sesion, evento.id) == EventStatus.DRAFT.value

    def test_no_se_publica_dos_veces(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.PUBLISHED.value)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"

    def test_un_evento_retirado_no_vuelve_a_publicarse(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """`retired` is terminal: it may already have sold tickets, and
        re-publishing would put it back without re-validating it."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.RETIRED.value)
        _zona(sesion, evento)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, evento.id) == EventStatus.RETIRED.value

    def test_otro_proveedor_no_publica(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)
        _zona(sesion, evento)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        assert _estado_en_base(sesion, evento.id) == EventStatus.DRAFT.value

    def test_un_cliente_no_publica(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _persona(cliente, sesion)
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio)
        _zona(sesion, evento)

        respuesta = cliente.post(PUBLICAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden"
        assert _estado_en_base(sesion, evento.id) == EventStatus.DRAFT.value


class TestRetiro:
    def test_retirar_pasa_de_publicado_a_retirado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a published event / WHEN the owner retires it / THEN
        `retired`."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.PUBLISHED.value)

        respuesta = cliente.post(RETIRAR.format(evento.id), headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == EventStatus.RETIRED.value
        assert _estado_en_base(sesion, evento.id) == EventStatus.RETIRED.value

    def test_no_se_retira_un_borrador(self, cliente: TestClient, sesion: Session) -> None:
        """Retirement removes from the catalog; a draft was never in it."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        respuesta = cliente.post(RETIRAR.format(evento.id), headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, evento.id) == EventStatus.DRAFT.value

    def test_no_se_retira_dos_veces(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio, estado=EventStatus.RETIRED.value)

        respuesta = cliente.post(RETIRAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"

    def test_otro_proveedor_no_retira(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno, estado=EventStatus.PUBLISHED.value)

        respuesta = cliente.post(RETIRAR.format(evento.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        assert _estado_en_base(sesion, evento.id) == EventStatus.PUBLISHED.value

    def test_sin_autenticar_no_retira(self, cliente: TestClient, sesion: Session) -> None:
        duenio = _cuenta(sesion, "duenio@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, duenio, estado=EventStatus.PUBLISHED.value)

        respuesta = cliente.post(RETIRAR.format(evento.id))

        assert _problema(respuesta)["code"] == "unauthenticated"
        assert _estado_en_base(sesion, evento.id) == EventStatus.PUBLISHED.value


class TestCicloDeVida:
    def test_el_evento_recorre_draft_published_retired(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """The three states of the task, in order, on one event: created as
        draft (editable), published once a zone exists (no longer editable),
        retired (terminal). Each step is read back from the database, not from
        the response body."""
        cabeceras = _proveedor(cliente, sesion)

        creado = cliente.post(EVENTOS, json=_cuerpo(), headers=cabeceras)
        assert creado.status_code == 201, creado.text
        evento_id = uuid.UUID(creado.json()["id"])
        assert _estado_en_base(sesion, evento_id) == EventStatus.DRAFT.value

        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        fila = sesion.get(Event, evento_id)
        assert fila is not None
        _zona(sesion, fila)

        publicado = cliente.post(PUBLICAR.format(evento_id), headers=cabeceras)
        assert publicado.status_code == 200, publicado.text
        assert _estado_en_base(sesion, evento_id) == EventStatus.PUBLISHED.value

        editado = cliente.patch(
            EVENTOS + f"/{evento_id}", json={"name": "No debe entrar"}, headers=cabeceras
        )
        assert editado.status_code == 409

        retirado = cliente.post(RETIRAR.format(evento_id), headers=cabeceras)
        assert retirado.status_code == 200, retirado.text
        assert _estado_en_base(sesion, evento_id) == EventStatus.RETIRED.value

        editado = cliente.patch(
            EVENTOS + f"/{evento_id}", json={"name": "Tampoco"}, headers=cabeceras
        )
        assert editado.status_code == 409
        assert _estado_en_base(sesion, evento_id) == EventStatus.RETIRED.value


class TestLecturasDeGestion:
    """The reads that feed the provider's page of 7.3.

    What is verified is the split that makes the catalog safe to add later:
    the management list shows drafts to their owner and to nobody else, the
    detail carries the zones the page edits, and a stranger gets the same 403
    the mutations answer — not a 404 that would confirm the event exists.
    """

    def test_el_proveedor_lista_los_suyos_con_borradores(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a provider with one draft and one published event / WHEN it
        lists its management view / THEN both come back, drafts included."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        _evento(sesion, duenio, nombre="Borrador de feria")
        _evento(
            sesion,
            duenio,
            nombre="Concierto publicado",
            estado=EventStatus.PUBLISHED.value,
        )

        respuesta = cliente.get(EVENTOS + "/mine", headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        nombres = {fila["name"] for fila in respuesta.json()}
        assert nombres == {"Borrador de feria", "Concierto publicado"}

    def test_no_se_listan_eventos_ajenos(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN an event owned by another provider / WHEN the caller lists /
        THEN the foreign event is absent, not hidden behind a field."""
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        _evento(sesion, ajeno, nombre="Ajeno")

        respuesta = cliente.get(EVENTOS + "/mine", headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json() == []

    def test_el_administrador_lista_todo(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _jefe(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        _evento(sesion, ajeno, nombre="De otro")

        respuesta = cliente.get(EVENTOS + "/mine", headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert [fila["name"] for fila in respuesta.json()] == ["De otro"]

    def test_un_cliente_no_lista(self, cliente: TestClient, sesion: Session) -> None:
        cabeceras = _persona(cliente, sesion)

        respuesta = cliente.get(EVENTOS + "/mine", headers=cabeceras)

        assert respuesta.status_code == 403
        assert _problema(respuesta)["code"] == "forbidden"

    def test_sin_autenticar_no_lista(self, cliente: TestClient) -> None:
        assert cliente.get(EVENTOS + "/mine").status_code == 401

    def test_el_detalle_trae_las_zonas_ordenadas(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN an event with two zones / WHEN the owner opens the detail /
        THEN both zones come with their price and capacity, sorted by name."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)
        zona_b = Zone(event_id=evento.id, name="Balcón", capacity=10, price=Decimal("40.00"))
        zona_a = Zone(event_id=evento.id, name="Anfiteatro", capacity=50, price=Decimal("15.00"))
        sesion.add_all([zona_b, zona_a])
        sesion.commit()

        respuesta = cliente.get(EVENTOS + f"/{evento.id}", headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["id"] == str(evento.id)
        assert [zona["name"] for zona in cuerpo["zones"]] == ["Anfiteatro", "Balcón"]
        assert cuerpo["zones"][0]["price"] == "15.00"
        assert cuerpo["zones"][1]["capacity"] == 10

    def test_otro_proveedor_no_ve_el_detalle(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN a foreign event / WHEN a stranger opens the detail / THEN the
        ownership 403, and no fields of the event reach the response."""
        cabeceras = _proveedor(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)

        respuesta = cliente.get(EVENTOS + f"/{evento.id}", headers=cabeceras)

        assert respuesta.status_code == 403
        assert _problema(respuesta)["code"] == "forbidden_not_owner"
        assert "Ajeno" not in respuesta.text

    def test_el_administrador_ve_un_detalle_ajeno(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _jefe(cliente, sesion)
        ajeno = _cuenta(sesion, "ajeno@ejemplo.com", role=Role.EVENT_OWNER.value)
        evento = _evento(sesion, ajeno)

        respuesta = cliente.get(EVENTOS + f"/{evento.id}", headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["owner_id"] == str(ajeno.id)

    def test_un_evento_inexistente_es_404_en_el_detalle(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cabeceras = _proveedor(cliente, sesion)

        respuesta = cliente.get(EVENTOS + f"/{uuid.uuid4()}", headers=cabeceras)

        assert respuesta.status_code == 404
        assert _problema(respuesta)["code"] == "not_found"

    def test_mine_no_come_el_parametro_del_id(self, cliente: TestClient, sesion: Session) -> None:
        """`GET /{evento_id}` keeps working for a real uuid after `/mine` was
        registered on the same prefix: the literal route and the parameter
        route must not shadow each other."""
        cabeceras = _proveedor(cliente, sesion)
        duenio = sesion.scalar(select(User).where(User.email == "proveedor@ejemplo.com"))
        assert duenio is not None
        evento = _evento(sesion, duenio)

        detalle = cliente.get(EVENTOS + f"/{evento.id}", headers=cabeceras)

        assert detalle.status_code == 200, detalle.text
        assert detalle.json()["id"] == str(evento.id)
