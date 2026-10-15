"""3.5 - Tokens de acceso Ed25519 y refresh tokens con rotacion.

Lo que se comprueba aqui es la mecánica, no el HTTP: `login`, `refresh` y
`logout` como rutas llegan en 3.6. Las pruebas van contra el servicio y la base
de pruebas, porque lo que hay que demostrar -que la reutilizacion de un refresh
corta la cadena entera- no se puede observar desde fuera sin montar dos
procesos.

El reloj se inyecta en todas partes y ninguna prueba espera. Para comprobar que
algo caduca se emite en el pasado o se renueva en el futuro. Un `sleep` en una
suite de tokens la hace lenta y fragil a la vez, y ademas hace que el tiempo de
vida probado no sea el de la configuracion.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ticket_api.config import Settings
from ticket_api.db.base import Base
from ticket_api.db.session import UnitOfWork
from ticket_api.errors.codes import ErrorCode
from ticket_api.errors.problem import ProblemException
from ticket_api.models import AuthSession, Role, User, UserStatus
from ticket_api.security.tokens import (
    EmisorDeTokens,
    ErrorDeToken,
    TokenExpiradoError,
    hash_de_refresh,
    nuevo_refresh_token,
)
from ticket_api.services.sesiones import ServicioDeSesiones
from ticket_api.tests.conftest import TABLAS_DE_PRUEBA, clave_efimera

TABLAS_DE_PRUEBA.append(Base.metadata)

HASH_EJEMPLO = "$argon2id$v=19$m=65536,t=3,p=4$sal$hash"

#: Instante fijo de arranque. Todas las pruebas empiezan aqui, con la sesion de
#: pruebas ya en marcha y sin milisegundos de reloj real mezclados.
ORIGEN = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _decodificar_segmento(token: str, indice: int) -> dict[str, object]:
    """Lee un segmento base64url de un JWT. Solo para las pruebas.

    Se leen los claims a mano en vez de usar `verificar_acceso` porque dos
    pruebas necesitan ver lo que el token dice *antes* de que se valide: una
    que manipula el rol y otra que comprueba que el payload no lleva el correo.
    """
    segmento = token.split(".")[indice]
    relleno = "=" * (-len(segmento) % 4)
    decodificado: dict[str, object] = json.loads(base64.urlsafe_b64decode(segmento + relleno))
    return decodificado


def _firmar_sin_algoritmo(claims: dict[str, object]) -> str:
    """Token JWT con `alg: none`, es decir, sin firma."""
    cabecera = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    cuerpo = base64.urlsafe_b64encode(json.dumps(claims).encode())
    quitar = lambda parte: parte.decode().rstrip("=")  # noqa: E731
    return f"{quitar(cabecera)}.{quitar(cuerpo)}."


@dataclass
class RelojDePrueba:
    """Reloj que solo avanza cuando se le dice."""

    momento: datetime = ORIGEN

    def __call__(self) -> datetime:
        return self.momento

    def avanza(self, **kwargs: float) -> datetime:
        self.momento = self.momento + timedelta(**kwargs)
        return self.momento


@pytest.fixture(autouse=True)
def esquema(engine: Engine) -> Iterator[None]:
    Base.metadata.drop_all(engine, checkfirst=True)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine, checkfirst=True)


def _usuario(sesion: Session, **cambios: object) -> User:
    valores: dict[str, object] = {
        "email": "ana@ejemplo.com",
        "password_hash": HASH_EJEMPLO,
        "role": Role.CLIENT.value,
        "status": UserStatus.ACTIVE.value,
    }
    valores.update(cambios)
    usuario = User(**valores)
    sesion.add(usuario)
    sesion.commit()
    return usuario


@dataclass
class Escenario:
    """Reloj, usuario, servicio y base, ya montados para una prueba."""

    reloj: RelojDePrueba
    usuario: User
    servicio: ServicioDeSesiones
    sesion: Session
    ajustes: Settings
    _unidad: UnitOfWork = field(repr=False)

    @property
    def emisor(self) -> EmisorDeTokens:
        """Emisor con el reloj del escenario."""
        return EmisorDeTokens(self.ajustes, self.reloj)

    def emisor_en(self, momento: datetime) -> EmisorDeTokens:
        """Emisor con el reloj puesto en otro momento.

        Permite emitir un token ya caducado: se firma en el pasado y la
        verificacion, que compara contra la hora real, lo rechaza.
        """
        return EmisorDeTokens(self.ajustes, lambda: momento)

    def filas(self, **filtros: object) -> list[AuthSession]:
        consulta = select(AuthSession)
        for columna, valor in filtros.items():
            consulta = consulta.where(getattr(AuthSession, columna) == valor)
        return list(self.sesion.scalars(consulta).all())

    def fila(self, identificador: uuid.UUID) -> AuthSession:
        encontrada = self.sesion.get(AuthSession, identificador)
        assert encontrada is not None, f"no hay sesion {identificador}"
        return encontrada


@pytest.fixture
def escenario(sesion: Session, settings: Settings) -> Iterator[Escenario]:
    reloj = RelojDePrueba()
    usuario = _usuario(sesion)
    unidad = UnitOfWork(sesion)
    yield Escenario(
        reloj=reloj,
        usuario=usuario,
        servicio=ServicioDeSesiones(unidad, settings, reloj),
        sesion=sesion,
        ajustes=settings,
        _unidad=unidad,
    )


class TestFirmaDelTokenDeAcceso:
    def test_se_firma_con_ed25519(self, escenario: Escenario) -> None:
        """La cabecera declara `EdDSA` y no `HS256`.

        Con la misma clave, un token firmado con HMAC se validaria contra una
        clave publica, porque alli el secreto es precisamente la clave. Fijar
        la lista de algoritmos -y no leer el de la cabecera- es lo que lo evita.
        """
        token, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        assert _decodificar_segmento(token, 0)["alg"] == "EdDSA"

    def test_los_claims_son_los_que_pide_el_diseno(self, escenario: Escenario) -> None:
        sesion_id = uuid.uuid4()

        token, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", sesion_id)
        claims = escenario.emisor.verificar_acceso(token)

        assert claims.usuario_id == escenario.usuario.id
        assert claims.sesion_id == sesion_id
        assert claims.rol == "client"
        assert claims.jti

    def test_el_payload_es_exactamente_el_del_diseno(self, escenario: Escenario) -> None:
        """Ni una clave mas ni una menos.

        Se comprueba el conjunto entero porque un claim anadido a la buena
        volunta -un `email`, un `iss` que nadie lee- viaja en cada peticion y
        acaba en la bitacora de un tercero.
        """
        token, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        assert set(_decodificar_segmento(token, 1)) == {"sub", "role", "sid", "iat", "exp", "jti"}

    def test_el_correo_no_aparece_en_el_token(self, escenario: Escenario) -> None:
        token, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        assert escenario.usuario.email not in token

    def test_cada_token_lleva_un_identificador_distinto(self, escenario: Escenario) -> None:
        """El `jti` es lo que permite distinguir dos emisiones identicas -mismo
        usuario, misma sesion, mismo instante- al revisar una bitacora."""
        primero, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())
        segundo, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        assert escenario.emisor.verificar_acceso(primero).jti != (
            escenario.emisor.verificar_acceso(segundo).jti
        )

    def test_la_vida_del_acceso_sale_de_la_configuracion(self, escenario: Escenario) -> None:
        _, expira = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        assert expira - ORIGEN == timedelta(seconds=escenario.ajustes.access_token_ttl_seconds)

    def test_el_reloj_del_servicio_es_el_de_la_prueba(self, escenario: Escenario) -> None:
        """Sin esto, un token emitido por el servicio con el reloj real y
        verificado con el de la prueba pareceria caducado."""
        emitida = escenario.servicio.abrir(escenario.usuario)

        claims = escenario.emisor.verificar_acceso(emitida.acceso)

        assert claims.emitido_en == ORIGEN


class TestTokensQueNoSeAceptan:
    def test_un_token_firmado_con_otra_clave_no_valida(self, escenario: Escenario) -> None:
        """GIVEN un atacante con su propia clave / WHEN firma un token /
        THEN la API no lo acepta."""
        token, _ = escenario.emisor.firmar_acceso(escenario.usuario.id, "admin", uuid.uuid4())
        impostor = EmisorDeTokens(
            escenario.ajustes.model_copy(update={"jwt_private_key_b64": clave_efimera()}),
            escenario.reloj,
        )

        with pytest.raises(ErrorDeToken):
            impostor.verificar_acceso(token)

    def test_un_token_con_el_rol_manipulado_no_valida(self, escenario: Escenario) -> None:
        """El rol viaja dentro del token, asi que es el objetivo natural.

        Cambiar `client` por `admin` rompe la firma. Por eso en 3.7 el rol de la
        peticion no se deduce del token, sino de la sesion.
        """
        emisor = escenario.emisor
        token, _ = emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())
        cabecera, _, firma = token.split(".")

        claims = _decodificar_segmento(token, 1)
        claims["role"] = "admin"
        cuerpo = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")

        with pytest.raises(ErrorDeToken):
            emisor.verificar_acceso(f"{cabecera}.{cuerpo}.{firma}")

    def test_un_token_sin_firma_no_valida(self, escenario: Escenario) -> None:
        """`alg: none` es el ataque clasico: un token que declara no llevar
        firma y ademas dice ser administrador."""
        token = _firmar_sin_algoritmo(
            {
                "sub": str(escenario.usuario.id),
                "role": "admin",
                "sid": str(uuid.uuid4()),
                "iat": int(ORIGEN.timestamp()),
                "exp": int((ORIGEN + timedelta(hours=1)).timestamp()),
                "jti": "sin-firma",
            }
        )

        with pytest.raises(ErrorDeToken):
            escenario.emisor.verificar_acceso(token)

    def test_un_token_sin_las_claims_obligatorias_no_valida(self, escenario: Escenario) -> None:
        """Un token sin `sid` no se puede contrastar con una sesion, y sin `exp`
        no caduca: aceptarlo seria aceptar algo valido para siempre."""
        clave = _clave_de_prueba(escenario.ajustes)
        ahora = int(ORIGEN.timestamp())
        token = jwt.encode(
            {"sub": str(escenario.usuario.id), "iat": ahora, "exp": ahora + 600},
            clave,
            algorithm="EdDSA",
        )

        with pytest.raises(ErrorDeToken):
            escenario.emisor.verificar_acceso(token)

    def test_una_texto_que_no_es_un_token_no_valida(self, escenario: Escenario) -> None:
        with pytest.raises(ErrorDeToken):
            escenario.emisor.verificar_acceso("esto-no-es-un-jwt")


def _clave_de_prueba(ajustes: Settings) -> Any:
    """Clave privada de la configuracion de la prueba.

    Se expone para poder firmar a mano un token mal formado. No se usa
    `EmisorDeTokens` porque ese firma siempre las seis claims, que es
    justamente lo que la prueba necesita saltarse.
    """
    from cryptography.hazmat.primitives import serialization

    crudo = base64.b64decode(ajustes.jwt_private_key_b64)
    return serialization.load_der_private_key(crudo, password=None)


class TestCaducidad:
    def test_el_token_de_acesso_caduca(self, escenario: Escenario) -> None:
        """Se firma con la vida de la configuracion en el pasado, y al
        verificarlo ya no sirve."""
        vencido = escenario.emisor_en(
            ORIGEN - timedelta(seconds=escenario.ajustes.access_token_ttl_seconds + 1)
        )
        token, _ = vencido.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        with pytest.raises(TokenExpiradoError):
            escenario.emisor.verificar_acceso(token)

    def test_un_token_que_todavia_no_caduca_se_verifica(self, escenario: Escenario) -> None:
        token, expira = escenario.emisor.firmar_acceso(escenario.usuario.id, "client", uuid.uuid4())

        claims = escenario.emisor.verificar_acceso(token)

        assert claims.expira_en == expira

    def test_el_refresh_caduca_y_no_renueva(self, escenario: Escenario) -> None:
        """GIVEN un refresh vencido / WHEN se renueva / THEN no hay token nuevo
        y el error es de sesion, no de credencial.

        La distincion no es academica: una caducidad se resuelve entrando otra
        vez, y el codigo de error decide si la web guarda lo que tiene.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.reloj.avanza(seconds=escenario.ajustes.refresh_token_ttl_seconds + 1)

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(emitida.refresh)

        assert fallo.value.code == ErrorCode.SESSION_REVOKED
        assert fallo.value.status == 401


