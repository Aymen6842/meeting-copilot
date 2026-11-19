# ADR-0004: arq as the job queue

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Aymen

## Context

Uploads trigger long-running processing (probing, normalising, transcription, diarization,
alignment, flagging). The API must enqueue jobs and return immediately; the GPU worker must take
jobs one at a time, retry failures with backoff, and report progress. Redis is already part of
the stack. The API is fully async (FastAPI + async SQLAlchemy).

Candidates: **arq** (async, Redis-based, small) and **Dramatiq** (synchronous workers, mature,
Redis or RabbitMQ).

## Decision

Use **arq**.

- Its async model matches the rest of the backend, so the API and worker share the same async
  database and storage code.
- Small surface area: easy to read, test and reason about.
- Concurrency for the GPU worker is set to 1 (`worker.gpu_concurrency`).
- Application-level job state (`Job` table: stage, attempts, progress, error, heartbeat) is kept
  in Postgres, so the product does not depend on queue internals for status, history or recovery.

## Consequences

**Positive**
- One async style across the codebase.
- Job state is queryable and durable in Postgres, independent of Redis.

**Negative**
- arq is smaller and less widely used than Dramatiq or Celery; fewer built-in features
  (for example, no built-in rate limiting or complex routing).
- If Redis loses data, queued jobs must be re-enqueued from the `Job` table. A recovery task that
  re-queues jobs stuck in `queued` or with a stale heartbeat handles this.

## Alternatives considered

- **Dramatiq:** mature and reliable, but synchronous workers would mean a second, sync version of
  the database and storage code in the worker.
- **Celery:** feature-rich but heavy for this scale, with more configuration than needed.
- **Postgres-only queue (SELECT … FOR UPDATE SKIP LOCKED):** viable and removes Redis, but Redis
  is also useful for progress events (SSE) and live mode later. Worth revisiting if Redis becomes
  a burden.
