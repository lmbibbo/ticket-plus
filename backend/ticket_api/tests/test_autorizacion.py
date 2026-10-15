"""3.7 - Dependencias de rol y propiedad del recurso.

La matriz se prueba contra rutas de prueba registradas en la propia instancia
de FastAPI, no contra las de eventos: `events` es de 4.1 y 4.2, y una prueba que
dependiera de ellas mediria el orden de las fases en lugar de la autorizacion. Las
rutas de prueba declaran las mismas dependencias que usaran las de verdad, asi
que lo que se comprueba aqui es el codigo de autorizacion y no un doble.

Lo que la matriz afirma:

- cada rol entra solo en lo suyo, y el acceso denegado es 403 con el codigo
  estable, no un 404 ni un 500;
- un evento ajeno a un proveedor se deniega sin revelar nada del recurso;
- el rol y la cuenta se leen de la base, no del token: un token firmado con
  `role: admin` a nombre de un cliente no le da permisos de administrador, y una
  suspension corta las peticiones en curso aunque el token siga vigente.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.api.deps import ActorDep, AdminDep, ClienteDep, PropietarioDep
from ticket_api.config import Settings
from ticket_api.db.base import Base
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE
from ticket_api.models import AuthSession, Role, User, UserStatus
from ticket_api.security.passwords import hash_password
from ticket_api.security.tokens import EmisorDeTokens
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA, clave_efimera

TABLAS_DE_PRUEBA.append(Base.metadata)

CONTRASENA = "contrasena-de-prueba-1"

LOGIN = "/api/v1/auth/login"
#: Identificador de un tercero que no es ninguna de las cuentas de la prueba.
AJENO = uuid.UUID("11111111-2222-3333-4444-555555555555")

@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Esquema real antes y despues de cada prueba.

    Estas pruebas hacen login de verdad -es como se consigue un token valido-, asi
    que escriben en la base y el aislamiento no puede venir de un `rollback`.
    """
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


@pytest.fixture
def api(app: FastAPI) -> FastAPI:
    """API con las rutas protegidas que la matriz va a recorrer.

    Se declaran con las mismas dependencias que usaran las de verdad. No son un
    doble: si `exigir_duenno` se rompiera, estas pruebas caerian.
    """

    @app.get("/api/v1/prueba/abierta")
    def abierta(actor: ActorDep) -> dict[str, str]:
        return {"rol": actor.rol.value}

    @app.get("/api/v1/prueba/admin")
    def solo_admin(actor: AdminDep) -> dict[str, str]:
        return {"rol": actor.rol.value}

    @app.get("/api/v1/prueba/proveedor")
    def solo_proveedor(actor: PropietarioDep) -> dict[str, str]:
        return {"rol": actor.rol.value}

    @app.get("/api/v1/prueba/cliente")
    def solo_cliente(actor: ClienteDep) -> dict[str, str]:
        return {"rol": actor.rol.value}

    @app.get("/api/v1/prueba/recurso/{duenno}")
    def recurso_ajeno(actor: ActorDep, duenno: uuid.UUID) -> dict[str, str]:
        actor.exigir_duenno(duenno)
        return {"ok": "propio"}

    @app.get("/api/v1/prueba/recurso-de-un-administrador/{duenno}")
    def recurso_para_administrador(actor: ActorDep, duenno: uuid.UUID) -> dict[str, str]:
        # La excepcion del administrador no esta en `exigir_duenno`: decidirla
        # aqui, y no en el metodo, es lo que permite que un cliente no la active
        # por un descuido de la ruta.
        if not actor.es_uno_de(Role.ADMIN):
            actor.exigir_duenno(duenno)
        return {"ok": "accede"}

    return app


