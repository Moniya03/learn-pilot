# KrakenD edge validation + trusted headers inward

KrakenD validates the Zitadel-issued JWT **once at the edge** (signature,
expiry, audience via Zitadel JWKS), then injects trusted identity headers
into the request it forwards to internal FastAPI services. Internal services
trust those headers blindly and do **not** re-validate the JWT.

## Header contract

KrakenD, after successful validation, sets on the inward request:

- `X-User-Id` — the Zitadel `sub` (canonical user identity; maps to
  `User.owner_id` via identity-service).
- `X-User-Email` — the verified email from token claims.
- `X-Auth-Scopes` — space-delimited scopes/roles for authorization checks.

KrakenD **strips** any client-supplied versions of these headers before
injection, so a client cannot forge identity by sending them inbound.

## Why validate once, trust inward

- Internal FastAPI services are reachable **only** on the private Docker
  network and bind only to that interface; they never expose public ports.
  KrakenD is the sole public entry point.
- Re-validating the JWT in every service would add redundant crypto on every
  inter-service hop and force every service to carry JWKS config/caching —
  for no security gain when the network is private and the edge is the only
  door.
- One validation point = one place to get auth right, one place to observe
  auth failures.

## Consequences

- **Network isolation is a security boundary.** Any service that accidentally
  exposes a port publicly becomes a forgeable-identity hole. Compose network
  config + port publishing discipline is mandatory; a CI check should assert
  no service port is published publicly except KrakenD (and the web frontend).
- Internal services read identity from headers via a shared FastAPI
  dependency (`current_user`), so the trust model is encoded in one place and
  the header names are typed.
- Internal service-to-service calls (one FastAPI calling another on the
  private network) propagate the same headers verbatim — no re-issuance.
- If a service ever needs to call out to the public internet or be exposed
  directly, it must NOT rely on these headers and must re-validate.

## Considered options

- **Edge validates once, trusted headers inward** — chose this.
- **Edge validates + each service re-validates** — rejected; redundant crypto
  on every hop, no added security on a private network.
- **Trust headers for reads, re-validate writes** — rejected; inconsistent
  trust model, most complex.