class TestAperturaDeSesion:
    def test_guarda_solo_el_hash_del_refresh(self, escenario: Escenario) -> None:
        """La base no sirve para suplantar a nadie aunque este expuesta."""
        emitida = escenario.servicio.abrir(escenario.usuario)

        filas = escenario.filas()
        assert len(filas) == 1
        assert filas[0].refresh_token_hash == hash_de_refresh(emitida.refresh)
        assert emitida.refresh not in filas[0].refresh_token_hash

    def test_cada_sesion_nace_en_su_propia_familia(self, escenario: Escenario) -> None:
        """Dos entradas al mismo usuario no comparten familia.

        Si la compartieran, reutilizar el refresh de una cerraria la otra, y el
        usuario tendria que volver a entrar sin saber por que.
        """
        primera = escenario.servicio.abrir(escenario.usuario)
        segunda = escenario.servicio.abrir(escenario.usuario)

        assert primera.sesion_id != segunda.sesion_id
        familias = {fila.family_id for fila in escenario.filas()}
        assert len(familias) == 2

    def test_una_cuenta_no_activa_no_abre_sesion(self, escenario: Escenario) -> None:
        """GIVEN un proveedor pendiente con la contrasena correcta / WHEN
        intenta entrar / THEN no emite tokens.

        Es el mismo camino que usara 3.6 en `login`: por eso la comprobacion
        vive en el servicio y no en la vista.
        """
        proveedor = _usuario(
            escenario.sesion,
            email="proveedor@ejemplo.com",
            role=Role.EVENT_OWNER.value,
            status=UserStatus.PENDING.value,
        )

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.abrir(proveedor)

        assert fallo.value.code == ErrorCode.ACCOUNT_NOT_ACTIVE
        assert escenario.filas(user_id=proveedor.id) == []

    def test_una_cuenta_suspendida_no_abre_sesion(self, escenario: Escenario) -> None:
        suspendida = _usuario(
            escenario.sesion, email="bruno@ejemplo.com", status=UserStatus.SUSPENDED.value
        )

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.abrir(suspendida)

        assert fallo.value.code == ErrorCode.ACCOUNT_NOT_ACTIVE

    def test_abrir_confirma_y_el_token_sirve(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)

        claims = escenario.emisor.verificar_acceso(emitida.acceso)

        assert claims.sesion_id == emitida.sesion_id
        assert claims.usuario_id == escenario.usuario.id


