# ADR-0001: Modular monolith with a separate GPU worker

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Aymen

## Context

The system has an HTTP API (meetings, transcripts, review, auth), a frontend, and heavy speech
processing that needs a GPU and runs for minutes to hours per recording. It is built and run by
one engineer. The GPU is the only scarce resource: one RTX 3060 Ti (8 GB) in production-like
use, a GTX 1050 (3 GB) for development.

Microservices are often proposed for systems like this. Their benefits are independent
deployment by separate teams and independent scaling. There is one team (one person), and the
only component with different scaling needs is the GPU work.

## Decision

Build the API as a **modular monolith**: one FastAPI application with strict internal module
boundaries (`meetings`, `transcripts`, `review`, `jobs`, `auth`). Modules communicate only
through each other's `service.py`; they never import another module's models or repository, and
never query another module's tables.

Run speech processing in **one separate worker process** (`apps/worker`) that takes jobs from a
Redis queue and processes **one GPU job at a time**.

The API never touches the GPU. Both processes share Postgres and object storage.

## Consequences

**Positive**
- One codebase, one deploy for the API; debugging stays local and simple.
- GPU work is isolated: a crash or out-of-memory error in the worker cannot take down the API.
- The worker can move to another machine (or a cloud GPU) without changing the API.
- Module boundaries keep a future split possible if a real reason appears.

**Negative**
- Module boundaries are enforced by convention and review, not by the network. A lint rule or
  import-checking test should back them up.
- The API and worker share a database schema; migrations must stay compatible with both.

## Alternatives considered

- **Microservices per domain** (meetings, transcription, review, auth): rejected. Adds network
  calls, distributed tracing and deployment overhead with no team or scaling benefit.
- **Everything in one process, GPU work in background threads:** rejected. A model crash or
  memory spike would take down the API, and long jobs would compete with request handling.
