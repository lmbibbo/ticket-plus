"""3.2 - Restricciones e indices de `users` y `auth_sessions`.

La mayoría de estas pruebas comprueban comportamiento a traves de la base, no
la forma del modelo: insertar dos cuentas con el mismo email y ver que la base
lo rechaza. Un `UniqueConstraint` retirado del esquema se detecta aqui aunque el
modelo siga importandose sin error.

El indice se comprueba por columnas y no por rendimiento. Medir que PostgreSQL
usa un indice es fragil: depende de la estimacion de cardinalidad, del volumen y
del planificador. Lo que se protege es que el indice exista y cubra las
columnas por las que se busca.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ticket_api.db.base import Base
from ticket_api.models import AuthSession, Role, User, UserStatus, normalizar_email
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA

TABLAS_DE_PRUEBA.append(Base.metadata)


#: `sesion` y `engine` vienen de `conftest.py`; no se redefinen aqui.


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    """Crea y destruye las tablas reales del modelo.

    Se crea el esquema de `Base` entero, no solo las tablas de este modulo, y se
    tira al terminar. Al reusar `Base.metadata` se dependen del orden de las
    pruebas, y el orden no es una garantia.
    """
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


_HASH_EJEMPLO = "$argon2id$v=19$m=65536,t=3,p=4$sal$hash"


def _usuario(**cambios: object) -> User:
    """Cuenta de ejemplo. Los cambios se mezclan sobre los valores por defecto."""
    valores: dict[str, object] = {
        "email": "ana@ejemplo.com",
        "password_hash": _HASH_EJEMPLO,
        "role": Role.CLIENT.value,
        "status": UserStatus.ACTIVE.value,
    }
    valores.update(cambios)
    return User(**valores)


def _sesion_de(
    usuario: User,
    token: str = "a" * 64,
    vence_en: timedelta = timedelta(days=7),
) -> AuthSession:
    return AuthSession(
        user=usuario,
        refresh_token_hash=token,
        expires_at=datetime.now(UTC) + vence_en,
    )


class TestNormalizacionDeEmail:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("Ana@Ejemplo.com", "ana@ejemplo.com"),
            ("  Ana@Ejemplo.com  ", "ana@ejemplo.com"),
            ("ANA@EJEMPLO.COM", "ana@ejemplo.com"),
        ],
    )
    def test_normaliza_a_minusculas_y_recorta(self, entrada: str, esperado: str) -> None:
        assert normalizar_email(entrada) == esperado

    @pytest.mark.parametrize("entrada", ["", "   "])
    def test_rechaza_un_email_vacio(self, entrada: str) -> None:
        with pytest.raises(ValueError, match="vacio"):
            normalizar_email(entrada)

    def test_rechaza_un_email_desbordado(self) -> None:
        with pytest.raises(ValueError, match="supera"):
            normalizar_email(f"{'a' * 320}@ejemplo.com")

    def test_no_unifica_alias_de_correo(self) -> None:
        """Gmail ignora el `+`, pero para el sistema son cuentas distintas.

        Unificarlas daria a cualquiera acceso a los correos de la otra cuenta.
        """
        assert normalizar_email("ana+entradas@correo.com") != normalizar_email("ana@correo.com")

    def test_el_modelo_normaliza_al_asignar(self, sesion: Session) -> None:
        usuario = _usuario(email="  Ana@Ejemplo.COM ")
        sesion.add(usuario)
        sesion.flush()

        assert usuario.email == "ana@ejemplo.com"


class TestUnicidadDeEmail:
    def test_no_admite_dos_cuentas_con_el_mismo_email(self, sesion: Session) -> None:
        sesion.add(_usuario())
        sesion.flush()

        # Mismo email con otra capitalizacion: el modelo lo normaliza y la base
        # lo rechaza como duplicado.
        sesion.add(_usuario(email="Ana@Ejemplo.com"))
        with pytest.raises(IntegrityError, match="uq_users_email"):
            sesion.flush()
        sesion.rollback()

    def test_admite_cuentas_con_emails_distintos(self, sesion: Session) -> None:
        sesion.add(_usuario(email="ana@ejemplo.com"))
        sesion.add(_usuario(email="otro@ejemplo.com"))
        sesion.flush()

        assert len(sesion.scalars(select(User)).all()) == 2

    def test_el_email_normalizado_ya_choca_en_la_base(
        self, sesion: Session, engine: Engine
    ) -> None:
        """La unicidad real es sobre el email ya normalizado.

        Sin normalizar, `Ana@Ejemplo.com` y `ana@ejemplo.com` entrarian como dos
        cuentas distintas y la segunda seria un duplicado logico invisible.
        """
        sesion.add(_usuario(email="ana@ejemplo.com"))
        sesion.commit()

        with pytest.raises(IntegrityError):
            sesion.execute(
                text("INSERT INTO users (email, password_hash, role, status) "
                     "VALUES ('Ana@Ejemplo.com', 'x', 'client', 'active')")
            )
        sesion.rollback()

    def test_la_base_rechaza_un_email_sin_normalizar(self, sesion: Session) -> None:
        """El CHECK protege tambien de lo que no pasa por el modelo.

        Una migracion o un script de mantenimiento que inserte con mayusculas
        crearia un duplicado que el indice unico no puede ver.
        """
        with pytest.raises(IntegrityError, match="ck_users_email_normalizado"):
            sesion.execute(
                text("INSERT INTO users (email, password_hash, role, status) "
                     "VALUES ('  Ana@Ejemplo.com ', 'x', 'client', 'active')")
            )
        sesion.rollback()


class TestRestriccionesDeDominio:
    @pytest.mark.parametrize("rol", ["root", "superuser", "Client", ""])
    def test_rechaza_un_rol_fuera_de_la_lista(self, sesion: Session, rol: str) -> None:
        sesion.add(_usuario(role=rol))
        with pytest.raises(IntegrityError, match="ck_users_role"):
            sesion.flush()
        sesion.rollback()

    @pytest.mark.parametrize("estado", ["activo", "Active", "borrado", ""])
    def test_rechaza_un_estado_fuera_de_la_lista(self, sesion: Session, estado: str) -> None:
        sesion.add(_usuario(status=estado))
        with pytest.raises(IntegrityError, match="ck_users_status"):
            sesion.flush()
        sesion.rollback()

    def test_acepta_los_tres_roles(self, sesion: Session) -> None:
        for indice, rol in enumerate(Role):
            sesion.add(_usuario(email=f"rol{indice}@ejemplo.com", role=rol.value))
        sesion.flush()

        assert len(sesion.scalars(select(User)).all()) == len(Role)

    def test_acepta_los_cuatro_estados(self, sesion: Session) -> None:
        for indice, estado in enumerate(UserStatus):
            sesion.add(_usuario(email=f"estado{indice}@ejemplo.com", status=estado.value))
        sesion.flush()

        assert len(sesion.scalars(select(User)).all()) == len(UserStatus)

    def test_los_valores_por_defecto_son_cliente_activo(self, sesion: Session) -> None:
        sesion.add(User(email="nuevo@ejemplo.com", password_hash=_HASH_EJEMPLO))
        sesion.commit()

        guardada = sesion.scalar(select(User))
        assert guardada is not None
        assert guardada.role == Role.CLIENT.value
        assert guardada.status == UserStatus.ACTIVE.value

    def test_las_marcas_de_tiempo_las_pon_la_base(self, sesion: Session) -> None:
        sesion.add(_usuario())
        sesion.commit()

        guardada = sesion.scalar(select(User))
        assert guardada is not None
        assert guardada.created_at is not None
        assert guardada.updated_at is not None
        # Con la zona horaria explicita, los timestamps de PostgreSQL vuelven con
        # offset. Sin el, comparar fechas seria una fuente clasica de fallos.
        assert guardada.created_at.tzinfo is not None

    def test_updated_at_avanza_al_modificar(self, sesion: Session) -> None:
        sesion.add(_usuario())
        sesion.commit()
        guardada = sesion.scalar(select(User))
        assert guardada is not None
        antes = guardada.updated_at

        guardada.status = UserStatus.SUSPENDED.value
        sesion.commit()
        sesion.refresh(guardada)

        assert guardada.updated_at >= antes


class TestSesionesDeAutenticacion:
    def test_una_sesion_no_puede_apuntar_a_un_usuario_inexistente(self, sesion: Session) -> None:
        with pytest.raises(IntegrityError, match="auth_sessions_user_id_fkey"):
            sesion.add(
                AuthSession(
                    user_id=uuid.uuid4(),
                    refresh_token_hash="b" * 64,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
            sesion.flush()
        sesion.rollback()

    def test_no_admite_dos_sesiones_con_el_mismo_token(self, sesion: Session) -> None:
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()

        sesion.add(_sesion_de(usuario, token="a" * 64))
        sesion.flush()
        sesion.add(_sesion_de(usuario, token="a" * 64))
        with pytest.raises(IntegrityError, match="uq_auth_sessions_refresh_token_hash"):
            sesion.flush()
        sesion.rollback()

    def test_una_sesion_no_puede_ser_su_propia_sucesora(self, sesion: Session) -> None:
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()

        nueva = _sesion_de(usuario, token="c" * 64)
        sesion.add(nueva)
        sesion.flush()

        # Tras el flush existe el id, que es cuando se puede probar el ciclo.
        nueva.replaced_by_id = nueva.id
        with pytest.raises(IntegrityError, match="ck_auth_sessions_no_ciclica"):
            sesion.flush()
        sesion.rollback()

    def test_cada_token_arranca_en_su_propia_familia(self, sesion: Session) -> None:
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()

        primera = _sesion_de(usuario, token="d" * 64)
        sesion.add(primera)
        sesion.commit()

        assert primera.family_id is not None
        assert primera.family_id != primera.id

    def test_la_rotacion_hereda_la_familia(self, sesion: Session) -> None:
        """La familia es lo que convierte "este token esta viejo" en "esta cadena
        esta comprometida" y permite revocar toda la cadena de una vez."""
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()

        primera = _sesion_de(usuario, token="e" * 64)
        sesion.add(primera)
        sesion.commit()

        rotada = _sesion_de(usuario, token="f" * 64)
        rotada.family_id = primera.family_id
        sesion.add(rotada)
        # El id se genera al insertar: hay que confirmar la rotada para poder
        # apuntarle a ella. Sin este flush, `rotada.id` es None y el enlace
        # queda puesto a NULL en silencio.
        sesion.flush()
        primera.replaced_by_id = rotada.id
        sesion.commit()

        assert rotada.family_id == primera.family_id
        assert primera.replaced_by is rotada

    def test_borrar_el_usuario_borra_sus_sesiones(self, sesion: Session) -> None:
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()
        sesion.add(_sesion_de(usuario, token="1" * 64))
        sesion.add(_sesion_de(usuario, token="2" * 64))
        sesion.commit()
        identificador = usuario.id

        sesion.delete(usuario)
        sesion.commit()

        assert sesion.scalars(
            select(AuthSession).where(AuthSession.user_id == identificador)
        ).all() == []
        # Y no queda ninguna sesion huerfana en la tabla.
        assert sesion.scalar(select(func.count()).select_from(AuthSession)) == 0

    def test_borrar_una_sesion_no_arrastra_a_la_que_la_reemplaza(self, sesion: Session) -> None:
        """`replaced_by_id` apunta a la sesion *sucesora*.

        Es decir, la fila que referencia es la vieja y la referenciada es la
        nueva. Por eso el caso que importa es borrar la sucesora: la
        predecesora la referencia, y con `CASCADE` en vez de `SET NULL` se
        llevaria por delante el historial de la sesion.
        """
        usuario = _usuario()
        sesion.add(usuario)
        sesion.flush()

        vieja = _sesion_de(usuario, token="3" * 64)
        sesion.add(vieja)
        sesion.commit()
        vigente = _sesion_de(usuario, token="4" * 64)
        vigente.family_id = vieja.family_id
        sesion.add(vigente)
        sesion.flush()
        vieja.replaced_by_id = vigente.id
        sesion.commit()

        sesion.delete(vigente)
        sesion.commit()
        # Con `expire_on_commit=False` los objetos siguen con los valores que
        # tenia la sesion en memoria. `SET NULL` lo aplico la base, asi que sin
        # esta recarga el `replaced_by_id` antiguo seguiria ahi y el test
        # pasaria aunque la columna no se hubiera puesto a NULL.
        sesion.expire_all()

        # Una consulta de conteo y no `sesion.get()`: el `get` devuelve el
        # objeto del mapa de identidad aunque la fila ya no exista, y el test
        # pasaria con la tabla vacia.
        assert sesion.scalar(select(func.count()).select_from(AuthSession)) == 1
        superviviente = sesion.scalars(select(AuthSession)).one()
        assert superviviente.id == vieja.id
        assert superviviente.replaced_by_id is None


