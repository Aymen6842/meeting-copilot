# ADR-0003: Integer millisecond timestamps and word-level storage

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Aymen

## Context

Every later feature depends on linking text back to audio: click a word to play it, replay an
unclear span, and (from stage 2) trace a requirement or recommendation back to the exact moment a
customer said it. Speech models return timestamps as floating-point seconds, and different
models round differently. Long recordings are processed in overlapping chunks whose results must
be merged without losing or duplicating words.

## Decision

- All audio positions are stored as **integer milliseconds from the start of the meeting**
  (`start_ms`, `end_ms`). No floats, no wall-clock times.
- Provider adapters convert model output to integer milliseconds at the boundary, adding the
  chunk's offset.
- **Words are stored as rows** (`Word`: segment_id, start_ms, end_ms, text, confidence), not only
  as text inside segments. Replay, highlighting and review items work at word level.
- Segments are appended, never edited in place, and carry a status (`draft` or `final`) so live
  mode can reuse the same model.

## Consequences

**Positive**
- Exact comparisons and sorting; no floating-point drift when merging chunks.
- Property tests can assert simple invariants: timestamps never go backwards, no word lost or
  duplicated across chunk boundaries.
- Any future claim can point to a precise audio span.

**Negative**
- Many more rows (roughly 8,000–10,000 words per hour of speech). Needs indexes on
  `Segment (run_id, start_ms)` and `Word (segment_id, start_ms)`; acceptable for Postgres at
  this scale. A time-range lookup for a run finds the segments first, then their words.
  `Word` deliberately has no `run_id` column: it would duplicate data that `segment_id`
  already implies and could drift out of sync.
- Millisecond precision is finer than model accuracy; that is fine, but it must not be presented
  as accuracy.

## Alternatives considered

- **Float seconds:** rejected. Rounding differences break de-duplication and equality checks.
- **Segment-level storage only (words as JSON):** rejected. Makes word-level review, queries and
  evidence links awkward and slow.
