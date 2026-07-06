# Backend services

Narrative docs about how the backend fits together and what each service does.
Specs and task lists live in `docs/plans/`; architectural decisions in
`docs/adr/`; domain language in `CONTEXT.md`. These docs explain the *why* and
the *shape*, with the current code as the source of truth.

- **00-backend-services-and-final-shape.md** — what every backend service does,
  why it is a separate process, how services talk (RabbitMQ events vs HTTP),
  the full request lifecycle, and the deployable shape once all plans land.
- **01-catalog-service.md** — catalog-service deep dive: role, data model,
  REST API, messaging, plan worker, config, and source map.

(future: 02-ingestion-service.md, 03-ai-service.md, 04-notes-service.md,
05-identity-service.md as each is implemented.)