"""7.3 - The provider's management area, exercised end to end.

The area is a projection of the event-management API: the list *is*
`GET /api/v1/events/mine`, every button posts to the API's own routes, and the
rules that decide an outcome (state machine, ownership, the zone guards) live
in the backend. What is verified here is the whole path a browser takes — the
draft born from the form, the zone added, the publish, the retire — and that
the page afterwards shows the state the API returns, which is the task's
acceptance clause.

Ownership is checked from the browser too: another provider's event opened by
its id gets the denied page, not the detail with hidden buttons. That the API
answers the same 403 is verified in the backend suite; here the point is that
the web never paints what the API refused.

The provider accounts are inserted straight into the shared test database, the
same convention as the admin suite: the web can only register *pending*
providers, and these tests need one already active on the other side of the
form.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from ticket_api.models import Role, User, UserStatus
from ticket_api.security.passwords import hash_password
from ticket_api.tests.conftest import url_de_pruebas

CONTRASENA = "contrasena-de-prueba-1"

#: HTTPS origin: the session cookie is `Secure` and over plain HTTP the test
#: client would not resend it.
ORIGEN_HTTPS = "https://localhost"

LOGIN = "/auth/login"
GESTION = "/gestion/eventos"

#: A window far enough in the future that no run can age past it.
INICIO = "2027-06-01T18:00"
FIN = "2027-06-01T23:00"

UBICACION_EVENTO = re.compile(r"/gestion/eventos/([0-9a-fA-F-]{36})$")
UBICACION_ZONA = re.compile(r"/gestion/eventos/[0-9a-fA-F-]{36}/zonas/([0-9a-fA-F-]{36})")


@pytest.fixture
def navegador(web_app: Flask) -> FlaskClient:
    return web_app.test_client()


def _proveedor_activo(motor_api: Engine, prefijo: str) -> str:
    """An already-approved provider, written straight into the test database.

    `motor_api` is declared by the caller's fixture only so the schema is
    guaranteed to exist; the row goes through this function's own engine,
    because the web conftest deliberately withholds its motor from tests. The
    email is unique per test, the convention every account in these suites
    follows.
    """
    correo = f"{prefijo}-{uuid.uuid4()}@ejemplo.com"
    motor = create_engine(url_de_pruebas(), pool_pre_ping=True)
    try:
        with Session(motor) as sesion:
            sesion.add(
                User(
                    email=correo,
                    password_hash=hash_password(CONTRASENA),
                    role=Role.EVENT_OWNER.value,
                    status=UserStatus.ACTIVE.value,
                )
            )
            sesion.commit()
        return correo
    finally:
        motor.dispose()


def _correo_admin(motor_api: Engine) -> str:
    correo = f"admin-{uuid.uuid4()}@ejemplo.com"
    motor = create_engine(url_de_pruebas(), pool_pre_ping=True)
    try:
        with Session(motor) as sesion:
            sesion.add(
                User(
                    email=correo,
                    password_hash=hash_password(CONTRASENA),
                    role=Role.ADMIN.value,
                    status=UserStatus.ACTIVE.value,
                )
            )
            sesion.commit()
        return correo
    finally:
        motor.dispose()


@pytest.fixture
def correo_proveedor(motor_api: Engine) -> Iterator[str]:
    yield _proveedor_activo(motor_api, "proveedor")


@pytest.fixture
def proveedor_ajeno(motor_api: Engine) -> Iterator[str]:
    yield _proveedor_activo(motor_api, "ajeno")


def _token_csrf(cliente: FlaskClient, pagina: str) -> str:
    """Token CSRF that the web emitted on a real page, scraped from its HTML.

    Read from the page and not from the session store on purpose: the test
    follows the same path a browser does, and a token the HTML never contained
    would test the store, not the form.
    """
    html = cliente.get(pagina, base_url=ORIGEN_HTTPS).get_data(as_text=True)
    encontrado = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert encontrado, "la pagina no publico ningun token CSRF"
    return encontrado.group(1)


def _enviar(cliente: FlaskClient, pagina: str, token: str, **campos: str) -> Any:
    return cliente.post(
        pagina,
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": token, **campos},
        follow_redirects=False,
    )


def _entrar(cliente: FlaskClient, correo: str) -> Any:
    return _enviar(
        cliente,
        LOGIN,
        _token_csrf(cliente, LOGIN),
        email=correo,
        password=CONTRASENA,
    )


def _salir(cliente: FlaskClient) -> Any:
    """Closes the current session through the area's own nav form."""
    return cliente.post(
        "/auth/salir",
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": _token_csrf(cliente, GESTION)},
        follow_redirects=False,
    )


