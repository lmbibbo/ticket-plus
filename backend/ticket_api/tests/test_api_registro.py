"""3.4 - Registro publico, normalizacion de email y hash Argon2id.

Las tres cosas que la especificacion pide se comprueban por su efecto, no por su
codigo: una cuenta queda `active` o `pending` segun el rol, un rol no
registrable no produce ninguna cuenta, y el correo se guarda normalizado. Que el
hash sea de Argon2id y no un texto plano se comprueba leyendo lo que hay en la
columna, porque un test que solo llame a `hash_password` no detectaria que
alguien laemplace por `str`.

Cada prueba usa la API real contra la base de pruebas. Se sustituye
`get_sessionmaker`, el unico punto por el que la peticion saca su sesion, para no
duplicar aqui la frontera de transaccion que ya prueba 3.1.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from ticket_api.db.base import Base
from ticket_api.errors.problem import PROBLEM_CONTENT_TYPE
from ticket_api.models import Role, User, UserStatus
from ticket_api.security.passwords import verify_password
from ticket_api.services import registro
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)

#: Cumple el minimo de longitud sin repetirla en cada prueba. Es una contrasena
#: de ejemplo, no una credencial: no se usa en ningun entorno real.
CONTRASENA = "contrasena-de-prueba-1"

RUTA = "/api/v1/auth/register"


def _cuerpo(email: str = "ana@ejemplo.com", **cambios: object) -> dict[str, object]:
    valores: dict[str, object] = {"email": email, "password": CONTRASENA, "role": "client"}
    valores.update(cambios)
    return valores


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Esquema real antes y despues de cada prueba.

    Estas pruebas confirman de verdad, asi que la transaccion que las envuelve no
    deshace nada: el aislamiento tiene que venir de tirar las tablas, no de un
    `rollback` que ya no deshace lo confirmado.
    """
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


