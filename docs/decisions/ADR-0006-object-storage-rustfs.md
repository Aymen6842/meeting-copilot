# ADR-0006: RustFS as the local S3-compatible object storage

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Aymen

## Context

Original audio, normalised copies and derived artifacts live in object storage, never in
Postgres. The stage 1 spec named MinIO for local and self-hosted use. While building the Docker
Compose stack (milestone 1, task 2), the official MinIO images turned out to be unavailable:
`minio/minio` and `minio/mc` no longer exist on Docker Hub, and the community edition is in
maintenance mode without published images.

The application needs only basic S3: put, get, head and delete objects, a private bucket, and
short-lived pre-signed upload and download URLs. Storage access goes through one module
(`core/storage.py`) using the standard S3 API, so the server behind it is replaceable.

## Decision

Use **RustFS** (`rustfs/rustfs`, Apache-2.0, pinned version) as the S3-compatible object storage
in Docker Compose, in integration tests, and for self-hosting. In the cloud, any S3-compatible
service can be used instead; the code does not change.

- The application talks to storage only through the S3 API (boto3-compatible client), never a
  vendor SDK or admin API.
- A one-shot `storage-init` service creates the bucket with the AWS CLI. Buckets are private by
  default and no policy grants anonymous access.
- The web console's cross-origin setting is restricted to the local frontend.

Verified before deciding (RustFS 1.0.6): health endpoints return 200, a new bucket has no
policy, anonymous list and get return 403, a pre-signed GET works while valid and returns 403
after expiry.

## Consequences

**Positive**
- Official, maintained images; one container with a MinIO-like setup, so the spec barely changes.
- Apache-2.0 licence: no AGPL obligations.
- Swapping storage later is a compose and configuration change, not a code change.

**Negative**
- RustFS is a young project (1.0 released recently) with a shorter track record than MinIO or
  SeaweedFS. Pin the version and run the storage integration tests on every upgrade.
- Testcontainers has no dedicated RustFS module; integration tests use a generic container.

## Alternatives considered

- **MinIO:** no official images; would mean building from source and tracking security fixes
  ourselves. AGPL licence.
- **SeaweedFS:** mature and Apache-2.0, but several roles (master, volume, filer, S3 gateway)
  make the compose setup more involved. The best fallback if RustFS disappoints.
- **Garage:** needs a cluster-layout step even for one node; AGPL licence.