def _pagina_gestion(cliente: FlaskClient) -> str:
    respuesta = cliente.get(GESTION, base_url=ORIGEN_HTTPS)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.get_data(as_text=True)


def _pagina_detalle(cliente: FlaskClient, evento_id: str) -> tuple[int, str]:
    respuesta = cliente.get(f"{GESTION}/{evento_id}", base_url=ORIGEN_HTTPS)
    return respuesta.status_code, respuesta.get_data(as_text=True)


def _id_desde(respuesta: Any) -> str:
    """The new event's id, read from the `Location` the POST answered with.

    The header rather than the HTML on purpose: a redirect the browser really
    follows is the contract of the POST, and the page could change its markup
    without changing where the event lives.
    """
    ubicacion = respuesta.headers["Location"]
    encontrado = UBICACION_EVENTO.search(ubicacion)
    assert encontrado, f"el POST no redirigio al detalle: {ubicacion}"
    return encontrado.group(1)


def _crear_evento(cliente: FlaskClient, nombre: str = "Feria de prueba") -> str:
    """Creates a draft through the form, the way a provider does."""
    pagina = GESTION
    respuesta = _enviar(
        cliente,
        pagina,
        _token_csrf(cliente, pagina),
        name=nombre,
        description="",
        starts_at=INICIO,
        ends_at=FIN,
    )
    assert respuesta.status_code == 303, respuesta.text
    return _id_desde(respuesta)


def _agregar_zona(
    cliente: FlaskClient, evento_id: str, nombre: str = "General"
) -> Any:
    pagina = f"{GESTION}/{evento_id}/zonas"
    return _enviar(
        cliente,
        pagina,
        _token_csrf(cliente, f"{GESTION}/{evento_id}"),
        name=nombre,
        capacity="100",
        price="25.00",
    )


def _id_de_zona(html: str) -> str:
    """The first zone id the detail page drew, from its own edit form."""
    encontrado = UBICACION_ZONA.search(html)
    assert encontrado, "el detalle no dibujo ningun formulario de zona"
    return encontrado.group(1)


