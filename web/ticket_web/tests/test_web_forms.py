"""6.3 - Formularios de alta, login, cierre de sesion y renovacion.

Estas pruebas no usan dobles: la web real llama a la API real por el transporte
ASGI y las dos comparten el PostgreSQL de pruebas. Es lo unico que demuestra la
afirmacion que sostiene a 6.3 -que el camino del navegador funciona de punta a
punta- porque un doble podria responder 201 a un registro que la API real
rechazaria, y la prueba pasaria igual.

Cada correo es unico porque las cuentas no se borran entre pruebas: se acumulan
en la base de pruebas y se limpian al terminar la corrida.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import select

#: La fabrica se alcanza por el modulo y no por su nombre: la suite de la web
#: parchea `ticket_api.db.session.get_sessionmaker` para atar la API en proceso
#: a la base de pruebas, y un `from ... import` fijaria la funcion original en
#: el momento de importar, antes de que el parche exista. Leyendola del modulo
#: en cada llamada, la prueba consulta la misma base en la que la API escribio.
from ticket_api.db import session as db_session
from ticket_api.models import AuthSession

#: Origen HTTPS: la cookie de sesion es `Secure` y sobre HTTP el cliente de
#: pruebas no la reenviaria.
ORIGEN_HTTPS = "https://localhost"

ROLE_CLIENT = "client"
ROLE_PROVEEDOR = "event-owner"
ROLE_ADMIN = "admin"

#: Cumple el minimo de longitud que impone el API.
CONTRASENA = "contrasena-de-prueba-1"

#: Por debajo del minimo, para probar que la politica se aplica y se pinta.
CONTRASENA_CORTA = "corta"


@pytest.fixture
def cliente(web_app: Flask) -> FlaskClient:
    return web_app.test_client()


def correo_nuevo() -> str:
    """Correo valido y unico.

    El dominio no puede ser uno de uso reservado como `test` o `invalid`: el
    `EmailStr` del API los rechaza, asi que un correo con ese dominio pasaria la
    prueba por el motivo equivocado y no por el que dice medir.
    """
    return f"persona-{uuid.uuid4().hex}@ejemplo.com"


def token_csrf(cliente: FlaskClient, pagina: str) -> str:
    """Token CSRF tal y como lo recibiria el navegador en la pagina indicada.

    Se lee de una pagina real y no del almacen, para que la prueba recorra el
    mismo camino que el visitante y no pueda pasar por un token que el HTML nunca
    llegaria a contener.
    """
    html = cliente.get(pagina, base_url=ORIGEN_HTTPS).get_data(as_text=True)
    encontrado = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert encontrado is not None, f"{pagina} no publico ningun token CSRF"
    return encontrado.group(1)


def enviar(cliente: FlaskClient, pagina: str, token: str, **campos: str) -> Any:
    """Envia un formulario con su token, como haria el navegador."""
    return cliente.post(
        pagina,
        base_url=ORIGEN_HTTPS,
        data={"csrf_token": token, **campos},
        follow_redirects=False,
    )


def registrarse(cliente: FlaskClient, *, rol: str = ROLE_CLIENT) -> str:
    """Da de alta una cuenta de cliente por el formulario y devuelve su correo."""
    correo = correo_nuevo()
    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo,
        password=CONTRASENA,
        role=rol,
    )
    assert respuesta.status_code == 303, respuesta.get_data(as_text=True)
    return correo


def hay_sesion(cliente: FlaskClient) -> bool:
    """Si el navegador guarda un token de acceso en la sesion del servidor."""
    from ticket_web.auth import CLAVE_ACCESO

    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        return isinstance(sesion.get(CLAVE_ACCESO), str)


def refresh_de(cliente: FlaskClient) -> str | None:
    """Refresh token que el navegador tiene guardado."""
    from ticket_web.auth import CLAVE_REFRESH

    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        valor = sesion.get(CLAVE_REFRESH)
        return valor if isinstance(valor, str) else None


def poner(cliente: FlaskClient, **valores: str) -> None:
    """Escribe claves en la sesion del navegador."""
    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        sesion.update(valores)


def caducar_access_token(cliente: FlaskClient, rol: str = ROLE_CLIENT) -> None:
    """Deja en la sesion un token de acceso ya caducado.

    Simula lo que pasa de verdad: el token caduca por tiempo mientras el
    navegador sigue abierto y el refresh sigue siendo utilizable.
    """
    caducado = datetime.now(UTC) - timedelta(seconds=5)
    payload = {
        "sub": str(uuid.uuid4()),
        "role": rol,
        "sid": str(uuid.uuid4()),
        "exp": int(caducado.timestamp()),
    }
    segmento = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    poner(cliente, access_token=f"cabecera.{segmento}.firma")


def _aviso_de(html: str) -> str:
    """Texto del aviso general del formulario.

    Se compara solo el aviso y no la pagina entera porque el formulario repuebla
    el correo que se escribio, y en estas dos respuestas ese correo es distinto
    por construccion. Comparar el HTML completo mediria el valor del campo, que no
    es lo que la prueba quiere saber.
    """
    encontrado = re.search(r'class="aviso"[^>]*>(.*?)</p>', html, re.DOTALL)
    assert encontrado is not None, "la respuesta no traia aviso general"
    return encontrado.group(1).strip()


# --------------------------------------------------------------- registro


def test_un_cliente_que_se_registra_ya_entra(cliente: FlaskClient) -> None:
    """GIVEN un visitante en el alta / WHEN crea una cuenta de cliente / THEN la
    cuenta se crea, la sesion queda abierta y aterriza en su area privada.

    El alta y el login son dos llamadas distintas en el API, asi que esta prueba
    cubre tambien que la web encadena las dos sin dejar al visitante con una
    cuenta creada y sin sesion.
    """
    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo_nuevo(),
        password=CONTRASENA,
        role=ROLE_CLIENT,
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["Location"].endswith("/mi-cuenta/")
    assert hay_sesion(cliente), "el alta no abrio sesion"
    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 200


def test_un_correo_duplicado_vuelve_al_formulario_con_error_de_campo(
    cliente: FlaskClient,
) -> None:
    """GIVEN un correo ya registrado / WHEN se vuelve a usar / THEN el formulario
    muestra el error junto al campo `email` y no se abre sesion.

    El error va anclado al campo y no en un aviso general porque es lo que la
    especificacion pide y lo que un formulario sabe pintar. Un aviso global
    obligaria al visitante a deducir que parte de lo que escribio esta mal.
    """
    correo = registrarse(cliente)
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo,
        password=CONTRASENA,
        role=ROLE_CLIENT,
    )

    assert respuesta.status_code == 409
    assert 'id="error-email"' in respuesta.get_data(as_text=True)
    assert not hay_sesion(cliente), "un alta fallida no debe abrir sesion"


def test_una_contrasena_corta_no_se_registra(cliente: FlaskClient) -> None:
    """La politica de contrasenas la aplica el API y la web la pinta por campo.

    Que la imponga el backend y no el formulario es lo que evita que bajarla en el
    HTML "para dar mejor experiencia" deje de estar protegida: un `minlength` en
    el navegador se quita con una peticion a mano.
    """
    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo_nuevo(),
        password=CONTRASENA_CORTA,
        role=ROLE_CLIENT,
    )

    assert respuesta.status_code == 422
    assert 'id="error-password"' in respuesta.get_data(as_text=True)
    assert not hay_sesion(cliente)


def test_un_rol_no_registrable_no_crea_cuenta(cliente: FlaskClient) -> None:
    """El rol `admin` no se puede pedir desde un formulario abierto.

    Que la web no lo ofrezca en el selector es cosa del HTML; un atacante que lo
    escriba a mano en el POST tiene que chocar contra la misma regla.
    """
    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo_nuevo(),
        password=CONTRASENA,
        role=ROLE_ADMIN,
    )

    assert respuesta.status_code == 422
    assert not hay_sesion(cliente)


def test_un_proveedor_pendiente_no_entra_y_sabe_por_que(cliente: FlaskClient) -> None:
    """GIVEN un alta de proveedor / WHEN se envia / THEN la cuenta se crea, no se
    abre sesion y se explica que falta la aprobacion.

    Es el comportamiento que un formulario ingenuo no tendria: si tras el alta se
    intentara el login, el API responderia con un `invalid_credentials` generico y
    el visitante leeria que su correo o su contrasena estan mal, cuando lo cierto
    es que la cuenta existe y le falta una aprobacion.
    """
    correo = correo_nuevo()

    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo,
        password=CONTRASENA,
        role=ROLE_PROVEEDOR,
    )

    assert respuesta.status_code == 200
    assert "aprobacion" in respuesta.get_data(as_text=True).lower()
    assert not hay_sesion(cliente), "un proveedor pendiente no debe entrar"

    intento = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo,
        password=CONTRASENA,
    )
    assert intento.status_code == 401, "una cuenta sin aprobar no puede entrar"
    assert cliente.get("/gestion/eventos", base_url=ORIGEN_HTTPS).status_code == 403


def test_el_alta_exige_token_csrf(cliente: FlaskClient) -> None:
    """Sin token no se crea nada.

    El alta es la operacion que mas se dispara sin sesion de por medio, asi que es
    la que mas necesita el token: cualquiera podria crear cuentas desde la pagina
    de otra persona.
    """
    correo = correo_nuevo()

    respuesta = cliente.post(
        "/auth/registro",
        base_url=ORIGEN_HTTPS,
        data={"email": correo, "password": CONTRASENA, "role": ROLE_CLIENT},
        follow_redirects=False,
    )
    assert respuesta.status_code == 400

    # Que la cuenta no exista se comprueba con un login, que solo pasaria si el
    # alta se hubiera llevado a cabo.
    intento = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo,
        password=CONTRASENA,
    )
    assert intento.status_code == 401


def test_la_contrasena_no_vuelve_en_la_pagina_del_formulario(cliente: FlaskClient) -> None:
    """Tras un error, la contrasena no aparece en el HTML.

    Devolverla la escribiria en el DOM, en el historial y en el boton de atras,
    que es donde un formulario que acaba de fallar acaba dejando lo que el
    visitante escribio.

    La contrasena del POST es demasiado corta a proposito: es lo que hace que el
    API la rechace, y el rechazo es lo que da pie a repintar el formulario. Con una
    contrasena valida el alta tendria exito y no habria pagina que repintar.
    """
    secreta = "corta9!"

    respuesta = enviar(
        cliente,
        "/auth/registro",
        token_csrf(cliente, "/auth/registro"),
        email=correo_nuevo(),
        password=secreta,
        role=ROLE_CLIENT,
    )

    assert respuesta.status_code == 422
    assert secreta not in respuesta.get_data(as_text=True)


# --------------------------------------------------------------- login


def test_entrar_con_credenciales_validas(cliente: FlaskClient) -> None:
    """GIVEN una cuenta activa / WHEN se envia el login / THEN abre sesion y
    aterriza en el area de su rol."""
    correo = registrarse(cliente)
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    respuesta = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo,
        password=CONTRASENA,
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["Location"].endswith("/mi-cuenta/")
    assert hay_sesion(cliente)


def test_credenciales_invalidas_dicen_lo_mismo_siempre(cliente: FlaskClient) -> None:
    """GIVEN un correo que no existe / WHEN se manda una contrasena / THEN el
    aviso es el generico y no dice si el correo existe.

    El API ya responde igual en los tres casos. Si la web distinguiera, el login
    volveria a servir para averiguar que correos estan registrados y quien esta
    pendiente de aprobacion.
    """
    respuesta = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo_nuevo(),
        password=CONTRASENA,
    )

    html = respuesta.get_data(as_text=True).lower()
    assert respuesta.status_code == 401
    assert "no son correctos" in html
    for fuga in ("no existe", "registrado", "aprobacion", "pendiente"):
        assert fuga not in html, f"el aviso filtra {fuga!r}"


def test_un_correo_existente_no_se_distingue_de_uno_inexistente(cliente: FlaskClient) -> None:
    """El mismo aviso para un correo real con contrasena mala y para uno que no
    existe.

    Se comparan las dos respuestas porque es la comparacion -y no cada una por
    separado- lo que demuestra que no hay diferencia que medir.
    """
    correo = registrarse(cliente)
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    existente = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo,
        password="contrasena-equivocada",
    )
    inexistente = enviar(
        cliente,
        "/auth/login",
        token_csrf(cliente, "/auth/login"),
        email=correo_nuevo(),
        password="contrasena-equivocada",
    )

    assert existente.status_code == inexistente.status_code == 401
    assert _aviso_de(existente.get_data(as_text=True)) == _aviso_de(
        inexistente.get_data(as_text=True)
    )


def test_el_login_exige_token_csrf(cliente: FlaskClient) -> None:
    """Sin token no se abre sesion."""
    correo = registrarse(cliente)
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    respuesta = cliente.post(
        "/auth/login",
        base_url=ORIGEN_HTTPS,
        data={"email": correo, "password": CONTRASENA},
        follow_redirects=False,
    )

    assert respuesta.status_code == 400
    assert not hay_sesion(cliente)


def test_la_navegacion_cambia_tras_entrar(cliente: FlaskClient) -> None:
    """Lo que se ve antes y despues del login no es el mismo.

    Es la misma comprobacion que hacia 6.2 con tokens de mentira, pero aqui la
    sesion se abrio de verdad recorriendo el formulario.
    """
    antes = cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)
    assert "Iniciar sesion" in antes
    assert "Mi cuenta" not in antes

    registrarse(cliente)

    despues = cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)
    assert "Mi cuenta" in despues
    assert "Registrarse" not in despues


# --------------------------------------------------------------- renovacion


def test_un_token_caducado_se_renueva_solo(cliente: FlaskClient) -> None:
    """GIVEN una sesion cuyo token de acceso caduca / WHEN se pide una vista
    privada / THEN la web renueva sola y la vista responde 200.

    La renovacion va en `before_request` porque el token caduca por tiempo y no
    por pantalla: al visitante que lleva diez minutos leyendo la pagina no hay
    ninguna vista a la que culpar del 401.
    """
    registrarse(cliente)
    caducar_access_token(cliente)

    respuesta = cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS)

    assert respuesta.status_code == 200
    assert hay_sesion(cliente), "la renovacion deberia haber repuesto el token"


def test_renovar_no_rota_el_token_csrf(cliente: FlaskClient) -> None:
    """Tras una renovacion, el token CSRF del formulario sigue siendo el mismo.

    Es el motivo de que la renovacion no vacie la sesion: si lo hiciera, cada
    formulario que el visitante tiene en pantalla pasaria a fallar con un 400
    imposible de corregir en cuanto su token de acceso caducara.
    """
    registrarse(cliente)
    caducar_access_token(cliente)

    token_antes = token_csrf(cliente, "/mi-cuenta/")
    cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS)
    token_despues = token_csrf(cliente, "/mi-cuenta/")

    assert token_antes == token_despues


def test_un_formulario_que_llega_tras_renovar_sigue_valiendo(cliente: FlaskClient) -> None:
    """El caso de uso de lo anterior, de punta a punta.

    El visitante tiene el formulario de cierre de sesion en pantalla, su token de
    acceso caduca, envia el formulario y el CSRF tiene que seguir aceptandolo: si
    no, el logout fallaria justo cuando el token caduca.
    """
    registrarse(cliente)
    caducar_access_token(cliente)
    token = token_csrf(cliente, "/mi-cuenta/")

    respuesta = enviar(cliente, "/auth/salir", token)

    assert respuesta.status_code == 303
    assert not hay_sesion(cliente)


def test_un_refresh_muerto_cierra_la_sesion_local(cliente: FlaskClient) -> None:
    """GIVEN un refresh que el API no reconoce / WHEN llega con el token de acceso
    caducado / THEN la sesion local se borra y el visitante vuelve a ser visitante.

    La API revoca la familia entera cuando detecta reutilizacion o una sesion
    cerrada. Dejar el navegador con una sesion que parece valida haria que viera su
    area privada y que ninguna operacion le aceptara.
    """
    registrarse(cliente)
    caducar_access_token(cliente)
    poner(cliente, refresh_token="refresh-que-no-existe")

    respuesta = cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS)

    assert respuesta.status_code == 403
    assert not hay_sesion(cliente), "una sesion muerta no debe quedar en el navegador"
    assert "Iniciar sesion" in cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)


def test_un_token_muerto_sin_refresh_no_intenta_renovar(cliente: FlaskClient) -> None:
    """Un access token caducado sin refresh no es una sesion que renovar.

    Es el caso de una sesion a la que solo le queda el token de acceso: la
    respuesta correcta es tratar al visitante como visitante, no disparar una
    renovacion que no puede funcionar.
    """
    caducar_access_token(cliente)

    respuesta = cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS)

    assert respuesta.status_code == 403
    assert "Iniciar sesion" in cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)


# --------------------------------------------------------------- logout


def test_el_logout_borra_las_credenciales_locales(cliente: FlaskClient) -> None:
    """GIVEN una sesion abierta / WHEN se confirma el cierre / THEN ni el acceso
    ni el refresh siguen en la sesion del servidor.

    Lo que se comprueba es el almacen y no la navegacion: que la barra deje de
    mostrar el area no prueba nada, porque tambien pasaria si la vista mintiera
    sobre quien es el visitante.
    """
    from ticket_web.auth import CLAVE_ACCESO, CLAVE_REFRESH

    registrarse(cliente)
    assert hay_sesion(cliente)

    respuesta = enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    assert respuesta.status_code == 303
    with cliente.session_transaction(base_url=ORIGEN_HTTPS) as sesion:
        assert CLAVE_ACCESO not in sesion
        assert CLAVE_REFRESH not in sesion
    assert cliente.get("/mi-cuenta/", base_url=ORIGEN_HTTPS).status_code == 403


def test_el_logout_revoca_el_refresh_en_la_api(cliente: FlaskClient) -> None:
    """Borrar la copia local no basta: el refresh tambien se revoca en la API.

    Es el otro lado del logout. Un token que sobrevive al cierre sirve para abrir
    sesion hasta que caduque, y da igual que el navegador ya no lo tenga.
    """
    registrarse(cliente)
    robado = refresh_de(cliente)
    assert robado is not None

    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    with db_session.get_sessionmaker()() as sesion_db:
        fila = sesion_db.scalar(
            select(AuthSession).order_by(AuthSession.created_at.desc()).limit(1)
        )
        assert fila is not None
        assert fila.revoked_at is not None, "el logout no revoco el refresh en la API"

    # El token que el navegador se llevo ya no renueva nada.
    with db_session.get_sessionmaker()() as sesion_db:
        fila = sesion_db.scalar(
            select(AuthSession).order_by(AuthSession.created_at.desc()).limit(1)
        )
        assert fila is not None
        assert fila.esta_revocada


def test_una_sesion_sin_renovar_no_puede_reabrirse(cliente: FlaskClient) -> None:
    """Tras el logout, una peticion con el refresh robado no recupera la sesion.

    Se pide la renovacion directamente a la API, que es donde se decide: la web no
    participa y por eso mismo no hay forma de que lo evite.
    """
    from fastapi.testclient import TestClient
    from ticket_api.main import create_app as create_api_app

    registrarse(cliente)
    robado = refresh_de(cliente)
    assert robado is not None
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    with TestClient(create_api_app()) as api:
        respuesta = api.post("/api/v1/auth/refresh", json={"refresh_token": robado})

    assert respuesta.status_code == 401


def test_la_navegacion_de_visitante_no_ofrece_areas_tras_el_logout(
    cliente: FlaskClient,
) -> None:
    """El cierre devuelve a la portada como visitante, sin restos del area."""
    registrarse(cliente)
    enviar(cliente, "/auth/salir", token_csrf(cliente, "/mi-cuenta/"))

    html = cliente.get("/", base_url=ORIGEN_HTTPS).get_data(as_text=True)

    assert "Iniciar sesion" in html
    assert "Mi cuenta" not in html
