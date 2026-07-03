# Weaviate as the vector store for transcript RAG

We use **Weaviate** (standalone service) as the vector store for transcript
chunk embeddings, not the pgvector Postgres extension.

Postgres remains the single source of truth for the relational domain (Owner,
Source, Course, Lesson, Plan, Day, Progress, Note, IngestionSaga) and for
transcript + note text. Weaviate owns only the chunk text + vectors used by
Q&A retrieval, referenced back to Postgres Video/Lesson ids.

## Why Weaviate over pgvector

pgvector was the simpler option (one DB, one backup, transactional with the
rows it describes), and at projected platform scale it would technically
suffice. We picked Weaviate for its purpose-built hybrid (vector + keyword)
search, richer query semantics, and headroom to scale the retrieval subsystem
independently of the transactional store — accepting the operational cost of a
second service and a sync boundary.

## Consequences

- **Two stores, one source of truth.** Postgres holds canonical Video/Lesson
  rows; Weaviate holds derived chunks keyed by Video id. Weaviate is a
  projection, never the system of record.
- **Sync obligation.** On Whisper re-run or caption upgrade, the Embed stage
  must delete-by-Video-id in Weaviate before inserting the new chunks. On Video
  / Course delete, a delete must cascade to Weaviate. This is enforced inside
  the ingestion consumers + a delete handler, not left to callers.
- **Chunking + embedding model + Weaviate schema** are decided separately
  (chunk size, overlap, model, collection shape).

## Considered options

- **Weaviate standalone** — chose this.
- **pgvector inside Postgres** — rejected for now; revisit only if the
  second-service cost stops being worth the hybrid-search benefit.
