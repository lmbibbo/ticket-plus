"""HTTP-level errors keep their real status in the web application.

The generic `Exception` handler used to swallow every `HTTPException` (Flask
resolves handlers walking the exception's MRO), so an unknown URL answered
`500 internal_error`. These tests pin the real status, the safe flat body, the
redirect pass-through and the single access-log line.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from flask import Flask
from werkzeug.test import TestResponse

from conftest import ManyAccessLogs
from ticket_web.main import REQUEST_ID_HEADER


def _json(response: TestResponse) -> dict[str, Any]:
    """Response body parsed as a JSON object, with a readable failure."""
    body = response.json
    assert isinstance(body, dict), f"the response is not a JSON object: {body!r}"
    return body


def test_unknown_route_answers_404_with_not_found_code(web_app: Flask) -> None:
    """GIVEN a URL no route matches / WHEN the web processes the request /
    THEN it answers 404 with `not_found`, the correlation id and no internals."""
    client = web_app.test_client()
    response = client.get("/no-existe")

    assert response.status_code == 404
    body = _json(response)
    assert body["code"] == "not_found"
    assert body["status"] == 404
    assert body["request_id"], "the problem body lost the correlation id"
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]

    serialised = json.dumps(body, ensure_ascii=False)
    assert "Traceback" not in serialised
    assert web_app.root_path not in serialised


def test_wrong_method_answers_405(web_app: Flask) -> None:
    """GIVEN an existing route called with a method it does not accept / WHEN the
    web processes the request / THEN it answers 405 without leaking details."""
    client = web_app.test_client()
    response = client.get("/auth/salir")  # POST-only route

    assert response.status_code == 405
    body = _json(response)
    assert body["code"] == "method_not_allowed"
    assert body["status"] == 405


def test_redirect_abort_is_not_json_wrapped(web_app: Flask) -> None:
    """GIVEN a view that aborts with a redirect response / WHEN the web processes
    the request / THEN the 3xx response passes through untouched."""
    from flask import abort, redirect

    @web_app.get("/redirige")
    def _redirige() -> NoReturn:
        abort(redirect("/destino"))

    client = web_app.test_client()
    response = client.get("/redirige")

    assert response.status_code == 302
    assert response.headers["Location"] == "/destino"
    assert not (response.content_type or "").startswith("application/json")


def test_unexpected_exception_still_answers_500(web_app: Flask) -> None:
    """GIVEN a view that raises a non-HTTP programming error / WHEN the web
    processes the request / THEN it answers 500 `internal_error` with no
    internal detail."""
    detalle_interno = "detalle-privado-que-no-debe-filtrarse"

    @web_app.get("/explota")
    def _explota() -> NoReturn:
        raise ValueError(detalle_interno)

    client = web_app.test_client()
    response = client.get("/explota")

    assert response.status_code == 500
    body = _json(response)
    assert body["code"] == "internal_error"
    assert body["status"] == 500
    assert detalle_interno not in json.dumps(body, ensure_ascii=False)


def test_unknown_route_logs_exactly_one_line_with_status_404(
    web_app: Flask, capturar_ambos: ManyAccessLogs
) -> None:
    """GIVEN an unknown route / WHEN the web finishes the request / THEN the
    access log has exactly one line for it, with the real status and INFO level
    (the single-line guard is intact)."""
    with capturar_ambos as lines:
        client = web_app.test_client()
        response = client.get("/no-existe")

    assert response.status_code == 404
    web = lines["web"]
    assert len(web) == 1, web
    assert web[0]["status"] == 404
    assert web[0]["level"] == "INFO"
    assert lines["api"] == [], "an unknown local route must not reach the API"