class TestCreacion:
    def test_a_new_draft_opens_its_own_page(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a logged-in provider / WHEN it submits the create form /
        THEN a draft is born, the POST redirects to its detail, and the page
        shows the name and the draft state."""
        assert _entrar(navegador, correo_proveedor).status_code == 303

        evento_id = _crear_evento(navegador, "Feria de primavera")

        codigo, html = _pagina_detalle(navegador, evento_id)
        assert codigo == 200
        assert "Feria de primavera" in html
        assert "Borrador" in html
        assert "Publicar" in html

    def test_an_invalid_window_is_repainted_field_by_field(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a create form whose end precedes its start / WHEN it is
        submitted / THEN the list page comes back with the error on `ends_at`
        and no event was born."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        pagina = GESTION

        respuesta = _enviar(
            navegador,
            pagina,
            _token_csrf(navegador, pagina),
            name="Ventana al reves",
            description="",
            starts_at=FIN,
            ends_at=INICIO,
        )

        assert respuesta.status_code == 200, respuesta.text
        html = respuesta.get_data(as_text=True)
        assert "Ventana al reves" in html, "el formulario no repueblo el nombre"
        assert 'id="error-ends_at"' in html
        assert "Ventana al reves" not in _pagina_gestion(navegador)


class TestPublicacion:
    def test_publishing_without_zones_is_repainted_as_an_aviso(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a draft with no zones / WHEN the provider publishes / THEN the
        API's refusal is painted on the detail and the event stays a draft."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)

        respuesta = _enviar(
            navegador,
            f"{GESTION}/{evento_id}/publicar",
            _token_csrf(navegador, f"{GESTION}/{evento_id}"),
        )

        assert respuesta.status_code == 200, respuesta.text
        html = respuesta.get_data(as_text=True)
        assert "al menos una zona" in html
        assert "Borrador" in html

    def test_a_zone_then_publish_reaches_the_published_state(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a draft whose zone was added / WHEN the provider publishes /
        THEN the page shows `published` and offers the retire action instead
        of the edit form, which the API only accepts on drafts."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)
        assert _agregar_zona(navegador, evento_id).status_code == 303

        respuesta = _enviar(
            navegador,
            f"{GESTION}/{evento_id}/publicar",
            _token_csrf(navegador, f"{GESTION}/{evento_id}"),
        )

        assert respuesta.status_code == 303, respuesta.text
        _, html = _pagina_detalle(navegador, evento_id)
        assert "Publicado" in html
        assert "Retirar del catalogo" in html
        assert "Editar evento" not in html


class TestRetiro:
    def test_retiring_leaves_the_event_read_only(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a published event / WHEN the provider retires it / THEN the
        page shows `retired` and neither publish nor retire is offered again —
        retirement is terminal in the API."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)
        assert _agregar_zona(navegador, evento_id).status_code == 303
        assert (
            _enviar(
                navegador,
                f"{GESTION}/{evento_id}/publicar",
                _token_csrf(navegador, f"{GESTION}/{evento_id}"),
            ).status_code
            == 303
        )

        respuesta = _enviar(
            navegador,
            f"{GESTION}/{evento_id}/retirar",
            _token_csrf(navegador, f"{GESTION}/{evento_id}"),
        )

        assert respuesta.status_code == 303, respuesta.text
        _, html = _pagina_detalle(navegador, evento_id)
        assert "Retirado" in html
        assert "Retirar del catalogo" not in html
        assert "Publicar" not in html


class TestZonas:
    def test_a_zone_can_be_added_then_edited_then_deleted(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a draft / WHEN a zone is added, edited and deleted through the
        forms / THEN each page reflects what the API stored."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)
        assert _agregar_zona(navegador, evento_id).status_code == 303

        _, html = _pagina_detalle(navegador, evento_id)
        assert "General" in html
        assert "100" in html
        zona_id = _id_de_zona(html)

        pagina = f"{GESTION}/{evento_id}"
        editada = _enviar(
            navegador,
            pagina + f"/zonas/{zona_id}",
            _token_csrf(navegador, pagina),
            name="Balcón",
            capacity="40",
            price="30.00",
        )
        assert editada.status_code == 303, editada.text
        _, html = _pagina_detalle(navegador, evento_id)
        assert "Balcón" in html
        assert "General" not in html

        borrada = _enviar(
            navegador,
            pagina + f"/zonas/{zona_id}/eliminar",
            _token_csrf(navegador, pagina),
        )
        assert borrada.status_code == 303, borrada.text
        _, html = _pagina_detalle(navegador, evento_id)
        assert "Balcón" not in html
        assert "todavia no tiene zonas" in html

    def test_a_duplicate_zone_name_is_repainted(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN an event whose zone is already called General / WHEN a second
        zone with that name is added / THEN the API's field error is painted
        next to the zone form's name input."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)
        assert _agregar_zona(navegador, evento_id).status_code == 303

        respuesta = _agregar_zona(navegador, evento_id, nombre="General")

        assert respuesta.status_code == 200, respuesta.text
        html = respuesta.get_data(as_text=True)
        assert 'id="error-zona-nueva-name"' in html

    def test_deleting_a_zone_that_is_already_gone_is_repainted(
        self, navegador: FlaskClient, correo_proveedor: str
    ) -> None:
        """GIVEN a zone / WHEN it is deleted twice / THEN the second POST —
        which the browser could send from a stale tab — paints the API's
        refusal instead of pretending it worked."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador)
        assert _agregar_zona(navegador, evento_id).status_code == 303
        pagina = f"{GESTION}/{evento_id}"
        _, html = _pagina_detalle(navegador, evento_id)
        zona_id = _id_de_zona(html)

        assert (
            _enviar(
                navegador,
                pagina + f"/zonas/{zona_id}/eliminar",
                _token_csrf(navegador, pagina),
            ).status_code
            == 303
        )

        respuesta = _enviar(
            navegador,
            pagina + f"/zonas/{zona_id}/eliminar",
            _token_csrf(navegador, pagina),
        )

        assert respuesta.status_code == 200, respuesta.text
        assert "ninguna zona con ese identificador" in respuesta.get_data(as_text=True)


class TestAutorizacion:
    def test_a_client_cannot_open_the_provider_area(
        self, navegador: FlaskClient
    ) -> None:
        """GIVEN a fresh client account — registration itself opens its session
        — / WHEN it opens the list / THEN the denied page, and not the list
        with its buttons hidden."""
        registro = "/auth/registro"
        correo = f"persona-{uuid.uuid4()}@ejemplo.com"
        respuesta = _enviar(
            navegador,
            registro,
            _token_csrf(navegador, registro),
            email=correo,
            password=CONTRASENA,
            role="client",
        )
        assert respuesta.status_code == 303, respuesta.text

        denied = navegador.get(GESTION, base_url=ORIGEN_HTTPS)

        assert denied.status_code == 403
        assert "Crear evento" not in denied.get_data(as_text=True)

    def test_an_anonymous_visitor_gets_the_denied_page(
        self, navegador: FlaskClient
    ) -> None:
        """No session, no list: `exigir_rol` answers 403 without redirecting
        to the login, which would reveal that the area exists."""
        respuesta = navegador.get(GESTION, base_url=ORIGEN_HTTPS)

        assert respuesta.status_code == 403

    def test_another_provider_cannot_open_the_detail(
        self,
        navegador: FlaskClient,
        correo_proveedor: str,
        proveedor_ajeno: str,
    ) -> None:
        """GIVEN an event of one provider / WHEN another provider opens its id
        / THEN the denied page with the API's reason, and none of the event's
        fields."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador, "Evento propio")
        assert _salir(navegador).status_code == 303
        assert _entrar(navegador, proveedor_ajeno).status_code == 303

        codigo, html = _pagina_detalle(navegador, evento_id)

        assert codigo == 403
        assert "Evento propio" not in html
        assert "no pertenece" in html

    def test_the_admin_cannot_open_the_provider_area(
        self,
        navegador: FlaskClient,
        correo_proveedor: str,
        motor_api: Engine,
    ) -> None:
        """The API lets the admin manage any event, but the web area is the
        provider's own workspace and `exigir_rol` reserves it for the role.
        The admin's bypass of ownership is the API's rule and is verified in
        the backend suite; here the point is that the web does not widen the
        role gate on its own."""
        assert _entrar(navegador, correo_proveedor).status_code == 303
        evento_id = _crear_evento(navegador, "Evento propio")
        assert _salir(navegador).status_code == 303
        assert _entrar(navegador, _correo_admin(motor_api)).status_code == 303

        codigo, html = _pagina_detalle(navegador, evento_id)

        assert codigo == 403
        assert "Evento propio" not in html
