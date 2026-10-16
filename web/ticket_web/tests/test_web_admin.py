"""7.2 - The admin's user-management page, exercised end to end.

The view is a projection of the admin API: the list it draws *is* the API's
pending list, and its two buttons are the API's approve and suspend routes.
What is verified here is the whole path a browser takes — register a provider,
log in as an admin, approve — and that the page afterwards reflects the state
the API returns, which is the task's acceptance clause: the account leaves the
list, and the same account can (or cannot) then log in.

The admin account is inserted straight into the shared test database: the web
can only register clients and providers through its own forms, and the flow
under test needs an admin on the other side of the form.
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

#: Origen HTTPS: la cookie de sesion es `Secure` y sobre HTTP el cliente de
#: pruebas no la reenviaria.
ORIGEN_HTTPS = "https://localhost"

LOGIN = "/auth/login"
REGISTRO = "/auth/registro"
SALIR = "/auth/salir"
USUARIOS = "/admin/usuarios"

#: Patron del formulario de aprobacion que la pagina dibuja por cuenta pendiente.
FORMULARIO_APROBAR = re.compile(r"/admin/usuarios/([0-9a-fA-F-]{36})/aprobar")


@pytest.fixture
def navegador(web_app: Flask) -> FlaskClient:
    return web_app.test_client()


@pytest.fixture
def correo_admin(motor_api: Engine) -> Iterator[str]:
    """A real admin account, in the shared test database.

    `motor_api` is declared only so the schema is guaranteed to exist before
    this engine opens; the row is written through the fixture's own engine
    because the web conftest deliberately withholds its motor from tests — it
    exists to build the DDL, not to hand out connections. The email is unique
    per test, the same convention every account in these suites follows, so
    nothing has to be cleaned out of a database the whole session shares.
    """
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
        yield correo
    finally:
        motor.dispose()


def _correo_nuevo() -> str:
    return f"persona-{uuid.uuid4()}@ejemplo.com"


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


def _registrar_proveedor(cliente: FlaskClient) -> str:
    """Registers a provider the way a visitor does; the account stays pending
    and no session is opened."""
    correo = _correo_nuevo()
    respuesta = _enviar(
        cliente,
        REGISTRO,
        _token_csrf(cliente, REGISTRO),
        email=correo,
        password=CONTRASENA,
        role="event-owner",
    )
    assert respuesta.status_code == 200, respuesta.text
    return correo


def _entrar(cliente: FlaskClient, correo: str) -> Any:
    return _enviar(
        cliente,
        LOGIN,
        _token_csrf(cliente, LOGIN),
        email=correo,
        password=CONTRASENA,
    )


def _hay_sesion(cliente: FlaskClient) -> bool:
    return cliente.get("/auth/sesion", base_url=ORIGEN_HTTPS).status_code == 200


def _salir(cliente: FlaskClient) -> Any:
    """Closes the current session through the nav's own form.

    Needed before a second login attempt: a *failed* login does not clear the
    session it finds, so without this the admin's session would still be the
    one answering the provider's tests.
    """
    return cliente.post(
        SALIR,
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": _token_csrf(cliente, USUARIOS)},
        follow_redirects=False,
    )


def _pagina_usuarios(cliente: FlaskClient) -> str:
    respuesta = cliente.get(USUARIOS, base_url=ORIGEN_HTTPS)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.get_data(as_text=True)


def _id_del_proveedor(html: str, correo: str) -> str:
    """The id of *this* test's pending account, from the list item that names
    it.

    Scraped rather than looked up through the API on purpose: the test follows
    what the browser sees. The database is shared by the whole session, so the
    first form on the page may well belong to another test's provider — the
    item has to be found by its email, not by position.
    """
    for item in html.split("<li>"):
        if correo in item:
            encontrado = FORMULARIO_APROBAR.search(item)
            assert encontrado, "el listado dibujo la cuenta sin su formulario de aprobacion"
            return encontrado.group(1)
    raise AssertionError(f"{correo} no aparece en el listado de pendientes")


def _accion(cliente: FlaskClient, usuario_id: str, accion: str) -> Any:
    """Sends one of the page's own forms, with the page's own token."""
    return cliente.post(
        f"{USUARIOS}/{usuario_id}/{accion}",
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": _token_csrf(cliente, USUARIOS)},
        follow_redirects=False,
    )


class TestListado:
    def test_the_admin_sees_the_pending_providers(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """GIVEN a provider waiting for approval / WHEN the admin opens the
        page / THEN the account is listed with its two actions."""
        _registrar_proveedor(navegador)
        assert _entrar(navegador, correo_admin).status_code == 303

        html = _pagina_usuarios(navegador)

        assert "persona-" in html
        assert "Aprobar" in html
        assert "Suspender" in html

    def test_the_empty_list_says_so(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """An empty pending list is a normal state, not a missing template.

        The test database is shared by the whole session and other tests leave
        pending providers behind, so the list is drained through the page's own
        forms first; that also proves the actions can clear the list to the
        last account.
        """
        assert _entrar(navegador, correo_admin).status_code == 303
        html = _pagina_usuarios(navegador)
        while FORMULARIO_APROBAR.search(html):
            pendiente = FORMULARIO_APROBAR.search(html)
            assert pendiente is not None
            assert _accion(navegador, pendiente.group(1), "aprobar").status_code == 303
            html = _pagina_usuarios(navegador)

        assert "No hay cuentas de proveedor pendientes" in html


class TestAprobacion:
    def test_approving_makes_the_provider_able_to_enter(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """The F3 demo path: approve, and the provider's next login works.

        The list afterwards no longer shows the account — the API's list is
        the pending list, so "reflected state" means it left it — and the
        proof that the change was real is the login, not the redirect.
        """
        correo_proveedor = _registrar_proveedor(navegador)
        assert _entrar(navegador, correo_admin).status_code == 303
        cuenta = _id_del_proveedor(_pagina_usuarios(navegador), correo_proveedor)

        respuesta = _accion(navegador, cuenta, "aprobar")

        assert respuesta.status_code == 303, respuesta.headers.get("Location")
        assert correo_proveedor not in _pagina_usuarios(navegador)

        assert _salir(navegador).status_code == 303
        assert _entrar(navegador, correo_proveedor).status_code == 303
        assert _hay_sesion(navegador)

    def test_a_second_approval_is_repainted_and_changes_nothing(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """A second approve - another tab, a stale page - is the API's 409,
        shown as an error on the refreshed list, and the account keeps the
        state the API returned the first time."""
        correo_proveedor = _registrar_proveedor(navegador)
        assert _entrar(navegador, correo_admin).status_code == 303
        cuenta = _id_del_proveedor(_pagina_usuarios(navegador), correo_proveedor)
        assert _accion(navegador, cuenta, "aprobar").status_code == 303

        respuesta = _accion(navegador, cuenta, "aprobar")

        assert respuesta.status_code == 200
        assert "ya no esta pendiente" in respuesta.get_data(as_text=True)

        assert _salir(navegador).status_code == 303
        assert _entrar(navegador, correo_proveedor).status_code == 303


class TestSuspension:
    def test_suspending_takes_the_provider_off_the_list_and_off_login(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """Suspension is the other half of the task: the account leaves the
        pending list and its credentials stop working."""
        correo_proveedor = _registrar_proveedor(navegador)
        assert _entrar(navegador, correo_admin).status_code == 303
        cuenta = _id_del_proveedor(_pagina_usuarios(navegador), correo_proveedor)

        respuesta = _accion(navegador, cuenta, "suspender")

        assert respuesta.status_code == 303, respuesta.headers.get("Location")
        assert correo_proveedor not in _pagina_usuarios(navegador)

        assert _salir(navegador).status_code == 303
        assert _entrar(navegador, correo_proveedor).status_code == 401
        assert not _hay_sesion(navegador)


class TestAutorizacion:
    def test_a_client_cannot_approve_anybody(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """GIVEN a client session / WHEN it posts the admin's form / THEN 403
        and the provider is still waiting.

        The id is a random one: the role rule fires before any lookup, and a
        client cannot even open the list that would reveal a real id. That the
        denied request changed nothing is proven by the admin's own list
        afterwards.
        """
        correo_proveedor = _registrar_proveedor(navegador)
        correo_cliente = _correo_nuevo()
        assert (
            _enviar(
                navegador,
                REGISTRO,
                _token_csrf(navegador, REGISTRO),
                email=correo_cliente,
                password=CONTRASENA,
                role="client",
            ).status_code
            == 303
        )

        respuesta = navegador.post(
            f"{USUARIOS}/{uuid.uuid4()}/aprobar",
            base_url=ORIGEN_HTTPS,
            data={"csrf_token": _token_csrf(navegador, LOGIN)},
            follow_redirects=False,
        )

        assert respuesta.status_code == 403

        # The client's session is replaced by the admin's, and the list still
        # shows the provider: the 403 did not approve anybody.
        assert _entrar(navegador, correo_admin).status_code == 303
        assert correo_proveedor in _pagina_usuarios(navegador)

    def test_an_admin_form_without_the_csrf_token_changes_nothing(
        self, navegador: FlaskClient, correo_admin: str
    ) -> None:
        """The guard of 6.4 applies to these forms like any other: no token,
        no approval. A pending provider cannot log in either, so the failed
        login afterwards is what proves the account was left untouched."""
        correo_proveedor = _registrar_proveedor(navegador)
        assert _entrar(navegador, correo_admin).status_code == 303
        cuenta = _id_del_proveedor(_pagina_usuarios(navegador), correo_proveedor)

        respuesta = navegador.post(
            f"{USUARIOS}/{cuenta}/aprobar",
            base_url=ORIGEN_HTTPS,
            data={},
            follow_redirects=False,
        )

        assert respuesta.status_code == 400

        assert _entrar(navegador, correo_proveedor).status_code == 401
