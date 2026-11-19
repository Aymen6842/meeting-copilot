# ADR-0002: Local-first transcription behind a provider interface

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Aymen

## Context

Speech recognition can run locally on a GPU (Whisper via faster-whisper, Cohere Transcribe,
Voxtral, Qwen3-ASR, FarukSTT for Tunisian Derja) or through a hosted API. The speech-model
landscape changes quickly, and the stage 1 benchmark must compare several models on the same
audio.

Customer audio is sensitive. "Audio never leaves your infrastructure" is a meaningful property
for enterprise customers. Local inference is also free to run while iterating on hundreds of
test recordings.

The unclear-segment feature needs **word-level timestamps and confidence**. Not every model or
API exposes both.

## Decision

Define two interfaces in `apps/worker/providers/`:

```python
class TranscriptionProvider(Protocol):
    name: str
    def capabilities(self) -> ProviderCaps: ...
    def transcribe(self, audio: AudioChunk, opts: TranscribeOptions) -> TranscriptResult: ...

class DiarizationProvider(Protocol):
    def diarize(self, audio: AudioFile, opts: DiarizeOptions) -> list[SpeakerTurn]: ...
```

- **Local GPU is the default.** A hosted API is supported as a baseline and fallback.
- Every provider declares its capabilities. If it lacks word-level confidence, unclear-segment
  detection degrades to segment level and the UI says so.
- Nothing outside `apps/worker/providers/` imports a model library directly.
- Extra providers for development: `fake` (deterministic, for CI) and `cached` (replays saved
  transcripts so UI work never waits on a GPU).
- Which provider and model to use comes from configuration profiles (`ci`, `laptop`, `desktop`),
  never from code.

## Consequences

**Positive**
- Models can be swapped and benchmarked without touching the pipeline.
- One shared contract test suite runs against every provider.
- CI runs without a GPU.
- Switching machines is a config change.

**Negative**
- The interface must cover the lowest common denominator; provider-specific features need
  explicit capability flags.
- Each provider needs a small adapter to map its output into `TranscriptResult`.

## Alternatives considered

- **Hosted API only:** rejected. Audio leaves the machine, costs grow with every test run, and
  word-level confidence is not guaranteed.
- **Hard-code faster-whisper:** rejected. Blocks the benchmark and ties the project to one model
  family in a fast-moving field.