@pytest.fixture
def cliente(
    api: FastAPI,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Cliente HTTP contra la API con el motor de pruebas detras."""
    monkeypatch.setattr("ticket_api.db.session.get_sessionmaker", lambda: session_factory)
    with TestClient(api, raise_server_exceptions=False) as instancia:
        yield instancia


def _cuenta(
    sesion: Session,
    email: str,
    role: str = Role.CLIENT.value,
    status: str = UserStatus.ACTIVE.value,
) -> User:
    """Crea una cuenta activa con hash real, para poder entrar con ella.

    Se hashea de verdad porque el token que se usa en las pruebas sale de un
    login contra la API: un hash inventado daria un 401 que no dice nada de
    autorizacion.
    """
    usuario = User(email=email, password_hash=hash_password(CONTRASENA), role=role, status=status)
    sesion.add(usuario)
    sesion.commit()
    return usuario


def _token(cliente: TestClient, email: str) -> str:
    """Access token de un login correcto."""
    respuesta = cliente.post(LOGIN, json={"email": email, "password": CONTRASENA})
    assert respuesta.status_code == 200, respuesta.text
    cuerpo: dict[str, Any] = respuesta.json()
    return str(cuerpo["access_token"])


def _con_token(cliente: TestClient, ruta: str, token: str | None) -> Any:
    cabeceras = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return cliente.get(ruta, headers=cabeceras)


def _problema(respuesta: Any) -> dict[str, Any]:
    assert respuesta.status_code >= 400, respuesta.text
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


class TestMatrizDeRoles:
    @pytest.mark.parametrize(
        ("rol", "ruta", "esperado"),
        [
            (Role.ADMIN.value, "/api/v1/prueba/admin", 200),
            (Role.EVENT_OWNER.value, "/api/v1/prueba/admin", 403),
            (Role.CLIENT.value, "/api/v1/prueba/admin", 403),
            (Role.ADMIN.value, "/api/v1/prueba/proveedor", 403),
            (Role.EVENT_OWNER.value, "/api/v1/prueba/proveedor", 200),
            (Role.CLIENT.value, "/api/v1/prueba/proveedor", 403),
            (Role.ADMIN.value, "/api/v1/prueba/cliente", 403),
            (Role.EVENT_OWNER.value, "/api/v1/prueba/cliente", 403),
            (Role.CLIENT.value, "/api/v1/prueba/cliente", 200),
        ],
    )
    def test_cada_rol_entra_solo_en_su_ruta(
        self, cliente: TestClient, sesion: Session, rol: str, ruta: str, esperado: int
    ) -> None:
        _cuenta(sesion, "persona@ejemplo.com", role=rol)
        token = _token(cliente, "persona@ejemplo.com")

        respuesta = _con_token(cliente, ruta, token)

        assert respuesta.status_code == esperado, respuesta.text

    def test_cualquier_rol_autenticado_entra_sin_exigir_rol(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """`ActorDep` es el punto de partida comun: autentica, pero no restringe."""
        _cuenta(sesion, "persona@ejemplo.com", role=Role.CLIENT.value)
        token = _token(cliente, "persona@ejemplo.com")

        assert _con_token(cliente, "/api/v1/prueba/abierta", token).status_code == 200

    def test_el_deniego_por_rol_es_403_con_codigo_estable(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """El codigo importa mas que el texto: la web muestra un formulario u
        otro segun el, y `forbidden` no es un fallo que pueda reintentarse."""
        _cuenta(sesion, "persona@ejemplo.com", role=Role.CLIENT.value)
        token = _token(cliente, "persona@ejemplo.com")

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/admin", token))

        assert cuerpo["code"] == "forbidden"
        assert cuerpo["status"] == 403


class TestPropiedadDelRecurso:
    @pytest.mark.parametrize(
        ("rol", "esperado"),
        [
            (Role.EVENT_OWNER.value, 200),
            (Role.CLIENT.value, 200),
        ],
    )
    def test_el_duenno_entra_a_su_recurso(
        self, cliente: TestClient, sesion: Session, rol: str, esperado: int
    ) -> None:
        cuenta = _cuenta(sesion, "duenno@ejemplo.com", role=rol)
        token = _token(cliente, "duenno@ejemplo.com")

        respuesta = _con_token(cliente, f"/api/v1/prueba/recurso/{cuenta.id}", token)

        assert respuesta.status_code == esperado, respuesta.text

    def test_un_evento_ajeno_se_deniega_sin_revelar_datos(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Responder 404 confirmaria que el evento existe, y un cuerpo distinto
        para cada caso tambien: denegar un recurso de otro y denegar uno
        inexistente tienen que ser indistinguibles, salvo por la ruta que ya
        conoce quien pregunta."""
        _cuenta(sesion, "duenno@ejemplo.com", role=Role.EVENT_OWNER.value)
        token = _token(cliente, "duenno@ejemplo.com")

        respuesta = _con_token(cliente, f"/api/v1/prueba/recurso/{AJENO}", token)
        inexistente = _con_token(cliente, f"/api/v1/prueba/recurso/{uuid.uuid4()}", token)

        cuerpo = _problema(respuesta)
        otro = _problema(inexistente)
        assert respuesta.status_code == 403
        assert cuerpo["code"] == "forbidden_not_owner"
        assert set(cuerpo) <= {"type", "title", "status", "detail", "code", "instance"}
        sin_ruta = {clave: valor for clave, valor in cuerpo.items() if clave != "instance"}
        assert sin_ruta == {clave: valor for clave, valor in otro.items() if clave != "instance"}

    def test_el_administrador_llega_a_cualquier_recurso(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """La excepcion del administrador vive en la ruta, no en `exigir_duenno`.

        Asi es como la especificacion pide que el administrador tenga acceso a
        todos los eventos sin que un cliente pueda heredarlo por descuido.
        """
        _cuenta(sesion, "jefe@ejemplo.com", role=Role.ADMIN.value)
        token = _token(cliente, "jefe@ejemplo.com")

        ajeno = _con_token(cliente, f"/api/v1/prueba/recurso-de-un-administrador/{AJENO}", token)

        assert ajeno.status_code == 200, ajeno.text

    def test_un_cliente_no_hereda_la_excepcion_del_administrador(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _cuenta(sesion, "persona@ejemplo.com", role=Role.CLIENT.value)
        token = _token(cliente, "persona@ejemplo.com")

        respuesta = _con_token(
            cliente, f"/api/v1/prueba/recurso-de-un-administrador/{AJENO}", token
        )

        assert respuesta.status_code == 403, respuesta.text


class TestLaPeticionSeAtribuye:
    def test_una_cuenta_suspendida_no_puede_operar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN una cuenta suspendida con un token vigente / WHEN llama a una
        ruta protegida / THEN se rechaza.

        Es la razon de que la peticion vuelva a mirar la base y no se fie solo
        del token: sin esto, la suspension tardaria lo que le quedase de vida al
        token.
        """
        cuenta = _cuenta(sesion, "persona@ejemplo.com", role=Role.ADMIN.value)
        token = _token(cliente, "persona@ejemplo.com")
        cuenta.status = UserStatus.SUSPENDED.value
        sesion.commit()

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/admin", token))

        assert cuerpo["code"] == "account_not_active"

    def test_una_cuenta_baja_no_puede_operar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cuenta = _cuenta(sesion, "persona@ejemplo.com")
        token = _token(cliente, "persona@ejemplo.com")
        cuenta.status = UserStatus.DELETED.value
        sesion.commit()

        assert _con_token(cliente, "/api/v1/prueba/abierta", token).status_code == 401

    def test_un_token_de_una_sesion_cerrada_no_operan(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Cerrar sesion tiene que cortar tambien las peticiones en curso, no solo
        las renovaciones."""
        _cuenta(sesion, "persona@ejemplo.com")
        emitido = cliente.post(LOGIN, json={"email": "persona@ejemplo.com", "password": CONTRASENA})
        refresh = emitido.json()["refresh_token"]
        token = emitido.json()["access_token"]
        cliente.post("/api/v1/auth/logout", json={"refresh_token": refresh})

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/abierta", token))

        assert cuerpo["code"] == "session_revoked"

    def test_una_sesion_caducada_no_opera(self, cliente: TestClient, sesion: Session) -> None:
        """La caducidad de la sesion se comprueba aunque el token siga vivo.

        El token caduca en quince minutos y la sesion en quince dias, asi que es
        el caso normal, no una excepcion: renovar es lo que mantiene la sesion
        viva.
        """
        cuenta = _cuenta(sesion, "persona@ejemplo.com")
        token = _token(cliente, "persona@ejemplo.com")
        registro = _primera_sesion(sesion, cuenta.id)
        registro.expires_at = datetime.now(UTC) - timedelta(days=1)
        sesion.commit()

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/abierta", token))

        assert cuerpo["code"] == "session_revoked"


class TestElTokenNoEsLaAutoridad:
    def test_el_rol_se_lee_de_la_base_y_no_del_token(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        """Un token firmado con `role: admin` a nombre de un cliente no abre las
        rutas de administrador.

        Si el rol se leyera de la claim, bastaria con firmar uno mismo -con la
        clave, que en un despliegue comprometido es lo que se busca- para
        escalarse. El rol sale de la fila de `users`, que es lo unico que no
        controla quien lleva el token.
        """
        cuenta = _cuenta(sesion, "persona@ejemplo.com", role=Role.CLIENT.value)
        _token(cliente, "persona@ejemplo.com")
        sesion_id = _primera_sesion(sesion, cuenta.id).id
        token, _ = EmisorDeTokens(settings).firmar_acceso(cuenta.id, Role.ADMIN.value, sesion_id)

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/admin", token))

        assert cuerpo["code"] == "forbidden"

    def test_un_token_que_apunta_a_la_sesion_de_otro_no_sirve(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        """La sesion que declara el token tiene que ser de la misma cuenta.

        Sin esta comprobacion, el token de un cliente valido con el `sid` de otro
        pasa: ambas filas existen y ambas estan vivas, pero no son la misma.
        """
        uno = _cuenta(sesion, "uno@ejemplo.com")
        otro = _cuenta(sesion, "otro@ejemplo.com")
        _token(cliente, "otro@ejemplo.com")
        sesion_de_otro = _primera_sesion(sesion, otro.id).id
        _token(cliente, "uno@ejemplo.com")
        token, _ = EmisorDeTokens(settings).firmar_acceso(
            uno.id, Role.CLIENT.value, sesion_de_otro
        )

        assert _con_token(cliente, "/api/v1/prueba/abierta", token).status_code == 401

    def test_un_token_firmado_con_otra_clave_no_sirve(
        self, cliente: TestClient, sesion: Session, settings: Settings
    ) -> None:
        _cuenta(sesion, "persona@ejemplo.com")
        ajena = EmisorDeTokens(
            settings.model_copy(update={"jwt_private_key_b64": clave_efimera()})
        )
        token, _ = ajena.firmar_acceso(uuid.uuid4(), Role.ADMIN.value, uuid.uuid4())

        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/admin", token))

        assert cuerpo["status"] == 401
        assert cuerpo["code"] == "unauthenticated"


class TestCabeceraAuthorization:
    def test_sin_cabecera_no_entra(self, cliente: TestClient) -> None:
        cuerpo = _problema(_con_token(cliente, "/api/v1/prueba/abierta", None))

        assert cuerpo["code"] == "unauthenticated"

    def test_un_esquema_distinto_no_entra(self, cliente: TestClient) -> None:
        """Una cabecera mal formada y un token invalido dan el mismo codigo pero
        no el mismo texto.

        El codigo comun es lo que la web necesita; el texto distinto es lo que
        le dice a quien integra si se ha equivocado en como escribir la
        cabecera o si lo que manda esta mal.
        """
        respuesta = cliente.get(
            "/api/v1/prueba/abierta", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )

        cuerpo = _problema(respuesta)
        assert cuerpo["code"] == "unauthenticated"
        assert "Bearer" in cuerpo["detail"]

    def test_un_token_sin_esquema_no_entra(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/v1/prueba/abierta", headers={"Authorization": "abc.def.ghi"})

        assert _problema(respuesta)["code"] == "unauthenticated"

    def test_una_cabecera_vacia_no_entra(self, cliente: TestClient) -> None:
        respuesta = cliente.get("/api/v1/prueba/abierta", headers={"Authorization": "Bearer "})

        assert _problema(respuesta)["code"] == "unauthenticated"

    def test_un_token_inventado_no_entra(self, cliente: TestClient) -> None:
        cuerpo = _problema(
            _con_token(cliente, "/api/v1/prueba/abierta", "eyJhbGciOiJFZERTQSJ9.aaa.bbb")
        )

        assert cuerpo["code"] == "unauthenticated"
        assert "Bearer" not in cuerpo["detail"]

    def test_el_esquema_no_distingue_mayusculas(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """RFC 7235 lo define asi, y la cabecera la pone el cliente."""
        _cuenta(sesion, "persona@ejemplo.com")
        token = _token(cliente, "persona@ejemplo.com")

        respuesta = cliente.get(
            "/api/v1/prueba/abierta", headers={"Authorization": f"bearer {token}"}
        )

        assert respuesta.status_code == 200, respuesta.text


class TestBitacora:
    def test_una_accion_administrativa_queda_con_la_identidad_de_quien_la_hace(
        self, cliente: TestClient, sesion: Session, captured_lines: Any
    ) -> None:
        """La especificacion pide que la accion quede registrada con el
        administrador, no con su `request_id`."""
        cuenta = _cuenta(sesion, "jefe@ejemplo.com", role=Role.ADMIN.value)
        token = _token(cliente, "jefe@ejemplo.com")

        with captured_lines() as lineas:
            cliente.get("/api/v1/prueba/admin", headers={"Authorization": f"Bearer {token}"})

        accion = [linea for linea in lineas if linea["route"] == "/api/v1/prueba/admin"]
        assert len(accion) == 1, lineas
        assert accion[0]["user_id"] == str(cuenta.id)

    def test_una_peticion_sin_identificar_no_inventa_un_usuario(
        self, cliente: TestClient, captured_lines: Any
    ) -> None:
        with captured_lines() as lineas:
            _con_token(cliente, "/api/v1/prueba/abierta", None)

        assert lineas[0].get("user_id") is None


def _primera_sesion(sesion: Session, usuario_id: uuid.UUID) -> AuthSession:
    registros = sesion.scalars(
        select(AuthSession).where(AuthSession.user_id == usuario_id)
    ).all()
    assert len(registros) == 1, "se esperaba exactamente una sesion"
    return registros[0]


