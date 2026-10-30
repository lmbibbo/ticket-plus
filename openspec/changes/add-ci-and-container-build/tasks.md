# Tasks

Scope is decisions A, B, and C: reproducible install, CI, and containerized
topology. Decision D (deployment target, registry publishing, deploy on merge)
is intentionally excluded and will be a separate change.

## 1. Reproducible install

- [x] 1.1 Create the branch `feature/add-ci-and-container-build` from `main` and verify with `git branch --show-current` that it is active and `git status --porcelain` is empty
- [x] 1.2 Install `uv` locally with the official installer and verify `uv --version` succeeds; record the version in `README.md`
- [x] 1.3 Add `[project]` to `pyproject.toml` with name, version, `requires-python = ">=3.12"`, and the runtime dependencies pinned to the versions currently installed in `.venv` (FastAPI, Uvicorn, Pydantic, pydantic-settings, SQLAlchemy, Alembic, psycopg binary, Flask, Jinja2, Werkzeug, itsdangerous, PyJWT, cryptography, argon2-cffi, email-validator, python-dotenv); verify `uv lock` resolves and `uv sync --frozen` installs them
- [x] 1.4 Add `[dependency-groups]` with the dev tools (`pytest`, `ruff`, `mypy`) and `[tool.uv] package = false`; verify `uv run python -c "import pytest, ruff, mypy, httpx"` succeeds
- [x] 1.5 Add `gunicorn` as a runtime dependency for the web image and verify the lockfile updates and `uv run python -c "import gunicorn"` succeeds
- [x] 1.6 Recreate the environment from the lockfile only (remove `.venv` or use a temporary `UV_PROJECT_ENVIRONMENT`) and verify `uv sync --frozen`, `uv run ruff check .`, `uv run mypy backend web`, and `uv run pytest` all pass with the current test count
- [x] 1.7 Document the install, test, environment-variable, and JWT-key-generation commands in `README.md` and verify each command runs exactly as written

## 2. Continuous integration

- [x] 2.1 Add `.github/workflows/ci.yml` with `pull_request` and `push` to `main` triggers, a per-ref `concurrency` group with `cancel-in-progress`, and read-only permissions; verify on GitHub that the workflow appears and starts on a push to the branch
- [x] 2.2 Implement the `quality` job with uv setup, `uv sync --frozen`, `uv run ruff check .`, and `uv run mypy backend web`; verify the job is green on the branch
- [x] 2.3 Implement the `tests` job with a `postgres:16` service, a `pg_isready` health check, `DATABASE_URL` and `TEST_DATABASE_URL` pointing at a database whose name ends in `_test`, a per-run generated `JWT_PRIVATE_KEY_B64`, and `uv run pytest`; verify the job is green and the suite runs against PostgreSQL rather than SQLite
- [x] 2.4 Implement the `containers` matrix job that builds the backend and web images with buildx and the GitHub Actions cache, with `push: false`; verify both `containers (backend)` and `containers (web)` checks appear and pass
- [x] 2.5 Implement the `smoke` job that runs `docker compose up -d --build --wait`, probes `/health` on the backend and the web app, prints `docker compose logs` on failure, and always runs `docker compose down`; verify the job is green and fails when a health probe is forced to fail
- [x] 2.6 Verify the red path on a draft pull request by temporarily breaking one test, confirming the required checks fail and the merge is blocked, then reverting the break and confirming green checks allow the merge
- [x] 2.7 Apply branch protection to `main` with the GitHub API using the four check contexts, and verify with `gh api repos/lmbibbo/ticket-plus/branches/main/protection` that the contexts are listed and a failing pull request cannot merge
- [x] 2.8 Update `publicar-pr.sh` to wait on `gh pr checks --watch` before merging and to use a timeout longer than the current ten minutes; verify it waits for a green pipeline instead of merging immediately
- [x] 2.9 Document the CI jobs, required checks, and the red/green merge behavior in `README.md` and verify the documented check names match the workflow

