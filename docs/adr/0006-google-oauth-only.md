# Authentication: Google OAuth only (SUPERSEDED)

**Status: superseded by ADR-0008, ADR-0009, ADR-0010.** The architecture
pivoted to microservices with a KrakenD gateway and Zitadel as the IdP; auth
is no longer a single-provider stateless-JWT-in-FastAPI design. Kept for
history.

----

Authentication is **Google OAuth only** — no password, magic-link, SSO, or
multi-provider identity layer. A client obtains a Google ID token (OIDC),
sends it to the API, the API validates the JWT against Google's published
JWKS, then find-or-creates a Local `User` row keyed by the Google `sub`. No
registration/password/reset/lockout flows exist; account deletion = remove the
Google account.

## Why Google-only

- Self-hosted frontend via Next.js `next-auth` Google provider handles the
  entire token dance; the backend is a stateless JWT validator over Google's
  public JWKS.
- Owner identity (the only auth requirement of ADR-related multi-user data)
  flows from one JWT claim — no broker, no IdP service, no realm/DB to operate.
- Scope creep rejected: password/magic-link reauth + their storage/reset/lock
  churn were not worth the surface for a single-provider audience.

## Consequences

- **No keycloak/krakend.** KrakenD gateway is unnecessary; FastAPI's own
  middleware (single python-jose/fastapi-jwt-auth dependency) validates token
  signatures and claims.
- **Trust boundary.** Google's `email_verified` claim is treated as
  authoritative; no local email verification is run again.
- **Refresh-token storage rejected.** The server stores no refresh tokens; the
  frontend refreshes Google access directly. The DB only stores the `User` row
  (google_sub, email, display_name, avatar_url), no secrets.
- **Fallback for users without a Google account is itself** — anyone who wants
  access signs up with a Google account; it's effectively the open-registration
  door.
- **Reversibility.** Adding a second provider is a frontend `next-auth`
  provider entry; backend JWKS logic is provider-agnostic.

## Considered options

- **Google OAuth only** — chose this.
- **Keycloak + KrakenD** — rejected; enterprise IdP + gateway machinery overshoots a single-provider OIDC setup a stateless JWT validator already covers.
- **Magic-link JWT (self-rolled)** — rejected; reimplements what Google already handles (email verification, token issuance, refresh).
