# Milestone 1: Skeleton

Goal: the full stack runs with `docker compose up`. A user can register, log in, upload a
recording to private storage, and see it in a meetings list. A background job proves the queue
works end to end, without needing a GPU. CI is green.

Put this file in `specs/stage-1/milestone-1.md`. Give Claude Code **one task at a time**.
Review, run and commit each task before starting the next.

---

## Before you start (you, not Claude Code)

- [ ] Create the GitHub repo and the folder layout from `CLAUDE.md`
- [ ] Add `CLAUDE.md` at the root and the spec as `specs/stage-1/spec.md`
- [ ] Write ADR-0001 (modular monolith + GPU worker), ADR-0002 (provider interface),
      ADR-0003 (integer millisecond timestamps), ADR-0004 (arq as the job queue)

---

## Task 1: Repository tooling

**Build:** uv workspace for `apps/api`, `apps/worker`, `packages/contracts`; Ruff, mypy strict,
pytest config; pnpm for `apps/web`; pre-commit hooks; `.editorconfig`; `.gitignore`.

**Done when:**
- [ ] `uv run ruff check .`, `uv run mypy apps` and `uv run pytest` all run (even with zero tests)
- [ ] pre-commit runs Ruff and blocks a badly formatted commit

## Task 2: Docker Compose stack

**Build:** `docker-compose.yml` with postgres, redis, minio (plus a one-shot service that creates
a private bucket), api, worker, web. Health checks on every service. `.env.example` with every
variable the stack needs.

**Done when:**
- [ ] `docker compose up -d` on a clean clone brings everything to healthy
- [ ] The MinIO bucket exists and is not publicly readable
- [ ] No secret values are committed

## Task 3: API skeleton

**Build:** FastAPI app factory; settings with pydantic-settings that load `config/default.yaml`
plus `config/profiles/$APP_PROFILE.yaml` plus environment variables; structured JSON logging;
a single error-handling layer mapping domain exceptions to HTTP; `GET /health` checking
Postgres, Redis and MinIO.

**Done when:**
- [ ] `/health` reports each dependency's status
- [ ] Tests cover settings precedence (default < profile < env) and the health endpoint
- [ ] OpenAPI docs are available at `/docs`

## Task 4: Database foundation

**Build:** async SQLAlchemy 2.0 engine and session handling; Alembic setup; `Workspace`,
`User`, `Membership` models; first migration; a Testcontainers Postgres fixture shared by all
integration tests.

**Done when:**
- [ ] `alembic upgrade head` and `alembic downgrade base` both work (tested in CI)
- [ ] Integration tests run against a real Postgres container, not mocks

## Task 5: Authentication and workspaces

**Build:** register, login, logout, `GET /me`. Passwords hashed with argon2. Session token in
an httpOnly, secure cookie. On registration the user gets a personal workspace. A dependency
that resolves the current user and workspace for every protected route.

**Done when:**
- [ ] Route-enumeration test: every route requires auth except an explicit public allowlist
      (`/health`, `/auth/register`, `/auth/login`)
- [ ] Wrong password, unknown user and expired token all return clean, non-leaky errors
- [ ] Login is rate-limited

## Task 6: Storage module

**Build:** `core/storage.py` wrapping the S3 client: create pre-signed upload (PUT) and
download (GET) URLs with short expiry, check that an object exists, read its size, delete.
Object keys are namespaced by workspace: `workspaces/{workspace_id}/meetings/{meeting_id}/original.<ext>`.

**Done when:**
- [ ] Integration tests against a MinIO container: upload via pre-signed URL, verify, delete
- [ ] An expired URL is rejected (test with a very short expiry)

## Task 7: Meetings module

**Build:** `Meeting` and `AudioAsset` models with the fields from the spec's data model;
`POST /meetings` (title, language hint, consent confirmed) returns a pre-signed upload URL;
`POST /meetings/{id}/upload-complete` verifies the object exists and allowed format/size;
`GET /meetings` (list) and `GET /meetings/{id}` (detail). Meeting status: `awaiting_upload`
→ `uploaded` → `probing` → `ready` | `failed`.

**Done when:**
- [ ] Creating a meeting without `consent_confirmed = true` is rejected
- [ ] A user cannot see or touch another workspace's meeting (explicit test)
- [ ] Files over 3 hours or in unsupported formats are rejected with a clear message

## Task 8: Job queue and a first worker job

**Build:** arq worker in `apps/worker`. On `upload-complete`, the API enqueues `probe_audio`.
The job reads the file from storage, runs ffprobe for duration, format and sample rate, writes
them to `AudioAsset`, and moves the meeting to `ready`. `Job` model with attempts, error and
heartbeat. Retries with backoff up to `max_attempts`. No GPU needed.

**Done when:**
- [ ] Upload a real file → meeting reaches `ready` with correct duration
- [ ] A corrupt file → job retries, then the meeting is `failed` with a readable error
- [ ] Running the same job twice gives the same result (idempotency test)

## Task 9: Typed API client

**Build:** a script that exports the OpenAPI schema and generates a TypeScript client into
`apps/web` (`pnpm gen:api`). CI fails if the committed client is out of date.

**Done when:**
- [ ] Changing an API schema without regenerating the client fails CI

## Task 10: Frontend skeleton

**Build:** Next.js app with login and register pages, an authenticated layout, a meetings list
(title, status, duration, created), and an upload page: drag and drop, consent checkbox,
language hint, upload progress bar, then status updates until `ready`.

**Done when:**
- [ ] Upload goes directly from the browser to storage via the pre-signed URL
      (the file never passes through the API)
- [ ] Vitest tests for the upload form validation
- [ ] Logged-out users are redirected to login

## Task 11: CI pipeline

**Build:** GitHub Actions workflow: Ruff, mypy, pytest (with Testcontainers), migration
up/down, web lint, type-check, Vitest, build, and a Playwright smoke test against the compose
stack: register → log in → upload a short fixture clip → see it reach `ready`.

**Done when:**
- [ ] The whole pipeline runs on every push in under 10 minutes
- [ ] A failing test blocks merging to `main`

## Task 12: README (you, with Claude Code's help)

**Build:** what the project is (two sentences), quick start (`docker compose up`), architecture
diagram, links to the spec and ADRs, current status ("Milestone 1 of 7 complete").

**Done when:**
- [ ] A friend can clone and run it from the README alone. Ask one to try.

---

## How to prompt Claude Code for each task

```
Read CLAUDE.md, specs/stage-1/spec.md and specs/stage-1/milestone-1.md.
We are doing Task N: <name>.
First: give me your plan in bullets and the list of files you will create or change.
Wait for my OK before writing code.
Then implement it with tests, run lint, types and tests, and report the results.
```

After each task: read the diff, run it yourself, ask "why" about anything you don't understand,
then commit.