## 3. Container images

- [x] 3.1 Add `.dockerignore` excluding `.git`, `.venv`, `.env`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, and `openspec`; verify the build context no longer contains those paths
- [x] 3.2 Add `backend/Dockerfile` as a multi-stage build using `python:3.12-slim` and a pinned uv binary, installing runtime dependencies with `uv sync --frozen --no-dev`, running as a non-root user, and starting `uvicorn --factory ticket_api.main:create_app`; verify the image builds and `/health` responds
- [x] 3.3 Add `web/wsgi.py` exposing `app = create_app()` and `web/Dockerfile` as a multi-stage build that runs gunicorn with one worker and configurable threads; verify the image builds, the process runs as non-root, and `/health` responds
- [x] 3.4 Add a `HEALTHCHECK` to both images using Python's standard library instead of curl, and verify `docker inspect` reports the health check and the container reaches the healthy state
- [x] 3.5 Verify no secret material is embedded by inspecting `docker history --no-trunc` and the runtime environment of both images, and confirm neither image contains `.env`, `.git`, the host `.venv` checkout, or test credentials; the runtime virtual environment at `/app/.venv` is expected
- [x] 3.6 Document the image build and run commands, the single-worker web constraint, and the health endpoints in `README.md`; verify each documented command runs as written

## 4. Compose topology

- [x] 4.1 Add `docker/init/01-create-test-db.sql` that creates the development database and the `ticketplus_test` database, and verify both exist after a fresh volume initialization
- [x] 4.2 Add `compose.yaml` with `db`, `backend`, and `web` services, the named volume `ticket-plus-pgdata`, `env_file`, health checks, and `depends_on` conditions; verify `docker compose config` resolves without errors
- [x] 4.3 Implement the backend entrypoint that runs `alembic -c backend/alembic.ini upgrade head` before starting uvicorn, and verify `alembic current` reports the head revision after startup
- [x] 4.4 Add `.env.example` with every variable required by both `Settings` classes, placeholder values only, and the JWT-key generation command; verify every key documented in the example exists in the configuration and no real secret is present
- [x] 4.5 Stop and remove the ad hoc `ticket-plus-postgres` container while preserving the `ticket-plus-pgdata` volume, start the compose stack, and verify both `/health` endpoints respond and the web entry point loads
- [x] 4.6 Verify test-database isolation by running the full suite against the compose database and confirming the development database is not modified
- [x] 4.7 Verify restart idempotence by restarting the backend and running `docker compose up` again, confirming migrations are safe to repeat and no data is lost; confirm `docker compose down` preserves the volume
- [x] 4.8 Document the compose workflow, the existing-container migration step, the local `SESSION_COOKIE_SECURE=false` requirement, and the data-preserving `down` behavior in `README.md`; verify the documented commands run as written

## 5. Integration and rollback

- [ ] 5.1 Run the full suite locally with `uv run pytest` against the compose PostgreSQL instance and verify the current test count passes
- [ ] 5.2 Run `uv run ruff check .` and `uv run mypy backend web` and verify both report no issues
- [ ] 5.3 Run the compose smoke flow exactly as the CI job does (`docker compose up -d --build --wait`, probe both health endpoints, `docker compose down`) and verify it matches the pipeline result
- [ ] 5.4 Rehearse the rollback on a scratch copy or scratch branch: disable the required checks, remove the workflow and compose files, and verify the applications still run with the previous virtual environment and the existing database volume
- [ ] 5.5 Run `openspec validate --specs --strict` and `openspec validate add-ci-and-container-build --strict` and verify both pass
- [ ] 5.6 Scan the tracked tree for secret material (`git grep` for key and password patterns) and verify `.env` is untracked and only placeholders appear in `.env.example`
- [ ] 5.7 Open the pull request, wait for every required check to pass, merge through `publicar-pr.sh`, delete the branch, and verify `main` still enforces the protection rule
