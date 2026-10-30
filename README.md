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

```bash
docker compose up -d --build --wait
```

Health endpoints:

- Backend: <http://127.0.0.1:8000/health>
- Web: <http://127.0.0.1:5000/health>

Stop the stack without deleting the database volume:

```bash
docker compose down
```

The web container runs gunicorn with a single worker because sessions are stored
in process memory. Do not raise the worker count until a shared session store is
introduced.

## Continuous integration

GitHub Actions runs the quality, test, container build, and compose smoke jobs on
every pull request and on every push to `main`. The required checks on `main` are:

- `quality`
- `tests`
- `containers (backend)`
- `containers (web)`
- `smoke`

A failing check blocks the merge. `publicar-pr.sh` waits for the checks before
merging.