class TestRotacion:
    def test_renovar_emite_un_token_nuevo(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)

        renovada = escenario.servicio.renovar(emitida.refresh)

        assert renovada.acceso != emitida.acceso
        assert renovada.refresh != emitida.refresh

    def test_renovar_mantiene_la_misma_cadena(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)
        familia_original = escenario.fila(emitida.sesion_id).family_id

        renovada = escenario.servicio.renovar(emitida.refresh)

        assert renovada.sesion_id != emitida.sesion_id
        assert {fila.family_id for fila in escenario.filas()} == {familia_original}

    def test_el_anterior_queda_reemplazado_y_revocado(self, escenario: Escenario) -> None:
        """El vinculo es lo que despues permite distinguir "cerre de sesion" de
        "reutilizacion". Sin el, los dos casos serían el mismo 401."""
        emitida = escenario.servicio.abrir(escenario.usuario)

        renovada = escenario.servicio.renovar(emitida.refresh)

        anterior = escenario.fila(emitida.sesion_id)
        assert anterior.esta_revocada
        assert anterior.replaced_by_id == renovada.sesion_id

    def test_la_renovacion_amplia_la_vida(self, escenario: Escenario) -> None:
        """La vida corre desde el ultimo uso y no desde el primero.

        Con una vida fija desde el alta, un usuario que entra a diario tendria
        que volver a autenticarse cada dos semanas.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.reloj.avanza(days=3)

        renovada = escenario.servicio.renovar(emitida.refresh)

        assert renovada.refresh_expira_en > emitida.refresh_expira_en

    def test_una_cadena_larga_sigue_siendo_una_cadena(self, escenario: Escenario) -> None:
        actual = escenario.servicio.abrir(escenario.usuario).refresh
        for _ in range(5):
            actual = escenario.servicio.renovar(actual).refresh

        filas = escenario.filas()
        assert len(filas) == 6
        assert len({fila.family_id for fila in filas}) == 1

    def test_renovar_no_emite_si_la_cuenta_se_suspendio(self, escenario: Escenario) -> None:
        """GIVEN una sesion abierta y la cuenta suspendida / WHEN se renueva /
        THEN no hay token nuevo y la cadena queda cerrada.

        Sin el cierre, quien tenga un refresh valido podria reintentar con el
        siguiente eslabon de la misma familia.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.usuario.status = UserStatus.SUSPENDED.value
        escenario.sesion.commit()

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(emitida.refresh)

        assert fallo.value.code == ErrorCode.ACCOUNT_NOT_ACTIVE
        assert all(fila.esta_revocada for fila in escenario.filas())

    def test_renovar_no_emite_si_la_cuenta_se_dio_de_baja(self, escenario: Escenario) -> None:
        """La baja es logica: la fila se queda.

        Eliminar la fila de verdad llevaria consigo sus sesiones por `CASCADE` y
        el refresh no encontraria nada que revisar. Que la sesion se revoque
        explicitamente es lo que permite que el usuario reciba un codigo de
        "cuenta no activa" y no uno de "refresh desconocido".
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.usuario.status = UserStatus.DELETED.value
        escenario.sesion.commit()

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(emitida.refresh)

        assert fallo.value.code == ErrorCode.ACCOUNT_NOT_ACTIVE


class TestRevocacion:
    def test_revocar_cierra_la_sesion(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)

        assert escenario.servicio.revocar(emitida.refresh) is True

        assert escenario.fila(emitida.sesion_id).esta_revocada

    def test_tras_revocar_no_se_renueva(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.servicio.revocar(emitida.refresh)

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(emitida.refresh)

        assert fallo.value.code == ErrorCode.SESSION_REVOKED

    def test_revocar_dos_veces_no_es_un_error(self, escenario: Escenario) -> None:
        """El segundo cierre devuelve "no habia nada" y no un 401.

        El navegador cierra sesion en cada visita a un enlace de salida, y un
        error ahi dejaria al usuario creyendo que sigue dentro.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)

        assert escenario.servicio.revocar(emitida.refresh) is True
        assert escenario.servicio.revocar(emitida.refresh) is False

    def test_revocar_un_refresh_que_no_existe_no_falla(self, escenario: Escenario) -> None:
        assert escenario.servicio.revocar(nuevo_refresh_token()) is False


