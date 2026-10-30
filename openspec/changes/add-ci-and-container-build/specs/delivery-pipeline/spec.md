# Spec Delta

## Purpose

Define how the project is installed reproducibly, verified automatically on every
pull request, packaged as container images, and run locally with the same service
topology expected in deployment.

## ADDED Requirements

### Requirement: Reproducible dependency installation
The project SHALL declare its runtime and development dependencies in a versioned
manifest and SHALL commit a lockfile that pins every resolved version and hash.
A fresh checkout SHALL install the same dependency set without relying on a
pre-existing virtual environment.

#### Scenario: Fresh checkout installs the pinned set
- **GIVEN** a clean clone of the repository with no virtual environment
- **WHEN** a developer installs the project using the documented command
- **THEN** the installation uses the committed lockfile and produces a working
  environment for both applications

#### Scenario: Lockfile drift is rejected
- **GIVEN** a manifest change that was not reflected in the lockfile
- **WHEN** the continuous integration pipeline installs dependencies in frozen mode
- **THEN** the installation fails and the pipeline reports the drift instead of
  resolving new versions

#### Scenario: Test and quality tooling are available
- **GIVEN** a fresh checkout installed from the lockfile
- **WHEN** the developer runs the documented test and quality commands
- **THEN** pytest, Ruff, and mypy are available at the pinned versions and the
  existing suites run without installing additional packages

### Requirement: Continuous integration on pull requests
The repository SHALL run an automated pipeline on every pull request and on every
push to `main`. The pipeline SHALL execute linting, static type checking, and the
full test suite against a PostgreSQL instance, and SHALL report a failing result
when any of those checks fails.

#### Scenario: Pull request with a failing test
- **GIVEN** a pull request whose code makes at least one test fail
- **WHEN** the pipeline runs
- **THEN** the test check reports failure and the pull request cannot be merged
  while the required check is failing

#### Scenario: Pull request with clean checks
- **GIVEN** a pull request whose code passes linting, type checking, and tests
- **WHEN** the pipeline runs
- **THEN** every required check reports success and the pull request is eligible
  for merge

#### Scenario: Tests run against PostgreSQL
- **GIVEN** a pipeline run on a clean runner
- **WHEN** the test job starts
- **THEN** it provisions a PostgreSQL 16 instance, points the test DSN at a
  dedicated database whose name ends in `_test`, and runs the full suite without
  substituting SQLite

#### Scenario: Duplicate runs are cancelled
- **GIVEN** a pull request that receives a new push while a pipeline is running
- **WHEN** the new pipeline starts
- **THEN** the superseded run for the same pull request is cancelled and only the
  latest result gates the merge

### Requirement: Container images for both applications
The repository SHALL define a container image for the backend API and a separate
container image for the web application. Each image SHALL build from repository
sources, run as a non-root user, expose a health check, and start without
embedding secrets or environment-specific credentials in its layers.

#### Scenario: Image builds from a clean checkout
- **GIVEN** a clean checkout with no local build cache
- **WHEN** the image build command runs for either application
- **THEN** the image builds successfully from the committed sources and the
  documented build context

#### Scenario: Container starts and reports health
- **GIVEN** a built image and the required environment variables supplied at runtime
- **WHEN** the container starts
- **THEN** the process runs as a non-root user and the application health endpoint
  responds successfully

#### Scenario: Image contains no secrets
- **GIVEN** a built image for either application
- **WHEN** its environment, filesystem, and layer history are inspected
- **THEN** it contains no `.env` file, private key, database password, session
  secret, or test credential

### Requirement: Local compose topology
The repository SHALL provide a compose definition that runs PostgreSQL, the
backend API, and the web application as separate services with persistent
database storage, ordered startup, and health checks. The local topology SHALL
match the service boundaries the applications expect in deployment.

#### Scenario: Clean environment starts successfully
- **GIVEN** a clean checkout with a local environment file derived from the
  documented example
- **WHEN** the compose stack starts
- **THEN** PostgreSQL becomes healthy, the backend becomes healthy after applying
  its migrations, the web application becomes healthy, and the web entry point
  responds

#### Scenario: Migrations run before traffic is served
- **GIVEN** a database at an older migration revision
- **WHEN** the backend service starts
- **THEN** it applies all pending migrations before accepting requests and the
  schema reaches the latest revision

#### Scenario: Development and test databases are separated
- **GIVEN** a freshly initialized compose database volume
- **WHEN** the database initialization completes
- **THEN** both the development database and a dedicated test database whose name
  ends in `_test` exist, and the test suite can run without touching development
  data

#### Scenario: Services wait for their dependencies
- **GIVEN** a compose stack started from scratch
- **WHEN** the web application starts before the backend is ready
- **THEN** the web service waits for the backend health check instead of
  answering requests with backend-unavailable errors

### Requirement: Pipeline validates containers
The continuous integration pipeline SHALL build both application images and
SHALL start the compose topology to verify that the services become healthy. A
container build or smoke failure SHALL fail the pipeline.

#### Scenario: Broken container definition fails the pipeline
- **GIVEN** a pull request that breaks a Dockerfile or the compose definition
- **WHEN** the pipeline runs the container job
- **THEN** the job fails and the pull request cannot be merged while the check is
  required

#### Scenario: Smoke check detects an unhealthy service
- **GIVEN** a compose stack whose backend or web service does not become healthy
- **WHEN** the smoke job probes the health endpoints within the configured timeout
- **THEN** the job fails and reports which service did not become healthy

### Requirement: Configuration example and secret hygiene
The repository SHALL document every environment variable required to run the
stack and SHALL provide an example environment file containing placeholders
rather than working credentials. The example SHALL include the procedure for
generating the JWT signing key.

#### Scenario: New environment is configured from the example
- **GIVEN** a developer with no existing environment file
- **WHEN** they copy the example file, generate the JWT key as documented, and
  start the compose stack
- **THEN** the services start with the generated values and no secret from the
  example file is reused

#### Scenario: Missing required configuration fails fast
- **GIVEN** a stack started without a required database or signing variable
- **WHEN** the backend process initializes
- **THEN** it exits with an explicit configuration error instead of starting with
  an unsafe default

#### Scenario: Unsafe defaults are overridden
- **GIVEN** the compose definition used for local development
- **WHEN** the web service starts
- **THEN** its session signing secret and cookie security settings are supplied
  explicitly rather than inherited from the application's development defaults
