# Design

## Context

See `proposal.md` for motivation. The current state that shapes this design:

- `pyproject.toml` contains only tool configuration. Dependencies exist only in
  the local `.venv`, and there is no lockfile or install command in the
  repository.
- `ruff check .` and `mypy backend web` pass today; the pytest suite passes with
  556 tests and needs a real PostgreSQL 16 instance. The web tests exercise the
  API in-process over an ASGI transport, so CI does not need running servers.
- The backend `Settings` requires `DATABASE_URL` and a valid Ed25519
  `JWT_PRIVATE_KEY_B64` at import time. The test suite also calls `get_settings()`
  directly, so CI must provide both values in addition to `TEST_DATABASE_URL`.
  The test DSN guard requires a database name ending in `_test`.
- The local PostgreSQL container is ad hoc (`ticket-plus-postgres`, image
  `postgres:16`, named volume `ticket-plus-pgdata`) and is not declared in the
  repository.
- Both applications expose `/health`. The web session store is in-process and
  assumes a single instance, which constrains the web container to one worker.
- There is no `.github/` directory. `publicar-pr.sh` creates a PR and calls
  `gh pr merge --auto`, which currently merges immediately because no checks are
  required.
- Stage I is archived with F4/F5 unbuilt, and Stage II plans Redis. This change
  must not depend on catalog, purchase, or Redis behavior.

## Goals / Non-Goals

**Goals:**

- One install path (`uv sync --frozen`) that reproduces the current working set
  locally and on a clean CI runner.
- A pull-request pipeline that gates lint, types, and the full suite on
  PostgreSQL, with stable check names usable by branch protection.
- Two runnable images with the same service boundaries the applications already
  assume, and a compose stack that starts from a clean checkout.
- No secret material in images or in the repository; configuration comes from
  the runtime environment.
- Leave application behavior and the existing test suite unchanged.

**Non-Goals:**

- Choosing a deployment host, publishing images to a registry, or deploying on
  merge. That is decision D and belongs to a later change.
- Adding Redis, the Stage II cache, or any Stage I F4/F5 behavior.
- Multi-instance web sessions, Kubernetes manifests, or a production ingress.
- Load, performance, or security scanning beyond the checks described here.

## Decisions

### D1. uv with PEP 621 metadata and a committed lockfile

`pyproject.toml` gains a `[project]` table with the runtime dependencies already
present in `.venv`, a `[dependency-groups]` dev group with pytest, Ruff, mypy,
and httpx, and `[tool.uv] package = false` because the applications are not
installed packages (pytest resolves them through `pythonpath`).

The lockfile is generated once from the current environment and committed.
Every install uses frozen mode:

```
uv sync --frozen
uv run ruff check .
uv run mypy backend web
uv run pytest
```

**Alternatives considered:** pip-tools with `requirements.in` and hashed
`requirements.txt` works but needs a second tool and does not manage the
environment. A plain `pip freeze` snapshot is a lock without a resolution or
update story. uv gives one tool for sync, run, and lock, and its cache keeps CI
fast.

### D2. One GitHub Actions workflow with four jobs

`.github/workflows/ci.yml` runs on `pull_request` and on `push` to `main`, with a
`concurrency` group keyed by ref and `cancel-in-progress: true`.

| Job | Purpose | Required check |
| --- | --- | --- |
| `quality` | `ruff check .`, `mypy backend web` | yes |
| `tests` | full pytest against `postgres:16` | yes |
| `containers` | matrix build of backend and web images, no push | yes |
| `smoke` | `docker compose up --wait`, probe both `/health` | yes |

The matrix produces check names `containers (backend)` and `containers (web)`,
which branch protection lists explicitly. The `smoke` job builds through compose
and runs only after `containers` succeeds, so a Dockerfile failure surfaces
before the slower stack start.

**Alternatives considered:** a single job is simpler but serializes feedback and
makes a Dockerfile failure look like a test failure. Separate workflows per
concern add files without adding signal.

