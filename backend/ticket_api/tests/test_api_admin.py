"""3.8 - Administracion de cuentas: aprobar proveedores y suspender cuentas.

Lo que se comprueba es el efecto, no la forma de la respuesta: que aprobar
cambie el estado y deje entrar, que suspender corte las peticiones en curso del
suspendido, y sobre todo que **una operacion que no es de administracion no
cambie nada**. Ese ultimo caso se prueba mirando la base despues, no el codigo
de error: un 403 acompanado de un cambio de estado seria un fallo de seguridad,
no un fallo de contrato.

La cuenta de administrador no sale del registro publico -por diseno de 3.4- y la
crea `ticket_api.cli`, que tambien se prueba aqui.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.db.base import Base
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE
from ticket_api.models import AuthSession, Role, User, UserStatus
from ticket_api.security.passwords import hash_password
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

CONTRASENA = "contrasena-de-prueba-1"
OTRA = "otra-contrasena-1"

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
PENDIENTES = "/api/v1/admin/users"
APROBAR = "/api/v1/admin/users/{}/approve"
SUSPENDER = "/api/v1/admin/users/{}/suspend"


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Esquema real antes y despues de cada prueba: estas operaciones escriben."""
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
    """Cabecera `Authorization` de un login ya hecho."""
    return {"Authorization": f"Bearer {emitido['access_token']}"}


def _jefe(cliente: TestClient, sesion: Session) -> tuple[User, dict[str, str]]:
    """Administrador con su cabecera `Authorization`, listo para llamar."""
    cuenta = _cuenta(sesion, "jefe@ejemplo.com", role=Role.ADMIN.value)
    return cuenta, _con(_entrar(cliente, "jefe@ejemplo.com"))


def _estado_en_base(sesion: Session, usuario_id: uuid.UUID) -> str:
    sesion.expire_all()
    usuario = sesion.get(User, usuario_id)
    assert usuario is not None
    return usuario.status


def _problema(respuesta: Any) -> dict[str, Any]:
    assert respuesta.status_code >= 400, respuesta.text
    assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    cuerpo: dict[str, Any] = respuesta.json()
    return cuerpo


