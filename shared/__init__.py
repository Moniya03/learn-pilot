"""LearnPilot cross-service contracts (ADR-0013).

Shared is imported via PYTHONPATH, not pip. Each service's Dockerfile
sets `ENV PYTHONPATH=/app` and copies `shared/` to `/app/shared/`; local dev
uses `PYTHONPATH=<repo root>` so `import shared` resolves from the repo root.

Keep this package to genuine contracts: header dependency, base settings,
event/command envelopes, common client adapters. No business logic.
"""