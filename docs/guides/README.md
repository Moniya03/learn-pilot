# Guides

Step-by-step setup and operation guides for LearnPilot. Read these when you're
doing the actual hands-on work; the ADRs (`../adr/`) explain *why* a choice was
made, the plans (`../plans/`) describe what will be built.

## Setup

- [Zitadel Google login setup](zitadel-google-setup.md) — create a Google
  Cloud OAuth project, register Google as an IdP in Zitadel, and create the
  OIDC application the Next.js frontend will use. Start here if you've never
  used Zitadel.

## Add when needed

- Running migrations (per-service `migrate.py` reference)
- Wiring a new service into KrakenD
- Adding a new external IdP (GitHub, Microsoft)
- Promoting the Zitadel config out of the console into the Admin API
