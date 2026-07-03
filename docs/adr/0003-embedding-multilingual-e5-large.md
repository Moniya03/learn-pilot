# Embedding model: intfloat/multilingual-e5-large

The Embed pipeline stage embeds transcript chunks with
**`intfloat/multilingual-e5-large`** (1024-dim vectors), stored as precomputed
vectors in Weaviate.

## Why this model

Course transcripts routinely mix languages (e.g. Gate Smashers style
Hindi-English-code-switched content), so a multilingual model materially beats
English-only embeddings for retrieval relevance here. e5-large is a strong
open multilingual retriever.

## Logistics

- **Chunking (paired baseline)** — token-bounded chunks (~512 tokens, ~64
  overlap), each carrying `[start, end]` timestamps so Q&A answers can cite a
  moment to seek to. Stored Weaviate props: `text`, `vector`, `course_id`,
  `video_id`, `chunk_index`, `start`, `end`.
- **Server** — run via a self-hosted `sentence-transformers` service (or HF
  Inference API for dev) that the Embed consumer calls; Weaviate stores the
  precomputed vectors (no Weaviate-native vectorizer).
- **E5 prefixes** — query passages must be prefixed `passage: ` and search
  queries `query: ` per the model's convention, or recall drops.
- **Re-embed cost** — changing the model means re-embedding every transcript
  chunk for every Course. Treat as expensive: gate swaps behind a real reason.

## Considered options

- **intfloat/multilingual-e5-large** — chose this.
- **OpenAI text-embedding-3-small** — cheaper, no hosting, but English-biased
  for mixed-language transcripts.
- **Local nomic-embed-text / bge-small via Ollama** — smaller, lighter, but
  weaker multilingual coverage.
