# Setting up Google login in Zitadel (LearnPilot dev)

A complete walkthrough if you've never touched Zitadel before. We'll:

1. Create a Google Cloud OAuth project.
2. Register Google as an **identity provider** in Zitadel.
3. Create a Zitadel **OIDC application** for the Next.js web frontend.
4. Test the full flow end-to-end.

Time: ~20 minutes. You'll need a Google account and admin access to your
local Zitadel instance (the one running on `http://localhost` from compose).

---

## 0. Concepts first (Zitadel cheat sheet)

Zitadel is a **identity provider** (IdP) — it manages user accounts, issues
JWTs, and can federate logins through other providers (like Google). You will
not write any auth code; the Next.js frontend talks to Zitadel, Zitadel
hands back a JWT, and the JWT is sent to KrakenD, which validates it and
forwards trusted headers to backend services (per ADR-0009, 0010).

Three Zitadel objects you need to know:

| Object | What it is | Where you set it up |
|---|---|---|
| **Instance** | The whole Zitadel deployment. One per project. URL: `http://localhost` for our dev setup. | `http://localhost/ui/console/instance` |
| **Organization** | A tenant inside the instance. The first one is auto-created during Zitadel startup (we called it `LearnPilot`). | `http://localhost/ui/console/orgs` |
| **Project** | A container for applications within an organization. | Inside an org, click "Projects" → "New" |
| **Application** | An OIDC client (e.g., your Next.js frontend) that gets a `client_id` and `client_secret`. | Inside a project → "Applications" → "New" |

Users exist in an organization. We created one admin user at startup
(`zitadel-admin@learnpilot.localhost`, password `Password1!` per `.env`).
The login name format is `<username>@<org-name>.<external-domain>`, all lowercased.

> **The login screen is at `http://localhost/ui/login`.** The console (admin
> UI) is at `http://localhost/ui/console`. They're different things.

---

## 1. Create a Google Cloud OAuth project

You'll do this in the Google Cloud Console, separate from Zitadel.

### 1.1 Pick or create a project

1. Open https://console.cloud.google.com/cloud-resource-manager.
2. Click **"Create Project"** → name it `LearnPilot Dev` → Create.
3. Wait for the project to provision, then open it (click the project name in the
   top bar and select it from the picker).

### 1.2 Configure the OAuth consent screen

This is the "Sign in with Google" screen users see. Required before you can
create OAuth credentials.

1. Go to **APIs & Services → OAuth consent screen** (left sidebar).
2. User type:
   - **External** — any Google account can log in. Choose this unless you have
     a Google Workspace org and only want Workspace users.
   - **Internal** — only your Workspace org can log in.
3. Fill in:
   - **App name:** `LearnPilot Dev`
   - **User support email:** your email
   - **Developer contact email:** your email
4. **Scopes** step: click "Add or Remove Scopes", add:
   - `openid`
   - `.../auth/userinfo.email`
   - `.../auth/userinfo.profile`
5. **Test users** step: add your own Google email. While the consent screen is
   in "Testing" mode, only test users can log in — you don't need to publish
   for local dev.
6. **Summary** → Back to Dashboard. Status should now say "Testing".

> **You can stay in Testing mode forever for local dev.** Publishing to
> "In Production" requires Google verification (~weeks). For dev with a few
> known test users, Testing is fine.

### 1.3 Create the OAuth 2.0 Client

1. Go to **APIs & Services → Credentials**.
2. Click **"+ CREATE CREDENTIALS"** → **"OAuth client ID"**.
3. Application type: **Web application**.
4. Name: `LearnPilot Zitadel IdP` (or whatever you want).
5. **Authorized redirect URIs** — add EXACTLY this one for local dev:

   ```
   http://localhost/idps/callback
   ```

   > **Critical:** no trailing slash, exact scheme (`http` not `https` since
   > dev is plain), exact host (`localhost`), exact path. Google rejects
   > mismatches.
6. Click **Create**. The modal shows the **Client ID** and **Client secret**.
   Copy both immediately — the secret is only shown once.

   If you lose the secret, delete the credential and create a new one.

**Save these two values somewhere — you'll paste them into Zitadel in step 2.**

---

## 2. Register Google as an identity provider in Zitadel

Back in the Zitadel console at `http://localhost/ui/console`.

### 2.1 Log in

- Login name: `zitadel-admin@learnpilot.localhost` (or whatever
  `ZITADEL_FIRSTINSTANCE_ORG_HUMAN_USERNAME`/`_NAME`/`_EXTERNALDOMAIN` are
  in your `.env`).
- Password: the `ZITADEL_ADMIN_PASSWORD` from `.env`.
- Login name format reminder: `<username>@<org>.<domain>`, all lowercased.
- If login fails, double-check the case and the domain suffix.

