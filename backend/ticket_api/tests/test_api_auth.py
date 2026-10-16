"""3.6 - Login, refresh y logout bajo `/api/v1/auth`.

Pruebas de contrato: van contra la API real con la base de pruebas detras, y
comprueban el efecto en `auth_sessions`, no la forma del codigo. Lo que importa
aquí es lo que un cliente puede hacer con la respuesta:

- solo las cuentas activas emiten tokens, y las demas fallan igual entre si;
- renovar rota el token, y reutilizar el anterior corta la cadena;
- cerrar sesion revoca, y es idempotente.

La clave de firma la pone la fixture `settings` y las rutas la leen de
`app.state`, asi que el token que devuelve la API se puede verificar en la misma
prueba. Si las rutas tomaran la clave del singleton global, verificarian con la
clave de desarrollo y estas pruebas estarian comparando firmas que no coinciden.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.config import Settings
from ticket_api.db.base import Base
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE
from ticket_api.models import AuthSession, Role, User, UserStatus
from ticket_api.security.passwords import hash_password, verify_password
from ticket_api.security.tokens import EmisorDeTokens, hash_de_refresh
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

#: Cumple el minimo de longitud sin repetirla en cada prueba. Es una contrasena
#: de ejemplo, no una credencial: no se usa en ningun entorno real.
CONTRASENA = "contrasena-de-prueba-1"
#: Segunda contrasena valida, para probar la que no es. Tamien de ejemplo.
OTRA = "otra-contrasena-1"

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Esquema real antes y despues de cada prueba.

    Estas pruebas confirman de verdad -login, refresh y logout escriben-, asi que
    la transaccion que las envuelve no deshace nada: el aislamiento tiene que
    venir de tirar las tablas, como en la suite de 3.4.
    """
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
    """Cliente HTTP contra la API con el motor de pruebas detras."""
    monkeypatch.setattr("ticket_api.db.session.get_sessionmaker", lambda: session_factory)
    with TestClient(app, raise_server_exceptions=False) as instancia:
        yield instancia


def _cuenta(
    sesion: Session,
    email: str = "ana@ejemplo.com",
    password: str = CONTRASENA,
    role: str = Role.CLIENT.value,
    status: str = UserStatus.ACTIVE.value,
) -> User:
    """Crea una cuenta con hash real, para que el login pueda verificarla.

    Se hashea de verdad y no se copia un hash de ejemplo: una prueba que usara un
    hash inventado comprobaria que la ruta existe y no que la credencial sirve.
    """
    usuario = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        status=status,
    )
    sesion.add(usuario)
    sesion.commit()
    return usuario


def _sesiones(sesion: Session) -> list[AuthSession]:
    return list(sesion.scalars(select(AuthSession).order_by(AuthSession.created_at)).all())


def _entrar(cliente: TestClient, email: str = "ana@ejemplo.com") -> dict[str, Any]:
    """Login que debe funcionar; si no, la prueba que lo llama no tiene sentido."""
    respuesta = cliente.post(LOGIN, json={"email": email, "password": CONTRASENA})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


def _problema(respuesta: Any) -> dict[str, Any]:
    assert respuesta.status_code >= 400, respuesta.text
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


