# YouTube acquisition via yt-dlp, defensive and cached

We acquire YouTube metadata, captions, and audio exclusively through
**yt-dlp**, treated as an unreliable upstream and defended against — not
through the official YouTube Data API for transcript/metadata acquisition.

## Why yt-dlp

- Single tool covers single-video + playlist enumeration, metadata (title,
  duration, thumbnail), captions, and audio download (for the Whisper
  fallback) — the YouTube Data API gives metadata only, not captions, and its
  `captions.download` endpoint is in practice restricted to the video owner.
- No API key, no quota, no Semantic Scholar-style rate limit to design around.

## Posture (the point of this ADR)

The pipeline does not treat YouTube as a reliable DB; it treats it as a flaky,
adversarial source:

- **Aggressive caching** — once a Video's metadata/captions/transcript are
  fetched, they live in Postgres (metadata/transcript text) + MinIO (raw
  caption payload, audio). Re-ingests, re-plans, re-embeds, and re-summarizes
  never re-hit YouTube.
- **Per-fetch retry + DLQ** — per ADR-0001, every source/caption/audio fetch
  sits behind retry + dead-letter; transient yt-dlp breakage degrades
  gracefully instead of crashing a Course.
- **Unsupported-source handling** — age-restricted, members-only, region-
  locked, live/premiere, and refused-duration videos resolve to an explicit
  `unsupported_source` terminal state on the saga, surfaced to the User; they
  are not retried infinitely.
- **yt-dlp pinning + scheduled auto-update** — a pinned version runs in prod;
  CI runs a scheduled smoke test that re-resolves a known stable URL weekly
  and bumps the pin when it breaks.

## ToS posture (deliberate, not ignored)

YouTube ToS restricts automated extraction. The product's position is that it
operates on content the user is licensed to watch, caches at the user's
request, and surfaces this as a product disclaimer. This is a risk accepted at
the product level, not a technical mitigation — recorded here so it isn't
rediscovered as a surprise.

## Consequences

- **Single source of truth = Postgres/Weaviate**; YouTube is never queried at
  request time (summary/Q&A/progress all read from cache).
- **Re-ingest triggers** (caption upgrade, transcript re-chunk, model swap)
  erode the cache benefit; they are explicit and rare.
- **Failure surfaces** users see: `unsupported_source`, `transcript_unavailable`
  (caption missing AND Whisper exhausted), `source_fetch_failed` (transient).

## Considered options

- **yt-dlp defensive + cached** — chose this.
- **YouTube Data API for metadata + a separate captions path** — rejected;
  fragments tooling for captions that the API can't actually serve.
- **Isolated ingest-gateway microservice** — deferred; a service boundary can
  be introduced later if ToS/ops blast radius demands it, without changing
  the cache+saga+retry shape.