class TestListadoDePendientes:
    def test_solo_aparecen_los_proveedores_pendientes(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "pendiente@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )
        _cuenta(sesion, "cliente@ejemplo.com")
        _cuenta(
            sesion, "aprobado@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.ACTIVE.value,
        )
        _cuenta(
            sesion, "suspendido@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.SUSPENDED.value,
        )

        respuesta = cliente.get(PENDIENTES, headers=cabeceras)

        cuerpo = respuesta.json()
        assert respuesta.status_code == 200, respuesta.text
        assert [c["id"] for c in cuerpo] == [str(pendiente.id)]
        assert cuerpo[0]["email"] == "pendiente@ejemplo.com"

    def test_un_cliente_en_estado_pendiente_tampoco_aparece(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """El filtro mira rol y estado, no solo estado.

        Un cliente nunca nace pendiente por el registro publico, asi que esta
        fila solo puede existir si alguien la escribio por SQL, por una migracion
        o por un script. El listado no puede depender de que eso no ocurra: es la
        pantalla donde el administrador aprueba, y colar ahi una cuenta que no sea
        de proveedor haria que la aprobacion no aplicara.
        """
        _, cabeceras = _jefe(cliente, sesion)
        _cuenta(sesion, "raro@ejemplo.com", role=Role.CLIENT.value, status=UserStatus.PENDING.value)
        _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        respuesta = cliente.get(PENDIENTES, headers=cabeceras)

        assert [c["email"] for c in respuesta.json()] == ["proveedor@ejemplo.com"]

    def test_un_cliente_no_abre_el_listado(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion, "persona@ejemplo.com", role=Role.CLIENT.value)
        emitido = _entrar(cliente, "persona@ejemplo.com")

        respuesta = cliente.get(PENDIENTES, headers=_con(emitido))

        assert _problema(respuesta)["code"] == "forbidden"

    def test_sin_autenticar_no_abre_el_listado(self, cliente: TestClient) -> None:
        respuesta = cliente.get(PENDIENTES)

        assert _problema(respuesta)["code"] == "unauthenticated"

    def test_el_listado_no_devuelve_el_hash_de_la_contrasena(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        _cuenta(
            sesion, "pendiente@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        respuesta = cliente.get(PENDIENTES, headers=cabeceras)

        assert "password_hash" not in respuesta.text
        assert CONTRASENA not in respuesta.text


class TestAprobacion:
    def test_aprobar_pasa_el_proveedor_a_activo(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        respuesta = cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == "active"
        assert _estado_en_base(sesion, pendiente.id) == UserStatus.ACTIVE.value

    def test_el_proveedor_aprobado_ya_puede_entrar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )
        antes = cliente.post(LOGIN, json={"email": "proveedor@ejemplo.com", "password": CONTRASENA})
        assert antes.status_code == 401

        cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        assert cliente.post(
            LOGIN, json={"email": "proveedor@ejemplo.com", "password": CONTRASENA}
        ).status_code == 200

    def test_aprobar_no_abre_sesiones_por_adelantado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Aprobar quita una restriccion; no es un login disfrazado.

        Si aprobar emitiera tokens, el administrador obtendria una sesion en
        nombre del proveedor sin que este hubiera hecho nada, y la primera
        peticion de esa sesion ya no tendria al proveedor como responsable.
        """
        _, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        sesion.expire_all()
        propias = [
            registro
            for registro in sesion.scalars(select(AuthSession))
            if registro.user_id == pendiente.id
        ]
        assert propias == []

    def test_no_se_aprueba_un_cliente(self, cliente: TestClient, sesion: Session) -> None:
        """No es un 403: el cliente no tiene nada que aprobar, la peticion no
        aplica al estado del recurso."""
        _, cabeceras = _jefe(cliente, sesion)
        cliente_cuenta = _cuenta(sesion, "persona@ejemplo.com")

        respuesta = cliente.post(APROBAR.format(cliente_cuenta.id), headers=cabeceras)

        cuerpo = _problema(respuesta)
        assert respuesta.status_code == 409
        assert cuerpo["code"] == "invalid_state_transition"
        # El texto dice que el problema es el rol y no el estado, para que quien
        # administra sepa que el boton no va a funcionar por mucho que lo pulse.
        assert "proveedor" in cuerpo["detail"]
        assert _estado_en_base(sesion, cliente_cuenta.id) == UserStatus.ACTIVE.value

    def test_no_se_aprueba_un_cliente_pendiente(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """El rol se comprueba antes que el estado.

        Un cliente no nace pendiente, pero la fila puede existir. Si la
        comprobacion fuera solo de estado, aprobarla la pasaria a `active`
        -donde ya estaba de facto- y la operacion habria "tenido exito" sin
        haber hecho nada.
        """
        _, cabeceras = _jefe(cliente, sesion)
        raro = _cuenta(
            sesion, "raro@ejemplo.com", role=Role.CLIENT.value, status=UserStatus.PENDING.value,
        )

        respuesta = cliente.post(APROBAR.format(raro.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, raro.id) == UserStatus.PENDING.value

    def test_no_se_aprueba_dos_veces(self, cliente: TestClient, sesion: Session) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )
        cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        segunda = cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        assert _problema(segunda)["code"] == "invalid_state_transition"

    def test_no_se_aprueba_una_cuenta_suspendida(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        proveedor = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.SUSPENDED.value,
        )

        respuesta = cliente.post(APROBAR.format(proveedor.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, proveedor.id) == UserStatus.SUSPENDED.value

    def test_una_cuenta_inexistente_es_404(self, cliente: TestClient, sesion: Session) -> None:
        _, cabeceras = _jefe(cliente, sesion)

        respuesta = cliente.post(APROBAR.format(uuid.uuid4()), headers=cabeceras)

        assert _problema(respuesta)["code"] == "not_found"

    def test_un_proveedor_no_aprueba(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """GIVEN un proveedor autenticado / WHEN aprueba a otro / THEN se deniega
        y el estado del otro no cambia."""
        _cuenta(
            sesion, "uno@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.ACTIVE.value,
        )
        emitido = _entrar(cliente, "uno@ejemplo.com")
        objetivo = _cuenta(
            sesion, "otro@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        respuesta = cliente.post(APROBAR.format(objetivo.id), headers=_con(emitido))

        assert _problema(respuesta)["code"] == "forbidden"
        assert _estado_en_base(sesion, objetivo.id) == UserStatus.PENDING.value

    def test_un_cliente_no_suspende(self, cliente: TestClient, sesion: Session) -> None:
        _cuenta(sesion, "persona@ejemplo.com")
        emitido = _entrar(cliente, "persona@ejemplo.com")
        objetivo = _cuenta(
            sesion, "otro@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        respuesta = cliente.post(SUSPENDER.format(objetivo.id), headers=_con(emitido))

        assert _problema(respuesta)["code"] == "forbidden"
        assert _estado_en_base(sesion, objetivo.id) == UserStatus.PENDING.value


class TestSuspension:
    def test_suspender_cambia_el_estado(self, cliente: TestClient, sesion: Session) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com")

        respuesta = cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["status"] == "suspended"
        assert _estado_en_base(sesion, objetivo.id) == UserStatus.SUSPENDED.value

    def test_suspender_corta_las_sesiones_vivas(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """La revocacion va con la suspension, no despues.

        Sin ella, un token de acceso ya emitido seguiria sirviendo hasta que
        caducara: quince minutos de entrada para una cuenta que el administrador
        acaba de expulsar.
        """
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com")
        _entrar(cliente, "persona@ejemplo.com")
        _entrar(cliente, "persona@ejemplo.com")

        cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        sesion.expire_all()
        del_suspendido = [
            registro
            for registro in sesion.scalars(select(AuthSession))
            if registro.user_id == objetivo.id
        ]
        assert len(del_suspendido) == 2
        assert all(registro.revoked_at is not None for registro in del_suspendido)

    def test_el_suspendido_no_puede_renovar_ni_entrar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com")
        emitido = _entrar(cliente, "persona@ejemplo.com")
        cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        renovar = cliente.post(REFRESH, json={"refresh_token": emitido["refresh_token"]})
        entrar = cliente.post(LOGIN, json={"email": "persona@ejemplo.com", "password": CONTRASENA})

        assert _problema(renovar)["code"] == "session_revoked"
        assert _problema(entrar)["code"] == "invalid_credentials"

    def test_suspender_no_toca_las_sesiones_de_otros(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com")
        _entrar(cliente, "jefe@ejemplo.com")
        _entrar(cliente, "persona@ejemplo.com")

        cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        sesion.expire_all()
        del_jefe = [
            registro
            for registro in sesion.scalars(select(AuthSession))
            if registro.user_id != objetivo.id
        ]
        assert del_jefe and all(registro.revoked_at is None for registro in del_jefe)

    def test_no_se_suspende_dos_veces(self, cliente: TestClient, sesion: Session) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com")
        cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        segunda = cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        assert _problema(segunda)["code"] == "invalid_state_transition"

    def test_no_se_suspende_una_cuenta_dada_de_baja(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        _, cabeceras = _jefe(cliente, sesion)
        objetivo = _cuenta(sesion, "persona@ejemplo.com", status=UserStatus.DELETED.value)

        respuesta = cliente.post(SUSPENDER.format(objetivo.id), headers=cabeceras)

        assert _problema(respuesta)["code"] == "invalid_state_transition"
        assert _estado_en_base(sesion, objetivo.id) == UserStatus.DELETED.value

    def test_una_cuenta_inexistente_es_404(self, cliente: TestClient, sesion: Session) -> None:
        _, cabeceras = _jefe(cliente, sesion)

        respuesta = cliente.post(SUSPENDER.format(uuid.uuid4()), headers=cabeceras)

        assert _problema(respuesta)["code"] == "not_found"

    def test_sin_autenticar_no_suspende(self, cliente: TestClient, sesion: Session) -> None:
        objetivo = _cuenta(sesion, "persona@ejemplo.com")

        respuesta = cliente.post(SUSPENDER.format(objetivo.id))

        assert _problema(respuesta)["code"] == "unauthenticated"
        assert _estado_en_base(sesion, objetivo.id) == UserStatus.ACTIVE.value


class TestBitacora:
    def test_la_aprobacion_queda_con_la_identidad_del_administrador(
        self, cliente: TestClient, sesion: Session, captured_lines: Any
    ) -> None:
        jefe, cabeceras = _jefe(cliente, sesion)
        pendiente = _cuenta(
            sesion, "proveedor@ejemplo.com", role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        with captured_lines() as lineas:
            cliente.post(APROBAR.format(pendiente.id), headers=cabeceras)

        accion = [
            linea for linea in lineas if linea["status"] == 200 and "approve" in linea["route"]
        ]
        assert len(accion) == 1, lineas
        assert accion[0]["user_id"] == str(jefe.id)


class TestConsolaDeArranque:
    """`ticket_api.cli` es la unica via para crear el primer administrador.

    Sin ella, la administracion de 3.8 no tendria contra quien empezar y el
    primer despliegue terminaria editando la base a mano.
    """

    def test_crea_un_administrador_activo(self, sesion: Session) -> None:
        from ticket_api.cli import crear_admin
        from ticket_api.db.session import UnitOfWork

        unidad = UnitOfWork(sesion)
        usuario = crear_admin(unidad, "Jefe@Ejemplo.com", OTRA)

        assert usuario.role == Role.ADMIN.value
        assert usuario.status == UserStatus.ACTIVE.value
        assert usuario.email == "jefe@ejemplo.com"
        assert sesion.scalar(select(User).where(User.email == "jefe@ejemplo.com")) is not None

    def test_el_administrador_creado_puede_entrar(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        from ticket_api.cli import crear_admin
        from ticket_api.db.session import UnitOfWork

        crear_admin(UnitOfWork(sesion), "jefe@ejemplo.com", OTRA)

        respuesta = cliente.post(LOGIN, json={"email": "jefe@ejemplo.com", "password": OTRA})

        assert respuesta.status_code == 200, respuesta.text

    def test_no_se_puede_duplicar_un_correo_existente(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        from ticket_api.cli import crear_admin
        from ticket_api.db.session import UnitOfWork
        from ticket_api.errors.problem import ProblemException

        _cuenta(sesion, "persona@ejemplo.com")

        with pytest.raises(ProblemException) as fallo:
            crear_admin(UnitOfWork(sesion), "PERSONA@ejemplo.com", OTRA)

        assert fallo.value.code == "email_already_registered"
        sesion.rollback()
        sesion.expire_all()
        cuentas = sesion.scalars(select(User)).all()
        assert len(cuentas) == 1, "la consola no debe crear una segunda cuenta"
        assert cuentas[0].role == Role.CLIENT.value

    def test_una_contrasena_corta_no_crea_nada(self, sesion: Session) -> None:
        from ticket_api.cli import crear_admin
        from ticket_api.db.session import UnitOfWork

        with pytest.raises(SystemExit):
            crear_admin(UnitOfWork(sesion), "jefe@ejemplo.com", "corta")

        assert sesion.scalar(select(User).where(User.email == "jefe@ejemplo.com")) is None