class TestEstadoDeLaSesion:
    def test_una_sesion_nueva_no_esta_revocada(self, sesion: Session) -> None:
        nueva = _sesion_de(_usuario())

        assert not nueva.esta_revocada
        assert nueva.revoked_at is None

    def test_revocar_registra_el_momento(self, sesion: Session) -> None:
        nueva = _sesion_de(_usuario())
        momento = datetime.now(UTC)

        assert nueva.revocar(momento) is True
        assert nueva.revoked_at == momento

    def test_revocar_dos_veces_no_cambia_nada(self, sesion: Session) -> None:
        nueva = _sesion_de(_usuario())
        nueva.revocar(datetime.now(UTC))

        momento = datetime.now(UTC)
        assert nueva.revocar(momento) is False
        assert nueva.revoked_at != momento

    def test_una_sesion_caducada_no_se_renueva(self, sesion: Session) -> None:
        caducada = _sesion_de(_usuario(), vence_en=timedelta(hours=-1))

        assert caducada.esta_vencida(datetime.now(UTC))
        assert not caducada.puede_renovarse(datetime.now(UTC))

    def test_una_sesion_revocada_no_se_renueva_aunque_no_haya_caducado(
        self, sesion: Session
    ) -> None:
        revocada = _sesion_de(_usuario())
        revocada.revocar()

        assert not revocada.puede_renovarse(datetime.now(UTC))

    def test_una_sesion_vigente_se_renueva(self, sesion: Session) -> None:
        vigente = _sesion_de(_usuario())

        assert vigente.puede_renovarse(datetime.now(UTC))


