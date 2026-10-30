"""WSGI entry point for the web container.

Gunicorn imports this module by name. The factory is called here, at import
time, so the process starts with the configuration read from the environment.
"""

from __future__ import annotations

from ticket_web.main import create_app

app = create_app()

__all__ = ["app"]