class TestInicioDeSesion:
    def test_un_cliente_activo_recibe_el_par_de_tokens(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        cuerpo = _entrar(cliente)

        assert cuerpo["token_type"] == "Bearer"  # noqa: S105 - es el esquema, no una credencial
        assert cuerpo["access_token"] and cuerpo["refresh_token"]
        assert len(cuerpo) == 4, f"se Publican mas campos de los previstos: {sorted(cuerpo)}"

    def test_expiracion_publicada_es_la_de_la_configuracion(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        _cuenta(sesion)
        cuerpo = _entrar(cliente)

        assert cuerpo["expires_in"] == settings.access_token_ttl_seconds

    def test_el_token_de_acceso_sirve_y_lleva_la_identidad_de_la_cuenta(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        cuenta = _cuenta(sesion, role=Role.CLIENT.value)
        cuerpo = _entrar(cliente)

        claims = EmisorDeTokens(settings).verificar_acceso(cuerpo["access_token"])
        registros = _sesiones(sesion)
        assert claims.usuario_id == cuenta.id
        assert claims.rol == Role.CLIENT.value
        assert claims.sesion_id == registros[0].id
        assert registros[0].user_id == cuenta.id

    def test_el_refresh_entregado_es_el_mismo_que_esta_guardado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Lo que se guarda es el hash del token entregado, no otro hash.

        Sin esta comprobacion, un hash calculado con otra sal o con otro token
        haria que toda renovacion fallase con `invalid_credentials`, y el fallo
        apareceria en la primera peticion posterior al login.
        """
        _cuenta(sesion)
        cuerpo = _entrar(cliente)

        assert _sesiones(sesion)[0].refresh_token_hash == hash_de_refresh(cuerpo["refresh_token"])

    def test_el_correo_se_normaliza_al_entrar(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion, email="ana@ejemplo.com")
        respuesta = cliente.post(LOGIN, json={"email": "ANA@Ejemplo.com", "password": CONTRASENA})

        assert respuesta.status_code == 200, respuesta.text

    def test_contrasena_incorrecta_no_emite_nada(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        respuesta = cliente.post(
            LOGIN, json={"email": "ana@ejemplo.com", "password": OTRA}
        )

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "invalid_credentials"
        assert _sesiones(sesion) == []

    def test_correo_inexistente_es_indistinguible_de_contrasena_mala(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Los dos fallos devuelven byte a byte lo mismo.

        Un codigo distinto por caso -"usuario no encontrado" frente a "contrasena
        incorrecta"- convierte el login en un buscador de correos registrados. Se
        comparan los tres campos visibles, no solo el estado, porque tambien
        `title` y `detail` van a la pantalla del navegador.
        """
        _cuenta(sesion)
        mala = _problema(cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": OTRA}))
        desconocido = _problema(
            cliente.post(LOGIN, json={"email": "nadie@ejemplo.com", "password": CONTRASENA})
        )

        assert mala == desconocido

    def test_cuenta_suspendida_no_puede_entrar(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion, status=UserStatus.SUSPENDED.value)
        respuesta = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": CONTRASENA})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "invalid_credentials"
        assert _sesiones(sesion) == []

    def test_proveedor_pendiente_no_puede_entrar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Aprobar proveedores es de 3.8; hasta entonces `pending` no entra."""
        _cuenta(sesion, role=Role.EVENT_OWNER.value, status=UserStatus.PENDING.value)
        respuesta = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": CONTRASENA})

        assert respuesta.status_code == 401, respuesta.text
        assert _sesiones(sesion) == []

    def test_proveedor_aprobado_si_puede_entrar(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion, role=Role.EVENT_OWNER.value, status=UserStatus.ACTIVE.value)

        respuesta = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": CONTRASENA})

        assert respuesta.status_code == 200, respuesta.text

    def test_cuenta_baja_no_puede_entrar(self, cliente: TestClient, sesion: Session) -> None:
        """Borrar es logico: la fila sigue, y `deleted` no es `active`."""
        _cuenta(sesion, status=UserStatus.DELETED.value)
        respuesta = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": CONTRASENA})

        assert respuesta.status_code == 401, respuesta.text

    def test_un_correo_mal_formado_es_error_de_validacion(self, cliente: TestClient) -> None:
        respuesta = cliente.post(LOGIN, json={"email": "no-es-un-correo", "password": CONTRASENA})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 422
        assert cuerpo["code"] == "validation_error"
        assert cuerpo["errors"][0]["field"] == "email"

    def test_una_contrasena_corta_es_un_401_y_no_un_422(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Un 422 por longitud revelaria la politica de contrasenas a quien las
        prueba, y ademas antes de comprobar la credencial."""
        _cuenta(sesion)
        respuesta = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": "corta"})

        assert respuesta.status_code == 401, respuesta.text

    def test_campos_desconocidos_se_rechazan(self, cliente: TestClient) -> None:
        respuesta = cliente.post(
            LOGIN, json={"email": "ana@ejemplo.com", "password": CONTRASENA, "rol": "admin"}
        )

        assert _problema(respuesta)["code"] == "validation_error"

    def test_un_correo_inexistente_tambien_paga_el_tiempo_de_una_verificacion(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sin hash que verificar, este camino responderia en microsegundos.

        Medir cuanto tarda un login permite enumerar cuentas, asi que el camino
        sin cuenta verifica contra un hash senuelo. Se comprueba que la
        verificacion se llama, no que tarde: un umbral de tiempo seria una
        prueba que falla en un Portable CI.
        """
        llamadas: list[str] = []

        def contando(hash_guardado: str, password: str) -> bool:
            llamadas.append(hash_guardado)
            return verify_password(hash_guardado, password)

        monkeypatch.setattr(
            "ticket_api.services.autenticacion.verify_password", contando
        )

        respuesta = cliente.post(LOGIN, json={"email": "nadie@ejemplo.com", "password": "x"})

        assert respuesta.status_code == 401
        assert len(llamadas) == 1, "el camino sin cuenta no verifico ningun hash"

    def test_ninguna_respuesta_de_login_lleva_la_contrasena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        ok = _entrar(cliente)
        fallo = cliente.post(LOGIN, json={"email": "ana@ejemplo.com", "password": "mal-clave-aqui"})

        assert CONTRASENA not in str(ok)
        assert "password_hash" not in str(ok)
        assert "mal-clave-aqui" not in fallo.text


class TestRenovacion:
    def test_renovar_devuelve_tokens_nuevos(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion)
        anterior = _entrar(cliente)

        respuesta = cliente.post(REFRESH, json={"refresh_token": anterior["refresh_token"]})

        assert respuesta.status_code == 200, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["access_token"] != anterior["access_token"]
        assert cuerpo["refresh_token"] != anterior["refresh_token"]

    def test_la_sesion_anterior_queda_reemplazada_y_revocada(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        anterior = _entrar(cliente)
        cliente.post(REFRESH, json={"refresh_token": anterior["refresh_token"]})

        sesion.expire_all()
        filas = _sesiones(sesion)
        assert len(filas) == 2
        vieja, nueva = filas[0], filas[1]
        assert vieja.revoked_at is not None
        assert vieja.replaced_by_id == nueva.id
        assert nueva.revoked_at is None

    def test_reutilizar_el_token_anterior_corta_la_cadena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN un refresh ya rotado / WHEN se vuelve a presentar / THEN se
        rechaza con `refresh_token_reused` y la cadena entera queda revocada."""
        _cuenta(sesion)
        anterior = _entrar(cliente)
        renovada = cliente.post(REFRESH, json={"refresh_token": anterior["refresh_token"]})
        assert renovada.status_code == 200, renovada.text

        respuesta = cliente.post(REFRESH, json={"refresh_token": anterior["refresh_token"]})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "refresh_token_reused"
        sesion.expire_all()
        assert all(fila.revoked_at is not None for fila in _sesiones(sesion))

    def test_renovar_con_un_token_desconocido_es_401(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        respuesta = cliente.post(REFRESH, json={"refresh_token": "rt_inventado"})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "invalid_credentials"
        assert _sesiones(sesion) == []

    def test_renovar_cuadro_veces_mantiene_los_cinco_eslabones(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Una cadena larga sigue siendo una cadena: el vinculo no se pierde."""
        _cuenta(sesion)
        tokens = _entrar(cliente)["refresh_token"]
        for _ in range(4):
            respuesta = cliente.post(REFRESH, json={"refresh_token": tokens})
            assert respuesta.status_code == 200, respuesta.text
            tokens = respuesta.json()["refresh_token"]

        sesion.expire_all()
        filas = _sesiones(sesion)
        assert len(filas) == 5
        assert [fila.replaced_by_id is not None for fila in filas[:-1]] == [True] * 4
        assert filas[-1].replaced_by_id is None

    def test_renovar_tras_suspender_la_cuenta_falla_y_corta_la_cadena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Una suspension tiene que cortar tambien las renovaciones: si solo
        cortara el login, bastaria un refresh vigente para seguir entrando."""
        cuenta = _cuenta(sesion)
        emitido = _entrar(cliente)
        cuenta.status = UserStatus.SUSPENDED.value
        sesion.commit()

        respuesta = cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "account_not_active"
        sesion.expire_all()
        assert all(fila.revoked_at is not None for fila in _sesiones(sesion))

    def test_renovar_conserva_el_rol_del_cuenta(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        _cuenta(sesion, role=Role.EVENT_OWNER.value)
        emitido = _entrar(cliente)

        respuesta = cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})

        claims = EmisorDeTokens(settings).verificar_acceso(respuesta.json()["access_token"])
        assert claims.rol == Role.EVENT_OWNER.value

    def test_dos_ingresos_son_cadenas_independientes(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        primero = _entrar(cliente)
        segundo = _entrar(cliente)

        sesion.expire_all()
        filas = _sesiones(sesion)
        assert filas[0].family_id != filas[1].family_id
        for emitido in (primero, segundo):
            respuesta = cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})
            assert respuesta.status_code == 200, respuesta.text

    def test_el_token_de_acceso_sigue_valiendo_despues_de_renovar(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        """Renovar rota el refresh, no el access token ya entregado.

        El access token es de corta duracion y se comprueba en cada peticion
        protegida; si renovarlo lo invalidara, la web tendria que renovar en cada
        navegacion.
        """
        _cuenta(sesion)
        emitido = _entrar(cliente)
        cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})

        claims = EmisorDeTokens(settings).verificar_acceso(emitido["access_token"])
        assert claims.sesion_id is not None


class TestCierreDeSesion:
    def test_cerrar_sesion_revoca_el_refresh(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion)
        emitido = _entrar(cliente)

        respuesta = cliente.post(LOGOUT, json={"refresh_token": emitido["refresh_token"]})

        assert respuesta.status_code == 204, respuesta.text
        assert respuesta.content == b""
        sesion.expire_all()
        assert _sesiones(sesion)[0].revoked_at is not None

    def test_tras_cerrar_sesion_no_se_puede_renovar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        emitido = _entrar(cliente)
        cliente.post(LOGOUT, json={"refresh_token": emitido["refresh_token"]})

        respuesta = cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 401
        assert cuerpo["code"] == "session_revoked"

    def test_cerrar_sesion_dos_veces_es_el_mismo_204(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        emitido = _entrar(cliente)
        cuerpo = {"refresh_token": emitido["refresh_token"]}

        primera = cliente.post(LOGOUT, json=cuerpo)
        segunda = cliente.post(LOGOUT, json=cuerpo)

        assert primera.status_code == segunda.status_code == 204

    def test_cerrar_sesion_con_un_token_desconocido_tambien_es_204(
        self, cliente: TestClient
    ) -> None:
        respuesta = cliente.post(LOGOUT, json={"refresh_token": "rt_inventado"})

        assert respuesta.status_code == 204, respuesta.text

    def test_cerrar_una_sesion_no_toca_las_otras(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion)
        primera = _entrar(cliente)
        segunda = _entrar(cliente)

        cliente.post(LOGOUT, json={"refresh_token": primera["refresh_token"]})

        sesion.expire_all()
        filas = sorted(_sesiones(sesion), key=lambda fila: fila.created_at)
        assert filas[0].revoked_at is not None
        assert filas[1].revoked_at is None
        sigue = cliente.post(REFRESH, json={"refresh_token": segunda["refresh_token"]})
        assert sigue.status_code == 200, sigue.text

    def test_cerrar_sesion_queda_registrado_sin_el_token(
        self, cliente: TestClient, sesion: Session, captured_lines: Any
    ) -> None:
        """La especificacion pide registrar el evento, y el token no aparece."""
        _cuenta(sesion)
        emitido = _entrar(cliente)

        with captured_lines() as lineas:
            cliente.post(LOGOUT, json={"refresh_token": emitido["refresh_token"]})

        cierre = [linea for linea in lineas if linea["route"] == LOGOUT]
        assert len(cierre) == 1, lineas
        assert cierre[0]["method"] == "POST"
        assert cierre[0]["status"] == 204
        assert emitido["refresh_token"] not in str(cierre[0])


class TestContrato:
    def test_las_tres_rutas_estan_publicadas(self, cliente: TestClient) -> None:
        esquema = cliente.get("/api/openapi.json").json()

        for ruta in (LOGIN, REFRESH, LOGOUT):
            assert ruta in esquema["paths"], f"{ruta} no esta en el OpenAPI"
            assert "post" in esquema["paths"][ruta], f"{ruta} no acepta POST"

    def test_login_y_refresh_publican_el_mismo_contrato_de_salida(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Los dos comparten `TokensResponse`.

        Si divergieran, el cliente tendria que distinguir "la respuesta de login"
        de "la de refresh" y no podria tratarlas igual, que es justo lo que la
        renovacion automatica necesita.
        """
        _cuenta(sesion)
        _entrar(cliente)
        referencia = cliente.get("/api/openapi.json").json()
        respuestas = {
            ruta: referencia["paths"][ruta]["post"]["responses"]["200"]
            for ruta in (LOGIN, REFRESH)
        }
        esquemas = {
            ruta: (respuesta.get("content", {}).get("application/json", {}).get("schema", {}))
            for ruta, respuesta in respuestas.items()
        }
        assert esquemas[LOGIN] == esquemas[REFRESH]
        assert esquemas[LOGIN]["$ref"] == "#/components/schemas/TokensResponse"
