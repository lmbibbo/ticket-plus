"""6.4 - The CSRF guard is a property of the application, not of a route.

Task 6.4 asks for two things: a token on every state-changing operation, and
verification that a request without it modifies nothing. The first half used to
be a convention — each POST view called `comprobar_csrf` on its own — and this
file is what keeps the second half true after the check moved into
`before_request`: a route that nobody remembered to guard is still rejected,
which is the only way a rule that grows with 7.2, 7.3 and 7.5 can be trusted.

The app under test is a full `create_app` with one throwaway route added on
top, not a stripped Flask: the rejection page extends the base layout, and the
layout resolves blueprint endpoints and the identity global, so a minimal app
would fail for reasons that have nothing to do with CSRF.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from ticket_web.config import Settings
from ticket_web.csrf import CAMPO_TOKEN, CLAVE_TOKEN
from ticket_web.main import create_app

#: Origen HTTPS: la cookie de sesion es `Secure` y sobre HTTP el cliente de
#: pruebas no la reenviaria.
ORIGEN_HTTPS = "https://localhost"

#: A route that exists only here, added the way a future task adds one: handler
#: only, no CSRF code of any kind inside it.
RUTA_NUEVA = "/ruta-que-todavia-no-existia"


@pytest.fixture
def navegador() -> Iterator[FlaskClient]:
    """Full app plus the throwaway route, with a fresh session store.

    The client is *not* opened as a context manager, the same way
    `conftest.cliente` does it: entering preserves every request context, and
    the middleware's teardown then runs twice over the same tokens.
    """
    app = create_app(
        Settings(
            app_name="csrf-test",
            api_base_url="http://api.interno",
            log_level="WARNING",
        )
    )

    @app.post(RUTA_NUEVA)
    def _ruta_nueva() -> tuple[str, int]:
        return ("hecho", 204)

    @app.get(RUTA_NUEVA)
    def _ruta_nueva_get() -> tuple[str, int]:
        return ("ok", 200)

    yield app.test_client()


def _sembrar_token(cliente: FlaskClient, token: str) -> None:
    """Write the session token directly, the way a previous page would have."""
    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        sesion[CLAVE_TOKEN] = token


def test_a_new_state_changing_route_still_requires_the_token(
    navegador: FlaskClient,
) -> None:
    """GIVEN a route added without any CSRF code / WHEN it is posted to
    without a token / THEN 400 and the handler never runs.

    This is the whole point of the guard: if the protection depended on each
    view remembering to call the checker, this route would answer 204 and the
    test would pass for the wrong reason.
    """
    respuesta = navegador.post(RUTA_NUEVA, base_url=ORIGEN_HTTPS, follow_redirects=False)

    assert respuesta.status_code == 400
    assert "No se ha cambiado nada" in respuesta.get_data(as_text=True)


def test_the_same_route_works_once_the_session_token_arrives(
    navegador: FlaskClient,
) -> None:
    token = secrets.token_urlsafe(32)
    _sembrar_token(navegador, token)

    respuesta = navegador.post(
        RUTA_NUEVA,
        base_url=ORIGEN_HTTPS,
        data={CAMPO_TOKEN: token},
        follow_redirects=False,
    )

    assert respuesta.status_code == 204


def test_a_token_from_another_session_does_not_open_the_route(
    navegador: FlaskClient,
) -> None:
    """The check compares against *this* session's token; a correct-looking
    token minted elsewhere is exactly what a cross-site form would carry."""
    _sembrar_token(navegador, secrets.token_urlsafe(32))

    respuesta = navegador.post(
        RUTA_NUEVA,
        base_url=ORIGEN_HTTPS,
        data={CAMPO_TOKEN: secrets.token_urlsafe(32)},
        follow_redirects=False,
    )

    assert respuesta.status_code == 400


def test_the_token_is_also_accepted_as_a_header(navegador: FlaskClient) -> None:
    """HTMX fragments (7.6) cannot always carry a form field; the header path
    exists so they reuse this same guard instead of a second mechanism."""
    token = secrets.token_urlsafe(32)
    _sembrar_token(navegador, token)

    respuesta = navegador.post(
        RUTA_NUEVA,
        base_url=ORIGEN_HTTPS,
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )

    assert respuesta.status_code == 204


def test_get_requests_are_not_gated(navegador: FlaskClient) -> None:
    """The guard exists for state changes only: reading a page keeps working
    with no token anywhere in the session."""
    respuesta = navegador.get(RUTA_NUEVA, base_url=ORIGEN_HTTPS)

    assert respuesta.status_code == 200