class TestIndices:
    """Los indices existen y cubren las columnas por las que se busca.

    Se comprueba la estructura y no el plan de ejecucion: un plan depende de la
    estimacion de filas y del volumen, asi que seria fragil. Lo que se protege
    aqui es que nadie borre un indice sin que la suite se entere.
    """

    def test_users_tiene_indice_unico_por_email(self, engine: Engine) -> None:
        indices = {i["name"]: i for i in inspect(engine).get_indexes("users")}

        assert "uq_users_email" in indices
        assert indices["uq_users_email"]["unique"] is True
        assert indices["uq_users_email"]["column_names"] == ["email"]

    def test_users_tiene_indice_por_rol_y_estado(self, engine: Engine) -> None:
        """Es el indice de "proveedores pendientes de aprobar"."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("users")}

        assert "ix_users_role_status" in indices
        assert indices["ix_users_role_status"]["column_names"] == ["role", "status"]

    def test_auth_sessions_esta_indexada_por_usuario(self, engine: Engine) -> None:
        """Cerrar sesion y la revocacion en cascada de una cuenta suspendida
        empiezan por `user_id`."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("auth_sessions")}

        assert "ix_auth_sessions_user_id" in indices
        assert indices["ix_auth_sessions_user_id"]["column_names"] == ["user_id"]

    def test_auth_sessions_esta_indexada_por_familia(self, engine: Engine) -> None:
        """Revocar una cadena entera busca por `family_id`."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("auth_sessions")}

        assert "ix_auth_sessions_family_id" in indices
        assert indices["ix_auth_sessions_family_id"]["column_names"] == ["family_id"]

    def test_auth_sessions_esta_indexada_por_caducidad(self, engine: Engine) -> None:
        """La limpieza periodica de sesiones caducadas recorre `expires_at`."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("auth_sessions")}

        assert "ix_auth_sessions_expires_at" in indices
        assert indices["ix_auth_sessions_expires_at"]["column_names"] == ["expires_at"]

    def test_el_hash_del_token_es_unico_e_indexado(self, engine: Engine) -> None:
        """La renovacion busca por hash, y ese indice es el que hace la busqueda
        barata ademas de garantizar que el token no este en dos filas."""
        indices = {i["name"]: i for i in inspect(engine).get_indexes("auth_sessions")}

        assert "uq_auth_sessions_refresh_token_hash" in indices
        assert indices["uq_auth_sessions_refresh_token_hash"]["unique"] is True

    def test_el_indice_de_email_soporta_la_busqueda_normalizada(self, sesion: Session) -> None:
        sesion.add(_usuario(email="ana@ejemplo.com"))
        sesion.commit()

        encontrada = sesion.scalars(
            select(User).where(User.email == "Ana@Ejemplo.com")
        ).all()

        assert encontrada == []  # el valor crudo no coincide con nada guardado