class TestReutilizacion:
    def test_reutilizar_un_token_ya_rotado_se_detecta(self, escenario: Escenario) -> None:
        """GIVEN un refresh que ya se renovo / WHEN se vuelve a usar / THEN se
        rechaza con un codigo propio y no con un 401 generico.

        El codigo propio es lo que permite a la web cerrar la sesion local:
        `invalid_credentials` no dice que el token este comprometido.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.servicio.renovar(emitida.refresh)

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(emitida.refresh)

        assert fallo.value.code == ErrorCode.REFRESH_TOKEN_REUSED
        assert fallo.value.status == 401

    def test_la_reutilizacion_corta_la_cadena_entera(self, escenario: Escenario) -> None:
        """El token mas nuevo tampoco deja de servir, y esa es la parte
        incomoda.

        Cuando aparece una reutilizacion no se sabe cual de los dos en conflicto
        es el atacante, asi que la respuesta no puede ser "este estaba mal". Cortar
        la cadena expulsa a los dos, y el legitimo vuelve a entrar.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)
        renovada = escenario.servicio.renovar(emitida.refresh)

        with pytest.raises(ProblemException):
            escenario.servicio.renovar(emitida.refresh)

        # El token nuevo se rechaza como sesion revocada y no como
        # reutilizacion: nobody lo ha reutilizado, lo han revocado. El codigo
        # dice lo que le paso a cada token, y la web los trata igual -cerrar la
        # sesion local- porque en los dos casos el refresh ya no sirve.
        with pytest.raises(ProblemException) as segundo:
            escenario.servicio.renovar(renovada.refresh)
        assert segundo.value.code == ErrorCode.SESSION_REVOKED

    def test_todos_los_eslabones_quedan_revocados(self, escenario: Escenario) -> None:
        emitida = escenario.servicio.abrir(escenario.usuario)
        escenario.servicio.renovar(emitida.refresh)

        with pytest.raises(ProblemException):
            escenario.servicio.renovar(emitida.refresh)

        filas = escenario.filas()
        assert len(filas) == 2
        assert all(fila.esta_revocada for fila in filas)

    def test_no_toca_las_sesiones_de_otra_cadena(self, escenario: Escenario) -> None:
        """GIVEN dos entradas independientes del mismo usuario / WHEN se
        reutiliza un refresh de la primera / THEN la segunda sigue viva.

        Sin el filtro por familia, un solo token robado seria un cierre de
        cuenta.
        """
        primera = escenario.servicio.abrir(escenario.usuario)
        segunda = escenario.servicio.abrir(escenario.usuario)
        renovada = escenario.servicio.renovar(primera.refresh)

        with pytest.raises(ProblemException):
            escenario.servicio.renovar(primera.refresh)

        assert not escenario.fila(segunda.sesion_id).esta_revocada
        assert escenario.servicio.renovar(segunda.refresh).sesion_id != renovada.sesion_id

    def test_un_refresh_inventado_no_corta_ninguna_cadena(self, escenario: Escenario) -> None:
        """Un token que no existe no puede cerrar sesiones ajenas.

        Es la razon de que la revocacion por familia solo ocurra tras encontrar
        una fila real: sin esa fila no hay familia que cortar.
        """
        emitida = escenario.servicio.abrir(escenario.usuario)

        with pytest.raises(ProblemException) as fallo:
            escenario.servicio.renovar(nuevo_refresh_token())

        assert fallo.value.code == ErrorCode.INVALID_CREDENTIALS
        assert not escenario.fila(emitida.sesion_id).esta_revocada