### D3. PostgreSQL service and CI environment

The test job uses a `postgres:16` service with a `pg_isready` health check and a
database named `ticketplus_test`. It exports:

```
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ticketplus_test
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/ticketplus_test
JWT_PRIVATE_KEY_B64=<generated per run>
SECRET_KEY=<test-only value>
```

`JWT_PRIVATE_KEY_B64` is generated in a workflow step with the same Python
snippet documented for developers, so no signing key is committed and every run
uses a fresh one. Both DSNs point at the `_test` database because `Settings`
requires `DATABASE_URL` even when the suite overrides the session factory, and
the guard rejects names without the suffix. No SQLite fallback is introduced.

The `concurrency` marker already exists in `pyproject.toml`; once F5 adds those
tests, the test job runs the whole suite, including the marked tests, against the
same PostgreSQL service. No separate SQLite path is needed.

### D4. Two multi-stage images, non-root, health-checked

`backend/Dockerfile` and `web/Dockerfile` use the repository root as build
context so they can see `pyproject.toml`, `uv.lock`, `backend/`, and `web/`.

- Builder stage: `python:3.12-slim` plus a pinned uv binary; `uv sync --frozen
  --no-dev` into a virtual environment.
- Runtime stage: `python:3.12-slim`, a dedicated non-root `app` user, the
  virtual environment copied from the builder, and only the application sources.
  No compilers, no uv, no dev dependencies.
- `HEALTHCHECK` uses Python's standard library to call `/health`, avoiding curl
  in the image.
- The backend starts with `uvicorn --factory ticket_api.main:create_app`; the web
  starts with gunicorn against a small `wsgi.py` that calls `create_app()`.

**Web worker count is one.** `ServerSideSessionInterface` stores sessions in
process memory and documents that a shared store is required for more than one
instance. Gunicorn runs with `--workers 1` and configurable threads. Raising the
worker count without a shared session store would silently break login; that is
deferred until Stage II introduces Redis.

**Alternatives considered:** a single image running both processes couples the
two applications and loses independent scaling and health. The Flask development
server avoids a new dependency but is not a deployment-grade server, and the
topology is meant to match deployment.

### D5. Compose topology and startup order

`compose.yaml` defines three services and reuses the existing named volume so
local data survives the switch:

```
+-------------------+        +-------------------+
| db                |        | backend           |
| postgres:16       |<-------| alembic upgrade   |
| dev + _test DBs   |        | uvicorn :8000     |
+-------------------+        +-------------------+
         ^                            ^
         |                            |
         |                    +-------------------+
         +--------------------| web               |
                              | gunicorn :5000    |
                              | API_BASE_URL=     |
                              | http://backend    |
                              +-------------------+
```

- `db` mounts `docker/init/01-create-test-db.sql`, which creates the development
  database and `ticketplus_test`, and uses the volume name `ticket-plus-pgdata`
  to preserve the current local data.
- `backend` waits for `db` to be healthy, runs `alembic -c backend/alembic.ini
  upgrade head`, then starts uvicorn. Migrations run in the service entrypoint
  because there is a single backend replica; a multi-replica deployment will
  need a separate migration step.
- `web` waits for the backend health check and reaches the API over the compose
  network at `http://backend:8000`; the browser only reaches port 5000.
- `SESSION_COOKIE_SECURE=false` and an explicit `SECRET_KEY` are supplied for
  local HTTP. The application default for the cookie is `true`, which would make
  local login fail, and the default secret is explicitly a development
  placeholder.
- No Redis service is added; Stage II will extend this file.

**Alternatives considered:** using the ad hoc container outside compose leaves
the topology undocumented and unverifiable in CI. A separate compose file per
environment is unnecessary while there is only one environment.

### D6. Branch protection and merge flow

Required checks are applied to `main` with the GitHub API, not stored in the
repository. The check contexts are the four job names above. `publicar-pr.sh`
currently waits at most ten minutes for a merge; CI with image builds can exceed
that, so the script will wait on `gh pr checks --watch` before merging and keep a
longer bounded timeout.

