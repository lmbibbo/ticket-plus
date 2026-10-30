# ticket-plus

Plataforma de venta de tickets para eventos con tres roles (administradores,
event owners y clientes). Los clientes compran tickets, los event owners
publican eventos y los administradores gestionan todo el sistema.

The backend is a FastAPI application and the web frontend is a separate Flask
application. Both live in this repository and share the same Python environment.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.13.6
- PostgreSQL 16
- Docker and Docker Compose (for the containerized workflow)

## Install

The dependency set is pinned in `uv.lock`. Install it without resolving new
versions:

```bash
uv sync --frozen
```

## Environment

Copy the example file and replace every placeholder:

```bash
cp .env.example .env
```

Generate the Ed25519 key used to sign access tokens and paste it into
`JWT_PRIVATE_KEY_B64`:

```bash
uv run python -c "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as K; from cryptography.hazmat.primitives import serialization as s; import base64; print(base64.b64encode(K.generate().private_bytes(s.Encoding.DER, s.PrivateFormat.PKCS8, s.NoEncryption())).decode())"
```

The backend refuses to start without `DATABASE_URL` and
`JWT_PRIVATE_KEY_B64`. The test suite also requires `TEST_DATABASE_URL`, whose
database name must end in `_test`.

## Quality checks

```bash
uv run ruff check .
uv run mypy backend web
uv run pytest
```

The full suite needs a running PostgreSQL 16 instance and a dedicated test
database. It does not use SQLite.

## Containerized workflow

The compose topology starts PostgreSQL, the backend, and the web application.
See `compose.yaml` for the service definitions and `docker/init/` for the
database initialization script.

Start the whole stack:

```bash
docker compose up -d --build --wait
```

The backend container runs `alembic -c backend/alembic.ini upgrade head` before
starting uvicorn, so the schema is current after every start. The command is
idempotent: restarting the container or running `docker compose up` again does
not reapply an already applied migration.

Health endpoints:

- Backend: <http://127.0.0.1:8000/health>
- Web: <http://127.0.0.1:5000/health>

### Migrating from the old ad hoc container

Earlier setups used a standalone `ticket-plus-postgres` container. Stop and
remove that container, but keep its `ticket-plus-pgdata` volume; compose declares
the same volume and reuses the existing data:

```bash
docker stop ticket-plus-postgres
docker rm ticket-plus-postgres
docker compose up -d --build --wait
```

Do not run `docker compose down -v` during this migration: that flag deletes the
volume and the data with it.

### Local HTTP cookies

Compose runs the web application over plain HTTP and sets
`SESSION_COOKIE_SECURE=false` for that reason. Keep the same value in `.env` when
you run the web application directly on the host. Production deployments must
serve HTTPS and set `SESSION_COOKIE_SECURE=true`; otherwise the session cookie is
never sent by the browser.

### Stopping the stack

`docker compose down` stops and removes the containers and the network but keeps
the `ticket-plus-pgdata` volume, so the database survives and the next
`docker compose up` reuses it. `docker compose down -v` also deletes the volume
and all data; use it only when you want a clean database.

### Building and running images individually

Build the images when you need to inspect them:

```bash
docker build -f backend/Dockerfile -t ticket-plus-backend:local .
docker build -f web/Dockerfile -t ticket-plus-web:local .
```

Run an image against a PostgreSQL instance reachable from the host (the
`--network host` flag makes the `localhost` DSN in `.env` work):

```bash
docker run --rm --network host --env-file .env ticket-plus-backend:local
docker run --rm --network host --env-file .env \
  -e API_BASE_URL=http://127.0.0.1:8000 \
  -e SECRET_KEY=change-me \
  -e SESSION_COOKIE_SECURE=false \
  ticket-plus-web:local
```

The web container runs gunicorn with a single worker because sessions are stored
in process memory. Do not raise the worker count until a shared session store is
introduced.

## Continuous integration

GitHub Actions runs `.github/workflows/ci.yml` on every pull request and on every
push to `main`. A concurrency group cancels superseded runs for the same ref.

| Job | What it does |
| --- | --- |
| `quality` | `uv sync --frozen`, `ruff check .`, and `mypy backend web` |
| `tests` | full pytest suite against a `postgres:16` service |
| `containers (backend)` | buildx build of `backend/Dockerfile`, no push |
| `containers (web)` | buildx build of `web/Dockerfile`, no push |
| `smoke` | `docker compose up -d --build --wait`, then probes both `/health` endpoints |

The required checks on `main` are `quality`, `tests`, `containers (backend)`,
`containers (web)`, and `smoke`. Those are the exact check names the workflow
reports, so branch protection can list them without ambiguity.

A failing required check blocks the merge; a pull request with all five checks
green is eligible to merge. `publicar-pr.sh` waits up to 30 minutes on
`gh pr checks --watch` and stops before merging if a check fails or the wait times
out.
