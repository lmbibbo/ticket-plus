"""6.2 - Layout base, navegacion por rol, cierre de sesion y vistas protegidas.

Los tokens de acceso que usan estas pruebas estan firmados de mentira: solo
importa el `role` que la web lee, porque la API es quien valida de verdad. El
valor es el que emite `ticket_api.security.tokens`, de modo que una prueba no
puede pasar por un formato que la API nunca emitiria.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

#: Origen HTTPS: la cookie de sesion es `Secure` y sobre HTTP el cliente de
#: pruebas no la reenviaria.
ORIGEN_HTTPS = "https://localhost"

ROLE_CLIENT = "client"
ROLE_PROVEEDOR = "event-owner"
ROLE_ADMIN = "admin"


def access_token(rol: str, *, usuario: uuid.UUID | None = None, expira_en: int = 900) -> str:
    """Token de acceso con la misma forma que emite la API."""
    ahora = datetime.now(UTC)
    payload = {
        "sub": str(usuario or uuid.uuid4()),
        "role": rol,
        "sid": str(uuid.uuid4()),
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(seconds=expira_en)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    segmento = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"cabecera.{segmento}.firma"


def _entrar(cliente: FlaskClient, rol: str, **kwargs: Any) -> None:
    """Abre sesion en el cliente de pruebas escribiendo en su sesion web.

    Se escribe en el diccionario que entrega `session_transaction` y no con
    `guardar_credenciales`, porque ese context manager solo mantiene vivo el
    contexto de peticion mientras abre y guarda la sesion, no mientras cede el
    control. El comportamiento de `guardar_credenciales` se comprueba aparte.
    """
    _guardar_token(cliente, access_token(rol, **kwargs), f"refresh-{rol}")


def _guardar_token(cliente: FlaskClient, token: str, refresh: str) -> None:
    """Escribe un par de credenciales directamente en la sesion del cliente."""
    from ticket_web.auth import CLAVE_ACCESO, CLAVE_REFRESH

    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        sesion.clear()
        sesion[CLAVE_ACCESO] = token
        sesion[CLAVE_REFRESH] = refresh


def _navegacion(html: str) -> str:
    """Contenido de la cabecera, que es donde vive la navegacion por rol."""
    inicio = html.find("<header>")
    fin = html.find("</header>")
    return html[inicio:fin]


def _rol_de(token: str) -> str:
    """Claim `role` de un token de acceso."""
    from ticket_web.auth import claims_del_token

    claims = claims_del_token(token)
    assert claims is not None, "el token deberia ser legible"
    return str(claims["role"])


def _token_del_navegador(cliente: FlaskClient) -> str:
    """Token CSRF que la web emitio en la ultima pagina servida al navegador.

    Se lee de una pagina real y no del almacen: asi la prueba recorre el mismo
    camino que el visitante y no puede pasar por un token que el HTML nunca
    llegaria a contener.
    """
    import re

    html = cliente.get("/auth/login", base_url=ORIGEN_HTTPS).get_data(as_text=True)
    encontrado = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert encontrado, "la pagina no publico ningun token CSRF"
    return encontrado.group(1)


def _salir(cliente: FlaskClient, token: str | None = None) -> Any:
    """Envia el cierre de sesion con su token, como haria el formulario."""
    if token is None:
        token = _token_del_navegador(cliente)
    return cliente.post(
        "/auth/salir",
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": token},
        follow_redirects=False,
    )


@pytest.fixture
def cliente(web_app: Flask) -> FlaskClient:
    return web_app.test_client()


# --------------------------------------------------------------- visitante


def test_el_visitante_ve_solo_las_pantallas_de_acceso(cliente: FlaskClient) -> None:
    """GIVEN un visitante sin sesion / WHEN abre la portada / THEN ve como maximo
    iniciar sesion y registrarse, y ningun area de gestion."""
    respuesta = cliente.get("/", base_url=ORIGEN_HTTPS)
    navegacion = _navegacion(respuesta.get_data(as_text=True))

    assert respuesta.status_code == 200
    assert "/auth/login" in navegacion
    assert "/auth/registro" in navegacion
    for area in ("Mi cuenta", "Mis eventos", "Usuarios"):
        assert area not in navegacion, f"al visitante se le ofrecio {area!r}"


def test_el_visitante_no_abre_ninguna_vista_de_rol(cliente: FlaskClient) -> None:
    """GIVEN un visitante / WHEN pide una vista reservada a un rol / THEN recibe
    403. No un 302 al login: la respuesta no debe revelar que la vista existe."""
    for ruta in ("/mi-cuenta/", "/gestion/eventos", "/admin/usuarios"):
        respuesta = cliente.get(ruta, base_url=ORIGEN_HTTPS)
        assert respuesta.status_code == 403, ruta


# -------------------------------------------------- navegacion por rol


@pytest.mark.parametrize(
    ("rol", "propio", "ajenos"),
    [
        (ROLE_CLIENT, "/mi-cuenta/", ("/gestion/eventos", "/admin/usuarios")),
        (ROLE_PROVEEDOR, "/gestion/eventos", ("/mi-cuenta/", "/admin/usuarios")),
        (ROLE_ADMIN, "/admin/usuarios", ("/mi-cuenta/", "/gestion/eventos")),
    ],
)
def test_cada_rol_ve_su_enlace_y_no_los_de_los_demas(
    cliente: FlaskClient, rol: str, propio: str, ajenos: tuple[str, ...]
) -> None:
    """GIVEN una sesion de un rol / WHEN se dibuja la navegacion / THEN aparece
    su area y ninguna de las ajenas."""
    _entrar(cliente, rol)
    html = cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)
    navegacion = _navegacion(html)

    assert propio in navegacion, f"a {rol} le falta su area"
    for ajeno in ajenos:
        assert ajeno not in navegacion, f"a {rol} se le mostraba {ajeno}"


@pytest.mark.parametrize(
    ("rol", "propio", "ajenos"),
    [
        (ROLE_CLIENT, "/mi-cuenta/", ("/gestion/eventos", "/admin/usuarios")),
        (ROLE_PROVEEDOR, "/gestion/eventos", ("/mi-cuenta/", "/admin/usuarios")),
        (ROLE_ADMIN, "/admin/usuarios", ("/mi-cuenta/", "/gestion/eventos")),
    ],
)
def test_una_vista_ajena_devuelve_403(
    cliente: FlaskClient, rol: str, propio: str, ajenos: tuple[str, ...]
) -> None:
    """GIVEN una sesion valida de un rol / WHEN pide la vista de otro / THEN 403
    y sin datos de esa vista."""
    _entrar(cliente, rol)

    for ajeno in ajenos:
        respuesta = cliente.get(ajeno, base_url=ORIGEN_HTTPS)
        assert respuesta.status_code == 403, f"{rol} abrio {ajeno}"
        assert "No tienes acceso" in respuesta.get_data(as_text=True)


def _api_vacia() -> Any:
    """Doble del cliente de API que devuelve listas vacias.

    La vista de administracion de 7.2 pide el listado al API, y estas pruebas
    llevan tokens firmados de mentira que el API real rechazaria con 401. El
    sujeto aqui es la proteccion por rol, no el contrato con el API, que vive
    en test_web_admin.py.
    """
    import httpx

    from ticket_web.api_client import ApiResponse

    class _ApiVacia:
        def get(self, path: str, **_: Any) -> Any:
            return ApiResponse(status=200, data=[], headers=httpx.Headers())

        def post(self, path: str, **_: Any) -> Any:  # pragma: no cover
            raise AssertionError(f"no se esperaba una escritura en {path}")

        def delete(self, path: str, **_: Any) -> Any:  # pragma: no cover
            raise AssertionError(f"no se esperaba un borrado en {path}")

        def close(self) -> None:
            return None

    return _ApiVacia()


@pytest.mark.parametrize(
    ("rol", "propio"),
    [
        (ROLE_CLIENT, "/mi-cuenta/"),
        (ROLE_PROVEEDOR, "/gestion/eventos"),
        (ROLE_ADMIN, "/admin/usuarios"),
    ],
)
def test_cada_rol_abre_su_propia_vista(
    web_app: Flask, cliente: FlaskClient, rol: str, propio: str
) -> None:
    """El contrapeso del anterior: la proteccion no puede cerrar tambien lo que
    si corresponde."""
    web_app.extensions["api_client"] = _api_vacia()
    _entrar(cliente, rol)
    assert cliente.get(propio, base_url=ORIGEN_HTTPS).status_code == 200


def test_la_respuesta_de_403_no_revela_la_vista_solicitada(cliente: FlaskClient) -> None:
    """GIVEN una vista de administracion / WHEN un cliente la pide / THEN el
    cuerpo no la describe: ni su titulo, ni sus controles, ni su URL de API."""
    _entrar(cliente, ROLE_CLIENT)
    html = cliente.get("/admin/usuarios", base_url=ORIGEN_HTTPS).get_data(as_text=True)

    for revelacion in ("Gestion de usuarios", "/api/v1/admin", "aprobar", "suspender"):
        assert revelacion not in html, f"el 403 filtro {revelacion!r}"


# ------------------------------------------------------------ sesion y logout


def test_el_logout_limpia_la_sesion_y_vuelve_a_la_portada(
    cliente: FlaskClient,
) -> None:
    """GIVEN una sesion abierta / WHEN se cierra / THEN el servidor ya no la
    conserva y el navegador vuelve a la vista publica."""
    _entrar(cliente, ROLE_CLIENT)
    token = cliente.get("/auth/sesion", base_url=ORIGEN_HTTPS)
    assert token.status_code == 200

    respuesta = _salir(cliente)

    assert respuesta.status_code == 303, respuesta.headers.get("Location")
    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 403


def test_el_logout_revoca_el_refresh_en_la_api(web_app: Flask) -> None:
    """La copia local no basta: el refresh token se revoca en la API. Si solo se
    borrara del navegador, seguiria sirviendo para abrir sesion hasta que
    caducara."""
    vistos: list[dict[str, Any]] = []

    class _ApiQueRevoca:
        def get(self, path: str, **_: Any) -> Any:  # pragma: no cover - no se usa
            raise AssertionError(f"no se esperaba una lectura en {path}")

        def post(self, path: str, **kwargs: Any) -> Any:
            import httpx

            from ticket_web.api_client import ApiResponse

            vistos.append({"ruta": path, "cuerpo": kwargs.get("json")})
            return ApiResponse(status=204, data=None, headers=httpx.Headers())

        def delete(self, path: str, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError(f"no se esperaba un borrado en {path}")

        def close(self) -> None:
            return None

    web_app.extensions["api_client"] = _ApiQueRevoca()
    cliente = web_app.test_client()
    _entrar(cliente, ROLE_CLIENT)

    _salir(cliente)

    assert vistos == [
        {"ruta": "/api/v1/auth/logout", "cuerpo": {"refresh_token": "refresh-client"}}
    ]


def test_el_logout_sin_api_no_deja_la_sesion_viva(web_app: Flask) -> None:
    """GIVEN que la API no responde / WHEN se cierra sesion / THEN la sesion
    local se borra igual. Dejarla viva por un fallo de red seria un fallo de
    seguridad disfrazado de disponibilidad."""
    from ticket_web.api_client import ApiFailure, ApiUnavailableError

    class _ApiCaida:
        def get(self, path: str, **_: Any) -> Any:  # pragma: no cover - no se usa
            raise AssertionError(f"no se esperaba una lectura en {path}")

        def post(self, path: str, **kwargs: Any) -> Any:
            raise ApiUnavailableError(ApiFailure.UNREACHABLE)

        def delete(self, path: str, **kwargs: Any) -> Any:  # pragma: no cover
            raise AssertionError(f"no se esperaba un borrado en {path}")

        def close(self) -> None:
            return None

    web_app.extensions["api_client"] = _ApiCaida()
    cliente = web_app.test_client()
    _entrar(cliente, ROLE_ADMIN)

    respuesta = _salir(cliente)

    assert respuesta.status_code == 303
    assert cliente.get("/admin/usuarios", base_url=ORIGEN_HTTPS).status_code == 403


def test_el_logout_exige_sesion_abierta(cliente: FlaskClient) -> None:
    """Sin sesion no hay nada que cerrar, y el 403 no filtra si habia algo.

    Sin token la guarda CSRF responde antes que la regla de rol: una peticion
    de cambio de estado sin token esta mal formada sea de quien sea. Con un
    token de visitante bien formado -el que emite el formulario de login- el
    formulario ya es valido y quien responde es el rol, con su 403.
    """
    respuesta = cliente.post("/auth/salir", base_url=ORIGEN_HTTPS)
    assert respuesta.status_code == 400

    token = _token_del_navegador(cliente)
    respuesta = cliente.post(
        "/auth/salir",
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert respuesta.status_code == 403


def test_el_logout_sin_token_csrf_no_cierra_la_sesion(cliente: FlaskClient) -> None:
    """Un cierre de sesion sin token es el logout de otra pagina: si se
    aceptara, bastaria una imagen en un foro para cerrar la sesion de quien la
    viera. La sesion sobrevive y nada se revoca."""
    _entrar(cliente, ROLE_CLIENT)

    respuesta = _salir(cliente, token="")

    assert respuesta.status_code == 400
    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 200


def test_el_logout_con_token_ajeno_no_cierra_la_sesion(cliente: FlaskClient) -> None:
    """El token de otra sesion tampoco vale: comparar contra el de esta sesion es
    lo que ata el formulario al navegador que lo pidio."""
    _entrar(cliente, ROLE_CLIENT)

    respuesta = _salir(cliente, token=secrets.token_urlsafe(32))

    assert respuesta.status_code == 400
    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 200


# ------------------------------------------------------- caducidad y basura


def test_un_token_caducado_deja_la_web_como_visitante(cliente: FlaskClient) -> None:
    """El token de acceso es corto a proposito. Cuando caduca, la navegacion
    vuelve a la de visitante en lugar de romper la plantilla."""
    _entrar(cliente, ROLE_ADMIN, expira_en=-60)
    html = cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)

    assert cliente.get("/admin/usuarios", base_url=ORIGEN_HTTPS).status_code == 403
    assert "/auth/login" in html


@pytest.mark.parametrize(
    "basura",
    ["", "no-es-un-jwt", "a.b", "a..c", "eyJhbGciOiJ9..firma", "a.b.c.d"],
)
def test_un_token_ilegible_no_abre_sesion(cliente: FlaskClient, basura: str) -> None:
    """Un token manipulado no debe romper la pagina ni abrir una sesion: se
    trata como si no hubiera sesion."""
    _guardar_token(cliente, basura, "refresh")

    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 403


def test_un_token_sin_expiracion_no_se_trata_como_sesion(cliente: FlaskClient) -> None:
    """Un token sin `exp` no caduca nunca. Aceptarlo seria el fallo exacto que
    la vida corta del token evita."""
    payload = {"sub": str(uuid.uuid4()), "role": ROLE_ADMIN}
    segmento = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    _guardar_token(cliente, f"cabecera.{segmento}.firma", "refresh")

    assert cliente.get("/admin/usuarios", base_url=ORIGEN_HTTPS).status_code == 403


def test_entrar_con_otra_cuenta_no_mezcla_sesiones(web_app: Flask) -> None:
    """Si el mismo navegador tenia una sesion y entra con otra cuenta, la
    identidad nueva sustituye a la anterior por completo.

    Se comprueba `guardar_credenciales` y no la navegacion porque es ahi donde se
    decide: si las credenciales nuevas se anaden a las viejas, el visitante
    acabaria con la identidad de una cuenta y el refresh de otra.
    """
    from ticket_web.auth import CLAVE_ACCESO, CLAVE_REFRESH, guardar_credenciales

    with web_app.test_request_context(base_url=ORIGEN_HTTPS):
        guardar_credenciales(access_token(ROLE_ADMIN), "refresh-admin")
        guardar_credenciales(access_token(ROLE_CLIENT), "refresh-cliente")

        from flask import session

        assert session[CLAVE_REFRESH] == "refresh-cliente"
        assert _rol_de(session[CLAVE_ACCESO]) == ROLE_CLIENT


def test_la_navegacion_cambia_al_cambiar_de_cuenta(cliente: FlaskClient) -> None:
    """Y el efecto se ve: tras entrar como cliente, el area de administracion ya
    no se ofrece."""
    _entrar(cliente, ROLE_ADMIN)
    assert "/admin/usuarios" in _navegacion(
        cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)
    )

    _entrar(cliente, ROLE_CLIENT)
    navegacion = _navegacion(cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True))

    assert "/mi-cuenta/" in navegacion
    assert "/admin/usuarios" not in navegacion


def test_la_navegacion_parte_de_un_enlace_de_salto(web_app: Flask) -> None:
    """El enlace de salto al contenido es requisito de accesibilidad de 7.7, pero
    se comprueba aqui porque es parte del layout que introduce esta tarea."""
    html = cliente_de_prueba(web_app).get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)

    assert 'class="skip-link" href="#contenido"' in html
    assert '<main id="contenido">' in html


def cliente_de_prueba(web_app: Flask) -> FlaskClient:
    """Cliente limpio, sin sesion."""
    return web_app.test_client()