"""6.1 - La sesion vive en el servidor y la cookie solo lleva un identificador.

La especificacion es explicita en las tres banderas de la cookie y en que el
navegador no debe recibir nada mas. Las pruebas atacan las dos cosas por
separado: el contenido, que no sale nunca, y las banderas, que se emiten siempre.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from flask import Flask, Response, session
from werkzeug.test import EnvironBuilder, TestResponse

from ticket_web.session_store import SESSION_IDLE_SECONDS, ServerSideSessionInterface

#: Base HTTPS porque la cookie es `Secure`: sobre HTTP el cliente de pruebas
#: correctamente no la reenvia, y la prueba mediria el transporte, no el
#: contrato de la sesion.
ORIGEN_HTTPS = "https://localhost"

COOKIE = "ticketplus_session"

#: Contenido con forma de credencial, para comprobar que no sale hacia el
#: navegador aunque la sesion lo contenga.
SECRETO = "eyJhbGciOiJFZERTQSJ9.cuerpo.de.la.entrada.firma"


def _app_con_sesion() -> Flask:
    """Aplicacion minima que escribe en la sesion, para ejercitar el almacen."""
    app = Flask(__name__)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME=COOKIE,
    )
    app.session_interface = ServerSideSessionInterface()

    @app.get("/guardar")
    def guardar() -> Response:
        session["access_token"] = SECRETO
        return Response("guardado")

    @app.get("/leer")
    def leer() -> Response:
        return Response(str(session.get("access_token", "")))

    @app.get("/cerrar")
    def cerrar() -> Response:
        session.clear()
        return Response("cerrado")

    return app


def _valor(respuesta: TestResponse) -> str:
    """Valor de la cookie `ticketplus_session` emitida."""
    for parte in respuesta.headers.getlist("Set-Cookie"):
        if parte.startswith(f"{COOKIE}="):
            return parte.split("=", 1)[1].split(";")[0]
    raise AssertionError(f"no se emitio la cookie {COOKIE}: {respuesta.headers}")


def _atributos(respuesta: TestResponse) -> dict[str, str]:
    """Atributos de la cookie emitida, en minusculas y sin el valor."""
    cabecera = next(
        parte
        for parte in respuesta.headers.getlist("Set-Cookie")
        if parte.startswith(f"{COOKIE}=")
    )
    atributos: dict[str, str] = {}
    for segmento in cabecera.split(";")[1:]:
        clave, _, valor = segmento.strip().partition("=")
        atributos[clave.lower()] = valor
    return atributos


# ------------------------------------------------------------- la cookie


def test_la_cookie_lleva_solo_el_identificador_opaco() -> None:
    """GIVEN una sesion con un token dentro / WHEN se guarda / THEN la cookie
    lleva el identificador del servidor y nada del contenido."""
    respuesta = _app_con_sesion().test_client().get("/guardar", base_url=ORIGEN_HTTPS)

    valor = _valor(respuesta)
    assert SECRETO not in valor, "el contenido de la sesion salio en la cookie"
    assert "access_token" not in valor
    assert len(valor) == 36, f"el identificador deberia ser un UUID: {valor!r}"
    uuid.UUID(valor), "el identificador deberia ser un UUID"


def test_la_cookie_es_httponly_segura_y_de_samesite_lax() -> None:
    """Las tres banderas las exige la especificacion; se comprueban sobre la
    cabecera emitida, no sobre la configuracion, para no validar una intencion."""
    respuesta = _app_con_sesion().test_client().get("/guardar", base_url=ORIGEN_HTTPS)

    atributos = _atributos(respuesta)
    assert "httponly" in atributos, atributos
    assert "secure" in atributos, atributos
    assert atributos.get("samesite", "").lower() == "lax", atributos


def test_la_web_real_emite_la_cookie_con_las_tres_banderas(web_app: Flask) -> None:
    """La cookie de la aplicacion real sale igual de endurecida que la de la
    aplicacion de pruebas. Un olvido aqui dejaria la sesion expuesta en el
    despliegue sin que ninguna otra prueba se diera cuenta."""
    assert web_app.config["SESSION_COOKIE_NAME"] == COOKIE
    assert web_app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert web_app.config["SESSION_COOKIE_SECURE"] is True
    assert web_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# -------------------------------------------------- el contenido se queda


def test_el_contenido_sobrevive_entre_peticiones() -> None:
    """GIVEN un identificador en la cookie / WHEN el navegador vuelve / THEN el
    servidor reconstruye la sesion a partir del almacen, y la cookie no cambia."""
    cliente = _app_con_sesion().test_client()

    primera = cliente.get("/guardar", base_url=ORIGEN_HTTPS)
    segunda = cliente.get("/leer", base_url=ORIGEN_HTTPS)

    assert _valor(segunda) == _valor(primera)
    assert segunda.get_data(as_text=True) == SECRETO


def test_cerrar_la_sesion_borra_el_estado_del_servidor() -> None:
    """GIVEN una sesion abierta / WHEN se cierra / THEN el servidor ya no la
    conserva y la cookie se elimina: la copia del navegador no sirve de nada."""
    cliente = _app_con_sesion().test_client()

    cliente.get("/guardar", base_url=ORIGEN_HTTPS)
    cierre = cliente.get("/cerrar", base_url=ORIGEN_HTTPS)

    assert _valor(cierre) == "", "la cookie deberia quedar vacia"
    assert _atributos(cierre).get("max-age") == "0"
    assert cliente.get("/leer", base_url=ORIGEN_HTTPS).get_data(as_text=True) == ""


def test_una_cookie_ajena_no_abre_una_sesion() -> None:
    """Un identificador inventado no colarse en una sesion ajena: el almacen no
    lo conoce y la peticion sigue siendo anonima."""
    cliente = _app_con_sesion().test_client()
    cliente.set_cookie(COOKIE, "00000000-0000-0000-0000-000000000000", domain="localhost")

    assert cliente.get("/leer", base_url=ORIGEN_HTTPS).get_data(as_text=True) == ""


def _interfaz(app: Flask) -> ServerSideSessionInterface:
    """Interfaz de sesion ya instalada, con su almacen tipado."""
    interfaz = app.session_interface
    assert isinstance(interfaz, ServerSideSessionInterface)
    return interfaz


def test_una_sesion_caducada_se_descarta() -> None:
    """Una sesion sin actividad se retira del almacen. Conservarla haria crecer el
    almacen sin limite y mantendria viva una sesion que el usuario ya dio por
    terminada."""
    app = _app_con_sesion()
    almacen = _interfaz(app).store
    cliente = app.test_client()

    cliente.get("/guardar", base_url=ORIGEN_HTTPS)
    (sid,) = almacen
    contenido, _ = almacen[sid]
    almacen[sid] = (contenido, -(SESSION_IDLE_SECONDS + 1))

    assert cliente.get("/leer", base_url=ORIGEN_HTTPS).get_data(as_text=True) == ""
    assert not almacen, "la sesion caducada sigue ocupando sitio"


def test_cada_sesion_recibe_un_identificador_distinto() -> None:
    """Dos visitantes simultaneos no pueden caer en la misma sesion: si el
    identificador se derivara de la hora o del contenido, colisionarian."""
    app = _app_con_sesion()
    interfaz = _interfaz(app)

    identificadores = {
        interfaz.open_session(app, _peticion()).sid
        for _ in range(8)
    }

    assert len(identificadores) == 8


def _peticion() -> Any:
    """Peticion sin cookies, para abrir una sesion fuera de un servidor real."""
    return EnvironBuilder(path="/").get_request()


# -------------------------------------------------------- el aislamiento


def test_la_web_no_abre_conexion_con_la_base_de_datos() -> None:
    """El almacen de sesiones esta separado del esquema de dominio: la web no
    importa el cliente de base de datos, asi que no puede escribir en las tablas
    de negocio por accidente."""
    import ticket_web.session_store as almacen

    texto = Path(almacen.__file__).read_text(encoding="utf-8")
    for prohibido in ("sqlalchemy", "psycopg", "ticket_api"):
        assert prohibido not in texto, f"el almacen de sesiones depende de {prohibido}"