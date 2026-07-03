# MinIO as the object store for ingestion artifacts

We use **MinIO** (self-hosted, S3-compatible) on the same VPS as the rest of
the Compose stack, for ingestion artifacts only: downloaded audio (for the
Whisper stage), raw caption payloads (cached, keyed by video_id so re-fetches
are cheap), and raw transcript JSON (so re-chunking / re-embedding doesn't
re-fetch from YouTube). One bucket per concern, with lifecycle/TTL: audio is
ephemeral (delete after transcription), captions + transcripts are kept for
replay.

## Why MinIO over alternatives

- **Self-hosted S3-compatible** matches the Deploy stack choice (Compose on
  VPS, ADR-0004) — no separate managed-store account, no egress cost, full
  local reproducibility with the same S3 API in dev.
- **Cloud S3** — rejected at start; billed per-GB + egress and adds an account
  before the product proves out. Swap is a config change (S3-compatible), so
  not a lock-in.
- **Postgres blobs (bytea)** — rejected for audio (multi-MB / large) and
  transcript payloads (KB–tens of MB) to avoid bloating the transactional
  store and backups.

## Consequences

- Audio is **scratch** — deleted post-transcription; caption/transcript buckets
  are the replay/cache surface, kept.
- MinIO is an **implementation artifact**, not a domain concept — it does not
  appear in `CONTEXT.md`. Workers reference artifacts by bucket + key; Postgres
  stores only the `artifact_uri` + checksum, never the bytes.
- Bad-video / failed-stage cleanup must cascade to MinIO (delete audio) on
  `VideoFailed` and on Course delete, alongside the Weaviate sync in ADR-0002.