### 2.2 Open the Google IdP template

1. In the top org switcher, make sure you're in the **`LearnPilot`** org
   (the one created at first-instance setup).
2. Left sidebar → **Identity Providers** (under Settings).

   > **If you don't see Identity Providers:** your user might not be an
   > org admin. The first-instance admin is; if you created a different
   > user, grant it the `IAM Owner` role on the org.

3. The Identity Providers page lists existing providers and **templates**
   for unconfigured ones. Find **Google** in the templates table and click it.

### 2.3 Copy the Zitadel callback URL

The Google template form has a **ZITADEL Callback URL** field with a
copy-to-clipboard icon. Click it.

For our local dev setup, that URL is:

```
http://localhost/idps/callback
```

> **Why `/idps/callback`?** With Login v2 enabled (which our compose sets
> via `ZITADEL_DEFAULTINSTANCE_FEATURES_LOGINV2_REQUIRED=true`), Zitadel
> uses a single callback endpoint for all external IdPs. The console's
> copy button gives you the exact value; the path is `/idps/callback`.
>
> If you ever set up Login v1 instead, the path would be
> `/ui/login/login/externalidp/callback`. We're on v2.

> **This URL must match the redirect URI you added in Google Cloud in
> step 1.3, character-for-character.** If they differ, Google will reject
> the callback.

### 2.4 Fill in the Google IdP form

On the same Google template form:

- **Client ID:** paste from step 1.3.
- **Client secret:** paste from step 1.3.
- **Scopes:** leave the prefilled `openid profile email` (or add more if you
  need them).
- **Automatic creation:** ✅ enabled (creates a Zitadel user on first Google
  login).
- **Automatic update:** ✅ enabled (updates the Zitadel user if their Google
  profile changes).
- **Account creation allowed:** ✅ enabled. (If you disable this, login
  fails for users without a pre-existing Zitadel account.)
