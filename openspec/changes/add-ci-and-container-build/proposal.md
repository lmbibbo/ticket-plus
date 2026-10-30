# Proposal

## Why

The repository has no dependency manifest or lockfile, no CI workflow, and no
container definitions. The 556-test suite only passes on the machine where the
virtual environment and the ad-hoc PostgreSQL container were created, so a fresh
clone cannot reproduce the build, and every pull request merges without any
automated check. This change makes the build reproducible, verifies every pull
request on GitHub Actions, and gives local development and the future deployment
a shared container topology.

## What Changes

- Add PEP 621 project metadata to `pyproject.toml` with the runtime and
  development dependencies currently installed in `.venv`, and commit a
  `uv.lock` lockfile managed with `uv`.
- Add a GitHub Actions workflow that runs Ruff, mypy, and the full pytest suite
  against a `postgres:16` service on every pull request and on `main`.
- Add a container-build job that builds the backend and web images without
  publishing them, and a smoke job that starts the compose topology and probes
  both `/health` endpoints.
- Add multi-stage `Dockerfile`s for the FastAPI backend and the Flask web app:
  pinned Python base, non-root runtime user, no secrets baked into layers, and a
  health check per image.
- Add `compose.yaml` with PostgreSQL, the backend, and the web app, including a
  database init script that creates the development and `_test` databases,
  ordered startup, and migrations applied before the backend serves traffic.
- Add `.env.example` with every required variable and the command to generate
  the Ed25519 JWT key, and document the `uv` and compose workflows in
  `README.md`.
- Require the CI checks on `main` through GitHub branch protection so the
  existing `publicar-pr.sh --auto` flow waits for green instead of merging
  immediately.
- Leave the deployment target, image registry publishing, and deploy-on-merge
  out of scope. Those decisions belong to a later change once a host is chosen.

## Capabilities

### New Capabilities

- `delivery-pipeline`: Reproducible dependency installation, continuous
  integration checks on pull requests, container images for both applications,
  and a local compose topology that mirrors the future deployment.

### Modified Capabilities

None. The runtime behavior described by the existing capabilities does not
change; this change adds how the project is built, verified, and run.

## Impact

- New files: `.github/workflows/ci.yml`, `backend/Dockerfile`,
  `web/Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`, and a
  PostgreSQL init script.
- `pyproject.toml` gains `[project]`, dependency groups, and `uv` settings;
  `uv.lock` is added to version control.
- `README.md` gains setup, test, and compose instructions.
- The developer workflow moves from `.venv/bin/pip` to `uv sync` and
  `uv run`; the existing `.venv` remains usable during the transition.
- CI needs GitHub Actions and a PostgreSQL service container; no production
  credentials or external provider accounts are required.
- The `main` branch gains a protection rule requiring the CI checks. The local
  `ticket-plus-postgres` container must be replaced by the compose service to
  avoid a port and name conflict.
- No application code, API contract, database schema, or OpenSpec capability
  behavior changes.

## Rollback Plan

1. Remove the required checks from `main` branch protection; pull requests merge
   as before, and `publicar-pr.sh --auto` no longer waits on CI.
2. Delete `.github/workflows/ci.yml`; GitHub stops running the pipeline without
   affecting application behavior.
3. Remove `compose.yaml`, both `Dockerfile`s, `.dockerignore`, and the init
   script; local development returns to the ad-hoc PostgreSQL container and the
   existing `.venv`.
4. Revert the `pyproject.toml` and `uv.lock` changes; the pinned versions match
   the current `.venv`, so no application code changes are needed.
5. No data migration or production state is involved. Discarded compose volumes
   are removed with `docker compose down -v`, and any preserved development
   database stays in its existing named volume.
6. Revert the `feature/add-ci-and-container-build` branch; no direct commits or
   pushes to `main` are made at any point.