**Alternatives considered:** relying on reviewers instead of branch protection
does not enforce the pipeline. Committing a ruleset file is possible in some
GitHub plans, but the API call is explicit and works for the current repository.

## Sequence Diagrams

### Pull request pipeline

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant GH as GitHub
    participant CI as Actions
    participant PG as PostgreSQL 16
    Dev->>GH: push branch / open pull request
    GH->>CI: trigger workflow
    par quality
        CI->>CI: uv sync --frozen, ruff, mypy
    and tests
        CI->>PG: start service and wait for pg_isready
        CI->>CI: generate ephemeral JWT key
        CI->>PG: pytest with TEST_DATABASE_URL (_test)
    and containers
        CI->>CI: build backend and web images
    and smoke
        CI->>CI: docker compose up --wait
        CI->>CI: probe backend and web /health
    end
    CI-->>GH: required check results
    alt every check green
        GH-->>Dev: pull request eligible for merge
    else any check red
        GH-->>Dev: merge blocked
    end
```

### Compose startup

```mermaid
sequenceDiagram
    participant C as compose
    participant DB as postgres
    participant API as backend
    participant WEB as web
    C->>DB: start with init script and named volume
    DB-->>C: healthy (pg_isready)
    C->>API: start after db is healthy
    API->>DB: alembic upgrade head
    API->>API: start uvicorn on 8000
    API-->>C: healthy (/health)
    C->>WEB: start after backend is healthy
    WEB->>API: probe /api/v1/ping
    WEB-->>C: healthy (/health)
```

## Risks / Trade-offs

- **CI duration** → `uv` and Docker layer caches, parallel jobs, and
  `cancel-in-progress` keep the feedback loop bounded. Image builds are the
  slowest step and are cached by buildx.
- **PostgreSQL service flakiness** → pin `postgres:16`, use a `pg_isready`
  health check, and let the runner wait for readiness before pytest.
- **uv adoption friction** → document the commands, generate the lock from the
  current `.venv`, and keep the existing virtual environment usable during the
  transition. Reverting the manifest restores the previous workflow.
- **Image size and attack surface** → multi-stage builds, no dev dependencies, no
  compiler in the runtime stage, and a non-root user.
- **Migration race on startup** → acceptable for one backend replica; documented
  as a constraint for the future multi-replica deployment.
- **In-memory web sessions** → gunicorn runs one worker. More workers require a
  shared session store and are deferred to the Stage II Redis work.
- **Port and container-name conflict** → compose reuses the existing
  `ticket-plus-pgdata` volume, and the migration plan stops the ad hoc container
  before starting compose.
- **Secret leakage into layers** → `.dockerignore` excludes `.env`, caches, and
  Git data; images receive configuration only at runtime; CI generates a fresh
  test key per run.
- **Branch protection requires repository admin** → if the API call is not
  permitted, CI still runs but does not gate merges; the task records the
  limitation instead of silently skipping it.

## Migration Plan

1. Create `feature/add-ci-and-container-build` from `main`; no direct work on
   `main`.
2. Land the manifest and lockfile first, and verify a clean install, lint, types,
   and the full suite with `uv`.
3. Land the workflow and verify it on a draft pull request: red on an injected
   failure, green after reverting it.
4. Apply branch protection and verify that a failing check blocks merge and that
   `publicar-pr.sh` waits for the checks.
5. Land the images and compose stack; stop the ad hoc PostgreSQL container and
   verify the stack starts from a clean checkout with the existing volume.
6. Re-run the full suite locally against the compose test database and run the
   compose smoke check.
7. Roll back per `proposal.md`; no application code or schema migration is
   involved.

## Open Questions

- Where images will be published and deployed is decision D and is intentionally
  deferred. The workflow builds without pushing, so adding a registry and deploy
  job later does not change this design.
- Whether the web image should use a shared session store is deferred until
  Stage II adds Redis; the single-worker constraint is recorded now.
