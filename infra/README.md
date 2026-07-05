# LearnPilot Infrastructure Setup

## Architecture overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser / Next.js frontend                                      │
│  http://localhost:3000                                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  KrakenD API Gateway (:8080)                                     │
│  - Validates Zitadel JWT on every request                        │
│  - Strips any inbound X-User-Id/X-User-Email/X-Auth-Scopes      │
│  - Injects trusted headers from JWT claims                       │
│  - Routes /api/* → backend services                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌──────────┐      ┌──────────┐      ┌──────────┐
  │ identity │      │ catalog  │      │ notes    │  ...
  │ :8000    │      │ :8000    │      │ :8000    │
  └──────────┘      └──────────┘      └──────────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ▼
                    ┌──────────────┐
                    │  Postgres    │
                    │  (shared)    │
                    │  - learnpilot│  ← app schemas
                    │  - zitadel   │  ← identity provider
                    └──────────────┘
```

**Zitadel** is the identity provider. It manages users, handles Google login, and issues JWTs. The Zitadel console UI is at `http://localhost/ui/console`.

**KrakenD** is the API gateway. It sits in front of all services, validates JWTs from Zitadel, and injects trusted headers (`X-User-Id`, `X-User-Email`) so services never need to parse tokens themselves.

**Flow:** User logs in via Zitadel → gets JWT → frontend sends JWT as `Authorization: Bearer <token>` → KrakenD validates it → forwards to backend with trusted headers.

## Prerequisites

- Docker + Docker Compose
- Google OAuth credentials (Client ID + Secret) from https://console.cloud.google.com/apis/credentials

## Quick start

```bash
cd infra

# 1. Create .env from example
cp .env.example .env

# 2. Generate Zitadel masterkey (32 hex chars = 32 bytes; NOT base64)
echo "ZITADEL_MASTERKEY=$(openssl rand -hex 16)" >> .env

# 3. Fill in Google OAuth credentials in .env
#    GOOGLE_CLIENT_ID=...
#    GOOGLE_CLIENT_SECRET=...

# 4. Start everything
docker compose up -d

# 5. Wait for Zitadel to be healthy (takes ~15-30s first time)
docker compose logs -f zitadel
# Wait for "instance is ready" or similar, then Ctrl+C

# 6. Verify KrakenD health
curl http://localhost:8080/__health
```

## Accessing Zitadel Console

The Zitadel console is the web UI for managing:
- Organizations
- Users
- Applications (OIDC clients for your frontend/API)
- Identity providers (Google login)
- Projects

### URL

```
http://localhost/ui/console
```

### First login

On first start, Zitadel creates an admin user from your `.env`:

| Field | Value |
|-------|-------|
| Username | `zitadel-admin` (or whatever `ZITADEL_ADMIN_USERNAME` is set to) |
| Password | `Password1!` (or whatever `ZITADEL_ADMIN_PASSWORD` is set to) |
| Login hint | `zitadel-admin@zitadel.localhost` |

You'll be forced to change the password on first login.

### What to do in the console

After logging in:

1. **Create a Project** (for your app's OIDC clients):
   - Go to Projects → Create new project → name it `learnpilot`

2. **Create an OIDC Application** (for Next.js frontend):
   - Inside the `learnpilot` project → Applications → New
   - Name: `web`
   - Type: Web
   - Auth method: PKCE (recommended for SPAs/Next.js)
   - Redirect URIs: `http://localhost:3000/api/auth/callback/zitadel` (NextAuth callback)
   - Post logout URIs: `http://localhost:3000`
   - **Save the Client ID** (you'll need it for NextAuth config)

3. **Create an API Application** (for KrakenD JWT validation):
   - Inside the `learnpilot` project → Applications → New
   - Name: `api`
   - Type: API
   - Auth method: Basic (or JWT)
   - **Save the Client ID and Client Secret** (you'll need them for KrakenD audience config)

4. **Note the Issuer URL**:
   - For local dev: `http://localhost`
   - JWKS URL: `http://localhost/oauth/v2/keys`
   - Well-known: `http://localhost/.well-known/openid-configuration`

## Setting up Google IdP

After creating your project in the console:

1. Go to your Organization (top-left dropdown) → Identity Providers
2. Click "New" → Google
3. Enter your Google OAuth credentials:
   - Client ID: from Google Cloud Console
   - Client Secret: from Google Cloud Console
   - Scopes: `openid profile email`
4. Save
5. Go to your project's Login Policy and enable the Google provider

Now users can sign in with Google. Zitadel will create a user record and issue JWTs with the Google user's `sub`, `email`, etc.

## How KrakenD JWT validation works

KrakenD validates every request (except health checks) by:

1. Extracting the `Authorization: Bearer <token>` header
2. Fetching Zitadel's public keys from JWKS URL (`http://zitadel:8080/oauth/v2/keys`)
3. Verifying the JWT signature, expiry, and issuer
4. Extracting claims (`sub`, `email`) and injecting them as headers:
   - `X-User-Id` ← JWT `sub` claim (Zitadel user ID)
   - `X-User-Email` ← JWT `email` claim
5. **Stripping** any inbound `X-User-Id`, `X-User-Email`, `X-Auth-Scopes` from the client (so clients can't spoof identity)
6. Forwarding the request to the backend service

Backend services trust these headers because only KrakenD can set them (it's on the internal network).

## Verifying the setup

```bash
# Check all services are running
docker compose ps

# Check Postgres has both databases
docker compose exec postgres psql -U learnpilot -c "\l"
# Should show: learnpilot, zitadel

# Check Zitadel is responding
curl http://localhost/.well-known/openid-configuration

# Check KrakenD health
curl http://localhost:8080/__health

# Check RabbitMQ management UI
open http://localhost:5552
# user: learnpilot / pass: learnpilot

# Check MinIO console
open http://localhost:9001
# user: minioadmin / pass: minioadmin
```

## Troubleshooting

### Zitadel won't start / "database does not exist"
- The init script (`init-zitadel-db.sh`) creates the `zitadel` database on first Postgres start.
- If Postgres was already started before, the script won't re-run. Delete the volume: `docker compose down -v && docker compose up -d`

### KrakenD returns 401 on all requests
- Check Zitadel is running: `docker compose logs zitadel`
- Check JWKS URL is reachable from KrakenD: `docker compose exec krakend curl http://zitadel:8080/oauth/v2/keys`
- Make sure the issuer in krakend.json matches your Zitadel issuer (`http://localhost`)

### Zitadel console shows "connection refused"
- Make sure Zitadel proxy is on port 80: `docker compose ps`
- Check `ZITADEL_EXTERNALPORT=80` and `ZITADEL_EXTERNALDOMAIN=localhost` in your .env

### Google login doesn't work
- Make sure Google OAuth redirect URI includes: `http://localhost/ui/login/externalidp/callback`
- Check Client ID/Secret are correct in Zitadel console

## File structure

```
infra/
├── docker-compose.yml          # All services
├── .env.example                # Template for .env
├── .env                        # Your actual config (gitignored)
├── postgres/
│   └── init-zitadel-db.sh      # Creates zitadel DB on first start
└── README.md                   # This file

gateway/
└── krakend.json                # KrakenD gateway config
```
