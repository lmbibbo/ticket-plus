# Tasks

## 1. HTTP error handler

- [x] 1.1 Register an `HTTPException` error handler in
  `web/ticket_web/observability/middleware.py::register_request_context` that
  renders status >= 400 as a flat JSON problem body (`code` from the Werkzeug
  name, `title`, `status`, `detail`, `request_id`) and verify with a new test
  that an unknown route responds `404` with `code == "not_found"` and no
  traceback or path in the body
- [x] 1.2 Verify with a new test that a request to an existing route with a
  disallowed method responds `405` with `code == "method_not_allowed"` (e.g.
  `GET /auth/salir`, which is POST-only)
- [x] 1.3 Verify with a test that 3xx `HTTPException`s (e.g. `abort(302)`) are
  not JSON-wrapped and still produce the redirect response (design decision 2)
- [x] 1.4 Verify with a test that an unexpected non-HTTP exception still responds
  `500` with `code == "internal_error"` and exposes no internal detail

## 2. Access log correctness

- [x] 2.1 Log the access line from the new handler with the real status
  (`level="INFO"` for 4xx, `level="ERROR"` for 5xx HTTP errors) and verify with a
  test that an unknown route produces exactly one log line with `status=404`
  (single-line guard intact)

## 3. Integration verification

- [x] 3.1 Run `ruff check .`, `mypy backend web` and
  `pytest backend/ticket_api/tests web/ticket_web/tests` and verify they all
  pass
- [x] 3.2 With the backend and web apps running locally, verify with `curl` that
  `http://127.0.0.1:5000/no-existe` answers `404` with the problem body and
  `X-Request-ID`, while `/health` still answers `200`
