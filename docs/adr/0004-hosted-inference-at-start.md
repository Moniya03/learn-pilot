# Hosted inference at start; self-host later

At launch (dev + initial prod) the heavyweight inference goes to **hosted
APIs**, not self-hosted models on the VPS:

- **Whisper transcription** → Groq's Whisper API (`whisper-large-v3`).
- **LLM** (summarize, Q&A synthesis) → a Groq-hosted model, called through the
  LiteLLM client so the model/provider is pure config.
- **Embeddings** (multilingual-e5-large) → the Hugging Face Inference API.

The self-hosted Docker-Compose stack (Postgres, RabbitMQ, Weaviate, API,
workers, web) therefore needs **no GPU**; only the `whisper` / `embed` / `llm`
clients make outbound calls.

## Why

Keeps the VPS footprint small and ops simple while proving the pipeline, with
per-call cost as the trade-off. LiteLLM + a thin embeddings client mean a
later migration to self-hosted (sentence-transformers for e5, local Whisper,
or a self-hosted LLM) is a config/adapter swap in each worker, not an
architectural change.

## Consequences

- **Secrets**: Groq + HF API keys in env, not bundled in images.
- **Cost ceiling**: large course bursts cost money; the auto-split/auto-ingest
  flows should be rate-aware to avoid runaway embed/whisper spend.
- **Single point of egress**: a hosted-provider outage pauses ingestion stage
  consumers; retries + DLX (per ADR-0001) absorb transient failures, sustained
  outages degrade gracefully (transcript/summary unavailable, video still
  watchable/trackable).
- **Migration path**: each heavy stage already wraps its call behind an
  interface in `shared/`; swapping to self-host = replace that adapter.

## Considered options

- **Hosted at start, self-host later** — chose this.
- **Self-host everything from day one** — rejected; GPU hosting cost + ops
  before the product proves out.
- **Fully managed PaaS for the whole stack** — rejected; couples to vendor and
  raises baseline cost vs a single VPS.
