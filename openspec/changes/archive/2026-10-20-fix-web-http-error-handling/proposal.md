# Proposal

## Why

The web application converts every unhandled `HTTPException` (unknown route, wrong
method, etc.) into a `500 internal_error` because the generic
`@app.errorhandler(Exception)` in `web/ticket_web/observability/middleware.py`
swallows `HTTPException` subclasses before Flask can render their real status. A
missing URL must answer `404`, not `500`; today every typo looks like a server
crash, it pollutes the access log with false 5xx lines, and monitoring cannot
distinguish a bad link from a real fault.

## What Changes

- Add an explicit `HTTPException` handler in the web application that renders the
  exception's own status code (404, 405, 400, etc.) with a JSON problem body in
  the same shape used by the generic error handler (`code`, `title`, `status`,
  `detail`, `request_id`).
- Narrow the generic `Exception` handler so it only handles genuine unexpected
  failures, which keep answering `500 internal_error` with no internal details.
- The access log records the real status for HTTP errors (e.g. `404` instead of
  `500`) while keeping the `request_id` correlation unchanged.
- Behavior of API-problem translation (`ApiProblemError`), access denied, CSRF and
  backend-unavailable responses stays untouched.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `web-frontend`: new requirement that HTTP-level errors (unknown route, method
  not allowed, malformed request routing) preserve their real status code and
  expose a safe JSON problem body without internal details; unexpected exceptions
  continue to answer `500`.

## Impact

- Affected code: `web/ticket_web/observability/middleware.py` (error handler
  registration), possibly `web/ticket_web/main.py` if the handler is registered
  next to the API-problem handlers.
- Affected tests: `web/ticket_web/tests/` — new test asserting `404` for unknown
  routes and `405` for wrong methods; existing observability tests must keep
  passing (log line now records the real status).
- No API, database, schema or dependency changes. No change to the backend
  FastAPI application.

## Rollback

- The change is a single handler addition in the web application: revert the
  commit (or merge PR) that introduces it and the previous behavior (generic
  handler answering `500`) is restored automatically.
- No data migration, no persisted state and no configuration are involved, so
  rollback requires no cleanup beyond `git revert`.
