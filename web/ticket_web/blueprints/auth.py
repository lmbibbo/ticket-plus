"""Blueprint de autenticacion: acceso, alta, cierre de sesion y renovacion.

Los formularios de GET y los de POST son vistas distintas sobre la misma ruta y
con nombres distintos, y no un `if request.method` dentro de una sola vista: cada
una tiene su contrato -una pagina o un formulario con errores- y mezclarlas
obliga a que las dos mitades se lean juntas para entender cualquiera.

Las tres operaciones que cambian estado -alta, login y cierre- exigen token
CSRF, pero no lo comprueban aqui: la comprobacion es de toda la aplicacion y
vive en `ticket_web.csrf.registrar_csrf`, instalada en `before_request`. Una
vista que olvidara su comprobador propio seria una vulnerabilidad silenciosa, y
la guarda central es lo que hace que una ruta nueva de 7.2 o 7.3 no pueda
saltarsela.
"""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for
from werkzeug.wrappers import Response as WerkzeugResponse

from ticket_web.api_client import ApiProblemError
from ticket_web.auth import exigir_rol, identidad_actual, rol_actual
from ticket_web.observability.middleware import set_problem_code
from ticket_web.sesion_api import (
    CredencialesRechazadas,
    abrir_sesion,
    cerrar_sesion,
    destino_por_rol,
    registrar,
)

#: Lo que Flask acepta como valor de retorno de una vista. Se usa la clase de
#: Werkzeug porque es la que devuelven tanto `redirect` como `jsonify`.
ResponseReturn = WerkzeugResponse | str | tuple[str, int]

#: Unico aviso de credenciales que se muestra. El API responde igual a un correo
#: inexistente, a una contrasena mala y a una cuenta sin aprobar, y cualquier
#: texto mas especifico devolveria el login a ser un buscador de cuentas.
AVISO_CREDENCIALES = "El correo o la contrasena no son correctos."

#: Lo que se le dice a un proveedor recien registrado. Distingue "todavia no" de
#: "mal": un texto de rechazo haria pensar al visitante que su correo o su
#: contrasena estan mal, cuando lo unico que falta es una aprobacion.
AVISO_PENDIENTE = (
    "Tu cuenta de proveedor esta creada y esperando aprobacion. "
    "Podras entrar y publicar eventos en cuanto un administrador la approve."
)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.get("/login")
def login() -> str:
    """Pantalla de inicio de sesion."""
    return render_template("auth/login.html", errores={}, aviso_texto=None, correo="")


@auth_bp.post("/login")
def login_enviar() -> ResponseReturn:
    """Comprueba las credenciales contra la API y abre la sesion."""
    correo = _campo("email")
    contrasena = _campo("password")

    try:
        abrir_sesion(correo, contrasena)
    except CredencialesRechazadas:
        # Se repinta el formulario con el aviso generico y 401. El codigo de
        # negocio va a la bitacora para que el operador sepa que paso; al
        # visitante no le sirve de nada y no debe leakear nada.
        set_problem_code("invalid_credentials")
        return (
            render_template(
                "auth/login.html",
                errores={},
                aviso_texto=AVISO_CREDENCIALES,
                correo=correo,
            ),
            401,
        )
    except ApiProblemError as exc:
        # Un 422 si el API veto los datos por su forma. Los errores por campo se
        # pintan donde tocan; el resto del problema no se enseña.
        return _formulario_con_problema("auth/login.html", exc, correo)

    return redirect(destino_por_rol(rol_actual() or ""), code=303)


@auth_bp.get("/registro")
def registro() -> str:
    """Pantalla de alta."""
    return render_template(
        "auth/registro.html",
        errores={},
        aviso_texto=None,
        correo="",
        rol="client",
    )


@auth_bp.post("/registro")
def registro_enviar() -> ResponseReturn:
    """Crea la cuenta y abre sesion si la cuenta ya puede entrar."""
    correo = _campo("email")
    contrasena = _campo("password")
    rol = _campo("role")

    try:
        cuenta = registrar(correo, contrasena, rol)
    except ApiProblemError as exc:
        return _formulario_con_problema("auth/registro.html", exc, correo, contrasena, rol)

    if not cuenta.activa:
        # No se entra: la API rechazaria el login con un error que no explicaria
        # por que. Se responde 200 porque la cuenta se creo bien lo que se pide
        # era una sesion que no existe todavia, y un 4xx Invite al navegador a
        # repetir un alta que ya funciono.
        return (
            render_template(
                "auth/registro_pendiente.html",
                aviso_texto=AVISO_PENDIENTE,
                correo=cuenta.email,
            ),
            200,
        )

    return redirect(destino_por_rol(cuenta.rol), code=303)


@auth_bp.post("/salir")
@exigir_rol("client", "event-owner", "admin")
def salir() -> ResponseReturn:
    """Cierra la sesion local y revoca el refresh token en la API."""
    cerrar_sesion()
    return redirect(url_for("system.index"), code=303)


@auth_bp.get("/sesion")
@exigir_rol("client", "event-owner", "admin")
def sesion() -> str:
    """Estado de la sesion, para las pruebas y para depurar el salto."""
    identidad = identidad_actual()
    return render_template(
        "auth/sesion.html",
        usuario_id=str(identidad.usuario_id) if identidad else "",
        rol=identidad.rol if identidad else "",
    )


def _campo(nombre: str) -> str:
    """Valor de un campo del formulario, o cadena vacia si no vino.

    Nunca `None`: un formulario que no trae un campo obligatorio tiene que
    producir el mismo error de campo que uno que lo trae vacio, y `None` se
    colaria por ahi hasta romperse mas lejos.
    """
    return (request.form.get(nombre) or "").strip()


def _formulario_con_problema(
    plantilla: str,
    exc: ApiProblemError,
    correo: str,
    contrasena: str = "",
    rol: str = "client",
) -> tuple[str, int]:
    """Repinta el formulario con los errores por campo que devuelve la API.

    La contrasena se repone vacia siempre, incluso si venia en el POST. Devolverla
    en el HTML la escribiria en el historial del navegador, en el boton "atras" y
    en el DOM de la pagina, que son justo los sitios donde un formulario que
    acaba de fallar guarda todo lo demas.
    """
    set_problem_code(exc.code)
    return (
        render_template(
            plantilla,
            errores=exc.problem.field_errors(),
            aviso_texto=None,
            correo=correo,
            contrasena="",
            rol=rol,
        ),
        exc.status,
    )


__all__ = [
    "AVISO_CREDENCIALES",
    "AVISO_PENDIENTE",
    "ResponseReturn",
    "auth_bp",
]