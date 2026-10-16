"""Almacen de sesiones del lado servidor para la aplicacion web.

La cookie del navegador no lleva el contenido de la sesion: solo un
identificador opaco. Los tokens de acceso y de renovacion se quedan aqui, en
un almacen separado del esquema de dominio, de modo que la web nunca necesita
una conexion a la base de datos para saber quien esta conectado.

El almacen es deliberadamente simple y vive en memoria del proceso. Es
suficiente para una instancia unica; cuando el despliegue necesite mas de una,
el contrato de `SessionInterface` es el punto unico a cambiar por Redis u otro
almacen compartido, sin tocar las vistas.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from flask import Request, Response
from flask.sessions import SessionInterface, SessionMixin
from werkzeug.datastructures import CallbackDict

#: Tiempo de inactividad tras el cual una sesion se considera caduca.
SESSION_IDLE_SECONDS: float = 60 * 60 * 8


class ServerSideSession(CallbackDict[str, Any], SessionMixin):
    """Contenido de una sesion, con su identificador de servidor.

    `CallbackDict` marca `modified` en cada escritura: es lo que decide si la
    sesion se vuelve a guardar al final de la peticion.
    """

    def __init__(self, initial: dict[str, Any] | None = None, sid: str | None = None) -> None:
        def on_update(self: ServerSideSession) -> None:
            self.modified = True

        super().__init__(initial, on_update)
        self.sid: str = sid or str(uuid.uuid4())
        self.modified = False
        self.accessed = False


class ServerSideSessionInterface(SessionInterface):
    """Guarda la sesion en el servidor y deja en la cookie solo el `sid`.

    Sin este interfaz, Flask firmaria el contenido completo de la sesion y lo
    devolveria al navegador: los tokens quedarian expuestos a quien pueda leer
    la cookie, que es justo lo que la especificacion prohibe.
    """

    def __init__(self, idle_seconds: float = SESSION_IDLE_SECONDS) -> None:
        self.idle_seconds = idle_seconds
        self.store: dict[str, tuple[dict[str, Any], float]] = {}

    def open_session(self, app: Any, request: Request) -> ServerSideSession:
        """Recupera la sesion de la cookie, o abre una vacia si no existe."""
        sid = request.cookies.get(self.get_cookie_name(app))
        if sid is not None:
            entrada = self.store.get(sid)
            if entrada is not None:
                contenido, visto = entrada
                if time.monotonic() - visto <= self.idle_seconds:
                    self.store[sid] = (contenido, time.monotonic())
                    sesion = ServerSideSession(dict(contenido), sid=sid)
                    sesion.accessed = True
                    return sesion
                # Caducada o desconocida: se descarta para que no crezca el
                # almacen con identificadores que nadie volvera a presentar.
                del self.store[sid]
        return ServerSideSession()

    def save_session(self, app: Any, session: SessionMixin, response: Response) -> None:
        """Vuelca la sesion en el almacen o la borra si quedo vacia."""
        nombre = self.get_cookie_name(app)
        if not session:
            self.store.pop(session.sid, None)  # type: ignore[attr-defined]
            response.delete_cookie(
                nombre,
                domain=self.get_cookie_domain(app),
                path=self.get_cookie_path(app),
                secure=self.get_cookie_secure(app),
                httponly=self.get_cookie_httponly(app),
                samesite=self.get_cookie_samesite(app),
            )
            response.vary.add("Cookie")
            return

        sid: str = session.sid  # type: ignore[attr-defined]
        self.store[sid] = (dict(session), time.monotonic())
        response.set_cookie(
            nombre,
            sid,
            httponly=self.get_cookie_httponly(app),
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
            path=self.get_cookie_path(app),
            domain=self.get_cookie_domain(app),
        )
        response.vary.add("Cookie")


__all__ = [
    "SESSION_IDLE_SECONDS",
    "ServerSideSession",
    "ServerSideSessionInterface",
]