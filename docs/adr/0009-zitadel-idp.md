# Zitadel as the self-hosted identity provider

We use **Zitadel** (self-hosted, Go-based, OIDC issuer) as the IdP, not
Keycloak. Google login is configured as an identity provider inside Zitadel;
the canonical user identity is the Zitadel OIDC `sub`.

## Why Zitadel over Keycloak

Keycloak was the original candidate but rejected on footprint and ops: JVM
runtime, ~512MB+ idle RAM, heavier upgrade/realm story than this single-VPS
stack wants. Zitadel is Go-based with a materially smaller runtime footprint,
a modern admin UX, native OIDC, Google as a first-class social identity
provider, and an actively-maintained self-host path — same capabilities that
mattered here (OIDC issuance, Google login, JWKS for gateway validation) at
lower ops cost.

## Why not no-IdP (next-auth only)

The microservices shape (ADR-0008) wants a central OIDC issuer that every
service + KrakenD can validate against uniformly. Pushing token issuance into
next-auth alone would couple all backend services to the frontend's session
shape and remove the central issuer KrakenD validates. Zitadel keeps identity
outside any one service.

## Logistics

- Runs in the Compose stack on the private Docker network; its data lives in
  the shared Postgres (a dedicated `zitadel` DB) or its bundled DB — decided
  at implementation time, default shared Postgres.
- Google configured as an identity provider inside Zitadel; product login UX
  is "Sign in with Google" via Zitadel's OIDC flow consumed by next-auth.
- KrakenD validates Zitadel-issued JWTs against Zitadel's JWKS endpoint at the
  edge (ADR-0010).
- `identity-service` find-or-creates a local `User` row keyed by the Zitadel
  `sub` on first login; it never stores tokens.

## Consequences

- One JVM avoided; ~one extra Go service + a DB for its store.
- Adding a second login provider (GitHub, email, etc.) is a Zitadel config
  change, not a code change across services.
- Zitadel version upgrades are an ops task, lighter than Keycloak's but real.

## Considered options

- **Zitadel** — chose this.
- **Keycloak** — rejected; heavier JVM footprint/ops for the same must-have
  capabilities.
- **next-auth only (no IdP)** — rejected; no central issuer for a
  multi-service backend to validate uniformly.
- **Ory Kratos + Hydra / Logto / Dex** — considered; Zitadel picked on the
  balance of footprint, admin UX, and Google-provider support.