- **Account linking allowed:** optional. Enable if you want users with
  existing Zitadel passwords to be able to link their Google login.
  (Either account creation OR linking must be enabled — they're alternatives.)

Click **Create**. The provider appears in the providers list.

### 2.5 Activate the provider

In the providers list, the new Google row has a tick icon labeled
**"set as available"**. Click it. Now the Google button will show on the
login screen.

### 2.6 Allow external IdPs in the login policy

This is the easy step to miss — without it, the Google button doesn't appear
even though the provider is "active".

1. Left sidebar → **Login Behavior and Security** (under Settings).
2. Toggle **"External Login allowed"** to **on**.
3. Save.

For an organization-scoped policy, the path is
`http://localhost/ui/console/org-settings?id=login`. For the default
instance policy, it's `http://localhost/ui/console/instance?id=login-policy`
(use the top instance switcher).

### 2.7 Test the Google button

Open `http://localhost/ui/login` in an **incognito** window. You should
see a "Continue with Google" button. Click it — you should be sent to
Google, prompted to choose an account, redirected back to Zitadel, and
landed on the login-success page.

If the Google button isn't there → check step 2.6 (login policy).
If Google says `redirect_uri_mismatch` → check that the URL in step 1.3
matches the one in step 2.3 character-for-character.
If the Zitadel login succeeds but no User row appears in identity-service
yet — that's fine, identity-service creates the row on the first `/v1/me`
call (find-or-create).

---

## 3. Create a Zitadel OIDC application for the Next.js web frontend

Zitadel will issue JWTs to the web frontend. To do that, you need an OIDC
application. We need one for the Next.js frontend now; you'll add others
later (e.g., a CLI/mobile app).

### 3.1 Create a project

1. In the top org switcher, ensure you're in the **`LearnPilot`** org.
2. Left sidebar → **Projects** → **New**.
3. Name: `LearnPilot Web` (or whatever).
4. After creating, click into the project.

### 3.2 Add an application

1. In the project, **Applications** → **New**.
2. The wizard asks for a name and type:
   - **Name:** `LearnPilot Web Frontend` (or similar).
   - **Application type:** **Web**.
   - **Authentication method:** **PKCE** (Proof Key for Code Exchange) —
     the secure default for server-side web apps. Authorization Code + PKCE.
3. Click **Create**.
4. The next screen shows the **Client ID**. Click **Copy**.

   > You do **not** need the client secret on the frontend — PKCE
   > authenticates with a one-time code challenge. The client_secret field
   > is for confidential clients; with PKCE on a Web app, Zitadel doesn't
   > issue one and you don't need it.
5. Click **Continue** to go to the detail page.

### 3.3 Add redirect URIs

On the application's detail page, scroll to **Redirect URIs** and add:

```
http://localhost:3000/api/auth/callback/zitadel
http://localhost:3000
http://localhost:3000/*
```

The first is the NextAuth (Zitadel provider) callback — NextAuth will catch
the auth code there and exchange it for tokens. The second and third are
common variations some setups need. You can add more later.

> **Save the Client ID** — you'll put it in `web/.env.local` as
> `ZITADEL_CLIENT_ID` when you wire NextAuth.

### 3.4 (Optional) Configure the post-logout redirect

If you want logout to send users back to the home page:

- **Post Logout Redirect URIs:** `http://localhost:3000`

### 3.5 Configure token claims for backend services

This is what makes KrakenD's `propagate_claims` work. The Zitadel-issued
JWT must include `sub` (always there) and `email` (default-scoped, usually
present). To be sure:

1. On the application, **Token Settings** tab.
2. **Add user info to access token:** if your backend services read claims
   from the access token (KrakenD does for validation), turn this on.
3. **User info inside ID token:** usually on by default; verify.

You can also add custom claims if your frontend passes extra context to
Zitadel. For now, defaults are fine.

---

## 4. Wire the secrets into the project

### 4.1 Google secrets → `.env` (compose)

If you want the Zitadel Google IdP config to come from `.env` instead of
the console, you'd automate this with the Zitadel Admin API
(`/idps/google` endpoint). For dev, the console is fine. The `.env`
placeholders exist for that API path:

```bash
GOOGLE_CLIENT_ID=...        # from step 1.3
GOOGLE_CLIENT_SECRET=...    # from step 1.3
```

### 4.2 Zitadel app client_id → Next.js `.env.local`

In `web/.env.local` (or however the frontend reads env):

```
ZITADEL_ISSUER=http://localhost
ZITADEL_CLIENT_ID=<from step 3.2>
ZITADEL_CLIENT_SECRET=<leave blank for PKCE>
NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=<random 32+ char string>
```

The web frontend (not built yet) will use these to wire NextAuth's Zitadel
provider.

---

## 5. End-to-end test

With everything wired:

1. Open `http://localhost:3000` in incognito.
2. Click "Sign in with Google".
3. Complete Google login.
4. NextAuth exchanges the auth code at
   `http://localhost:3000/api/auth/callback/zitadel` and gets a Zitadel
   access token + id_token.
5. The frontend calls `http://localhost:8080/api/identity/v1/me` with
   `Authorization: Bearer <jwt>`.
6. KrakenD validates the JWT against Zitadel's JWKS, injects
   `X-User-Id` (from `sub`) and `X-User-Email` (from `email`).
7. identity-service upserts the User row and returns the profile.

If step 6 fails, check the KrakenD logs:
```bash
docker logs learnpilot_krakend
```
The most common issues are issuer mismatch (KrakenD's `issuer` field must
exactly match Zitadel's reported issuer, which is `http://localhost` for
our setup) or audience/client_id mismatch (we don't enforce `audience`
in KrakenD config yet, so this shouldn't bite you).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Google login shows `redirect_uri_mismatch` | The redirect URI in Google Cloud doesn't match Zitadel's. They must be byte-identical, including scheme and trailing slash. |
| No Google button on login screen | Login Policy has "External Login allowed" off. Enable it. |
| Google login returns to Zitadel but login fails | The IdP isn't "active" (no tick). Set it as available in the providers list. |
| Google login returns 403 "Account creation not allowed" | Enable "Account creation allowed" in the IdP config. |
| Zitadel redirect URI in console is different from what I added in Google | Stop and reconcile — they must match. The console's "ZITADEL Callback URL" copy button is authoritative. |
| `ZITADEL_FIRSTINSTANCE_LOGINCLIENT_PAT_EXPIRATIONDATE` parse error | A YAML/env issue on the Zitadel container, not the IdP config. Unrelated; check `docker logs learnpilot_zitadel_api`. |
| KrakenD logs `issuer mismatch` | The `issuer` array in `gateway/krakend.json` doesn't match the issuer in `curl http://localhost/.well-known/openid-configuration`. For our dev setup it should be `["http://localhost"]` — no port (see the Zitadel gateway skill README §5 for why). |
| Frontend gets 401 from `localhost:8080` | Token's `aud` doesn't match what KrakenD expects, or the token is expired. Try with a fresh `access_token`. |

---

## What's next

- Add `web/` (Next.js) when the frontend is in scope. It will use
  `next-auth` with the `Zitadel` provider, taking `ZITADEL_CLIENT_ID` from
  step 3.2.
- Add more IdPs if needed (GitHub, Microsoft) using the same procedure.
- Move the Zitadel IdP config out of the console into the Admin API +
  compose bootstrap, so a fresh `docker compose up` creates the IdP from
  the env vars in `.env`. That's an INF-6 follow-up.