@pytest.fixture
def cliente(
    app: object,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """Cliente HTTP contra la API con el motor de pruebas detras."""
    monkeypatch.setattr("ticket_api.db.session.get_sessionmaker", lambda: session_factory)
    with TestClient(app, raise_server_exceptions=False) as instancia:  # type: ignore[arg-type]
        yield instancia


def _unica_cuenta(sesion: Session) -> User:
    cuentas = sesion.scalars(select(User)).all()
    assert len(cuentas) == 1, f"se esperaba una cuenta y hay {len(cuentas)}"
    return cuentas[0]


class TestRegistroDeCliente:
    def test_una_cuenta_de_cliente_nace_activa(self, cliente: TestClient) -> None:
        respuesta = cliente.post(RUTA, json=_cuerpo(role="client"))

        assert respuesta.status_code == 201, respuesta.text
        cuerpo = respuesta.json()
        assert cuerpo["role"] == "client"
        assert cuerpo["status"] == "active"

    def test_la_cuenta_queda_en_la_base(self, cliente: TestClient, sesion: Session) -> None:
        cliente.post(RUTA, json=_cuerpo(role="client"))

        guardada = _unica_cuenta(sesion)
        assert guardada.status == UserStatus.ACTIVE.value
        assert guardada.role == Role.CLIENT.value

    def test_la_respuesta_no_devuelve_el_hash(self, cliente: TestClient) -> None:
        respuesta = cliente.post(RUTA, json=_cuerpo())

        assert "password" not in respuesta.text
        assert "password_hash" not in respuesta.text


class TestRegistroDeProveedor:
    def test_una_cuenta_de_proveedor_nace_pendiente(self, cliente: TestClient) -> None:
        """GIVEN un visitante que elige `event-owner` / WHEN se registra /
        THEN la cuenta existe pero no esta activa.

        Nace `pending` y no `active` porque publicar eventos es una capacidad
        que concede un administrador, no el propio registro.
        """
        respuesta = cliente.post(
            RUTA, json=_cuerpo(email="proveedor@ejemplo.com", role="event-owner")
        )

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["status"] == "pending"

    def test_el_rol_es_el_de_proveedor(self, cliente: TestClient, sesion: Session) -> None:
        cliente.post(RUTA, json=_cuerpo(email="proveedor@ejemplo.com", role="event-owner"))

        guardada = _unica_cuenta(sesion)
        assert guardada.role == Role.EVENT_OWNER.value
        assert guardada.status == UserStatus.PENDING.value


class TestRolesNoRegistrables:
    @pytest.mark.parametrize("rol", ["admin", "administrator", "root", "ADMIN", ""])
    def test_un_rol_no_registrable_no_crea_cuenta(
        self, cliente: TestClient, sesion: Session, rol: str
    ) -> None:
        """GIVEN un visitante que pide un rol fuera del registro publico /
        WHEN envia el formulario / THEN no se crea ninguna cuenta.

        Se comprueba tambien que la base queda vacia: un 422 con la cuenta
        guardada seria el fallo dangerouso, porque el usuario veria un error y la
        cuenta existiera.
        """
        respuesta = cliente.post(RUTA, json=_cuerpo(role=rol))

        assert respuesta.status_code == 422, respuesta.text
        assert respuesta.json()["code"] == "validation_error"
        assert sesion.scalars(select(User)).all() == []

    def test_el_error_esta_anclado_al_campo_rol(self, cliente: TestClient) -> None:
        """El formulario necesita saber que campo marcar, no solo que hay un error."""
        cuerpo = cliente.post(RUTA, json=_cuerpo(role="admin")).json()

        campos = [error["field"] for error in cuerpo["errors"]]
        assert campos == ["role"]

    def test_el_rol_por_defecto_es_cliente(self, cliente: TestClient) -> None:
        """Un formulario que no manda rol registra un cliente, no una cuenta sin rol."""
        respuesta = cliente.post(RUTA, json={"email": "ana@ejemplo.com", "password": CONTRASENA})

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["role"] == "client"


class TestNormalizacionDelEmail:
    @pytest.mark.parametrize(
        "enviado",
        ["Ana@Ejemplo.com", "  ANA@ejemplo.COM  ", "ana@EJEMPLO.com"],
    )
    def test_se_guarda_normalizado(
        self, cliente: TestClient, sesion: Session, enviado: str
    ) -> None:
        respuesta = cliente.post(RUTA, json=_cuerpo(email=enviado))

        assert respuesta.status_code == 201, respuesta.text
        assert respuesta.json()["email"] == "ana@ejemplo.com"
        assert _unica_cuenta(sesion).email == "ana@ejemplo.com"

    def test_el_mismo_correo_con_otra_capitalizacion_es_duplicado(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Sin normalizar, estas dos cuentas serian distintas y el indice unico
        no las veria como el mismo correo."""
        cliente.post(RUTA, json=_cuerpo(email="ana@ejemplo.com"))

        respuesta = cliente.post(RUTA, json=_cuerpo(email="Ana@Ejemplo.COM"))

        assert respuesta.status_code == 409, respuesta.text
        assert len(sesion.scalars(select(User)).all()) == 1


class TestHashDeContrasena:
    def test_la_contrasena_no_se_guarda_en_claro(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cliente.post(RUTA, json=_cuerpo())

        assert CONTRASENA not in _unica_cuenta(sesion).password_hash

    def test_lo_guardado_es_un_hash_argon2id_verificable(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Comprueba el algoritmo leyendo la columna, no llamando a la funcion.

        Es la unica forma de detectar que alguien sustituyo `hash_password` por
        un `str(...)`: el resto de las pruebas seguirian pasando.
        """
        cliente.post(RUTA, json=_cuerpo())

        guardado = _unica_cuenta(sesion).password_hash
        assert guardado.startswith("$argon2id$")
        assert verify_password(guardado, CONTRASENA)

    def test_una_contrasena_distinta_no_verifica(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        cliente.post(RUTA, json=_cuerpo())

        assert not verify_password(_unica_cuenta(sesion).password_hash, "otra-contrasena-1")

    def test_dos_cuentas_con_la_misma_contrasena_no_coinciden(
        self, cliente: TestClient, sesion: Session
    ) -> None:
        """Sin sal, dos cuentas con la misma contrasena darian el mismo hash, y
        una tabla de hashes delaria que dos personas eligieron lo mismo."""
        cliente.post(RUTA, json=_cuerpo(email="ana@ejemplo.com"))
        cliente.post(RUTA, json=_cuerpo(email="bruno@ejemplo.com"))

        hashes = {cuenta.password_hash for cuenta in sesion.scalars(select(User)).all()}
        assert len(hashes) == 2

    @pytest.mark.parametrize(
        "cuerpo",
        [
            {"email": "ana@ejemplo.com", "password": "corta"},
            {"email": "ana@ejemplo.com"},
            {"email": "no-es-un-correo", "password": CONTRASENA},
            {"email": "ana@ejemplo.com", "password": CONTRASENA, "invitado": "sorpresa"},
        ],
        ids=["corta", "sin-password", "correo-invalido", "campo-de-mas"],
    )
    def test_una_contrasena_invalida_no_crea_cuenta(
        self, cliente: TestClient, sesion: Session, cuerpo: dict[str, object]
    ) -> None:
        respuesta = cliente.post(RUTA, json=cuerpo)

        assert respuesta.status_code == 422, respuesta.text
        assert sesion.scalars(select(User)).all() == []


class TestCorreoDuplicado:
    def test_devuelve_409_con_el_codigo_estable(self, cliente: TestClient) -> None:
        cliente.post(RUTA, json=_cuerpo())

        respuesta = cliente.post(RUTA, json=_cuerpo())

        assert respuesta.status_code == 409
        assert respuesta.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
        cuerpo = respuesta.json()
        assert cuerpo["code"] == "email_already_registered"
        assert cuerpo["type"].endswith("/email_already_registered")

    def test_el_error_esta_anclado_al_campo_email(self, cliente: TestClient) -> None:
        """Es lo que la demo de F2 pide: un error de campo en el formulario, no
        un 500 ni un mensaje suelto."""
        cliente.post(RUTA, json=_cuerpo())

        cuerpo = cliente.post(RUTA, json=_cuerpo()).json()

        errores = cuerpo["errors"]
        assert [error["field"] for error in errores] == ["email"]
        assert "password" not in cuerpo["detail"].lower()

    def test_no_confirma_una_cuenta_parcial(self, cliente: TestClient, sesion: Session) -> None:
        """Tras el conflicto la peticion sigue viva: si la vista se comiera la
        sesion, la siguiente peticion de la misma conexion fallaria."""
        cliente.post(RUTA, json=_cuerpo())
        cliente.post(RUTA, json=_cuerpo())

        otra = cliente.post(RUTA, json=_cuerpo(email="bruno@ejemplo.com"))
        assert otra.status_code == 201
        assert len(sesion.scalars(select(User)).all()) == 2

    def test_otro_registro_que_entra_entre_la_busqueda_y_el_insert_da_409(
        self, cliente: TestClient, sesion: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La carrera que la busqueda previa no puede cerrar.

        Dos peticiones con el mismo correo que llegan a `INSERT` a la vez pasan
        las dos la busqueda, y solo `uq_users_email` puede separar. Aqui se anula
        la busqueda para llegar a esa rama: es el unico modo de probarla en
        serie, y es justo la rama que sin ella devolveria un 500 en el pico de
        trafico de un alta masiva.
        """
        cliente.post(RUTA, json=_cuerpo())
        monkeypatch.setattr(registro, "_email_ya_registrado", lambda *_: False)

        respuesta = cliente.post(RUTA, json=_cuerpo())

        assert respuesta.status_code == 409, respuesta.text
        assert respuesta.json()["code"] == "email_already_registered"
        assert len(sesion.scalars(select(User)).all()) == 1

    def test_tras_la_carrera_la_sesion_sigue_sirviendo(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """La transaccion queda muerta tras un `IntegrityError`.

        Sin el `rollback` que hace el servicio, la siguiente peticion falla con
        "current transaction is aborted" y el cliente ve un 500 por un registro
        que si era valido.
        """
        cliente.post(RUTA, json=_cuerpo())
        monkeypatch.setattr(registro, "_email_ya_registrado", lambda *_: False)
        cliente.post(RUTA, json=_cuerpo())

        assert cliente.post(RUTA, json=_cuerpo(email="bruno@ejemplo.com")).status_code == 201

    def test_un_integrity_error_que_no_es_el_correo_no_se_disfraza(
        self, cliente: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Un fallo de integridad distinto del correo unico tiene que llegar
        como 500. Vestirlo de "correo ya registrado" esconde el defecto y hace
        que el usuario reintente sin causa posible.
        """
        monkeypatch.setattr(registro, "_es_conflicto_de_email", lambda _: False)

        cliente.post(RUTA, json=_cuerpo())
        monkeypatch.setattr(registro, "_email_ya_registrado", lambda *_: False)
        respuesta = cliente.post(RUTA, json=_cuerpo())

        assert respuesta.status_code == 500
        assert respuesta.json()["code"] == "internal_error"


class TestContratoDeLaRuta:
    def test_la_ruta_es_parte_del_contrato_publico(self, cliente: TestClient) -> None:
        """GIVEN el despliegue / WHEN se consulta el esquema / THEN el endpoint
        de registro esta declarado bajo `/api/v1`.

        Es lo que fija 3.6 para login, refresh y logout: si la ruta se moviera,
        Flask -que la llama por nombre- se enteraria por un 404 en produccion.
        """
        esquema = cliente.get("/api/openapi.json").json()

        assert "/api/v1/auth/register" in esquema["paths"]
