# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Meeting-to-Engineering Copilot. Stage 1 turns an uploaded meeting recording into a trustworthy,
replayable record: diarized transcript, word-level timestamps and confidence, flagged unclear
spans with audio replay. Later stages add live mode, requirement extraction and codebase
investigation.

Specs (what to build) are in `specs/<stage>/`: the current one is `specs/stage-1/spec.md`, with
milestone task lists beside it. Decisions (why it is built this way) are in `docs/decisions/`. Read them before
changing architecture. If a task conflicts with the spec or an ADR, stop and ask; do not
silently diverge.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, pydantic-settings
- Worker: arq (Redis-backed job queue), faster-whisper, pyannote
- Storage: Postgres, Redis, RustFS (S3-compatible; see ADR-0006)
- Frontend: Next.js (App Router), TypeScript strict, typed API client generated from OpenAPI
- Tooling: uv, Ruff, mypy (strict), pytest, Hypothesis, Testcontainers, pnpm, ESLint, Vitest, Playwright
- Everything runs with `docker compose up`

## Layout

```
apps/api/src/app/<module>/   meetings, transcripts, review, jobs, auth
apps/api/src/app/core/       config, db, storage, logging, security
apps/worker/                 pipeline stages + speech/diarization providers
apps/web/                    Next.js frontend
packages/contracts/          shared schemas → OpenAPI → generated TS client
eval/                        dataset loaders, normaliser, metrics, REPORT.md
config/                      default.yaml + profiles/{ci,laptop,desktop}.yaml
specs/stage-1/               spec.md + milestone-N.md task lists (one folder per stage)
docs/                        architecture.md, decisions/ (ADRs)
```

## Architecture rules (non-negotiable)

1. **Modular monolith + one GPU worker.** Do not add new services without a new ADR.
2. **Module boundaries.** Each module exposes `service.py` (public functions) and keeps
   `models.py`, `repository.py`, `routes.py`, `schemas.py` private. A module never imports
   another module's repository or models, and never queries another module's tables.
3. **Routes are thin.** Validation in schemas, logic in services, data access in repositories.
4. **Time is integer milliseconds** from the start of the meeting. No floats, no wall-clock times
   for audio positions.
5. **Never overwrite model output.** Human corrections are stored beside the original.
6. **Segments are appended, never edited in place.** Status is `draft` or `final`.
7. **Every workspace-owned query is scoped by `workspace_id`.** No exceptions.
8. **Providers sit behind interfaces** (`TranscriptionProvider`, `DiarizationProvider`). Nothing
   outside `apps/worker/providers/` imports a model library directly.
9. **The GPU worker processes one job at a time.** Jobs are idempotent and resumable per step.
10. **Secrets only from environment variables.** Never commit real keys; update `.env.example`.

## Coding conventions

- Type hints everywhere; mypy strict must pass. No `Any` without a comment explaining why.
- Async all the way in the API. No blocking calls in request handlers.
- Errors: raise domain exceptions in services; map them to HTTP responses in one place.
- Logging: structured JSON via the shared logger; include `job_id` / `meeting_id` when known.
  Never log audio content, transcript text or credentials.
- Names: full words, no abbreviations except well-known ones (id, url, api, vad, asr).
- Keep functions small; if a function needs a comment to explain *what* it does, split it.
- Frontend: server components by default; client components only for interactivity.
  All API calls go through the generated client.

## Tests

- Every change ships with tests. A bug fix starts with a failing test that reproduces it.
- Unit tests for logic, property tests (Hypothesis) for invariants such as "timestamps never go
  backwards" and "no word lost across chunks".
- Integration tests use Testcontainers (Postgres, Redis, RustFS). Do not mock the database.
- CI has no GPU: use the `fake` provider or the tiny CPU model (`APP_PROFILE=ci`).
- Security: the route-enumeration test must pass; every new route requires auth unless it is
  explicitly listed as public.

## Commands

```bash
docker compose up -d                 # full stack
uv run pytest                        # backend + worker tests
uv run ruff check . && uv run ruff format --check .
uv run mypy apps
uv run alembic -c apps/api/alembic.ini upgrade head
pnpm --dir apps/web test             # Vitest
pnpm --dir apps/web exec playwright test
pnpm --dir apps/web gen:api          # regenerate TS client from OpenAPI
```

## Hardware profiles

`APP_PROFILE` selects `config/profiles/<name>.yaml`:
- `ci`: no GPU, fake provider
- `laptop`: GTX 1050 (3 GB, Pascal). Uses the **CUDA 12.6 PyTorch build**, pinned. Smaller models.
  Never publish benchmark numbers from this profile.
- `desktop`: RTX 3060 Ti (8 GB). Benchmarks and demo.

## How to work in this repo

- Work on one task at a time from the current milestone file in `specs/<stage>/`. Keep changes small and focused.
- Before writing code, state the plan in a few bullets and list the files you will touch.
- After writing code, run lint, types and tests, and report the results honestly.
- Do not add dependencies without saying why; prefer the standard library and existing deps.
- Do not reformat or refactor unrelated code.
- Use conventional commits: `feat(meetings): add upload endpoint`, `test(review): ...`.
- If something in the spec is unclear or seems wrong, ask instead of guessing.
