# Snipe-IT License Integration

Fetches licensed users from **Microsoft 365**, **Atlassian** (Jira Cloud) and **Slack**, and reconciles them against **Snipe-IT** license seats — reporting (and optionally applying) who should be checked in/out.

## Setup

```bash
git clone ...
cd snipe_it_integration
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# edit .env with your credentials (see sections below)
```

---

## Microsoft 365 configuration

Authentication uses an **Entra ID (Azure AD) app registration** with a client secret. The script calls the Microsoft Graph API with application permissions (no user login required).

### 1. Register an app in Entra ID

1. Go to [portal.azure.com](https://portal.azure.com) → **Entra ID** → **App registrations** → **New registration**
2. Name it something like `snipe-it-license-sync`
3. Leave **Supported account types** as *Accounts in this organizational directory only*
4. No redirect URI needed → click **Register**

### 2. Create a client secret

1. Open the app → **Certificates & secrets** → **Client secrets** → **New client secret**
2. Set an expiry (e.g. 24 months) → **Add**
3. Copy the **Value** immediately — it is only shown once

### 3. Grant API permissions

1. Open the app → **API permissions** → **Add a permission** → **Microsoft Graph** → **Application permissions**
2. Add all three:
   - `User.Read.All`
   - `LicenseAssignment.Read.All`
   - `Organization.Read.All`
3. Click **Grant admin consent for \<your tenant\>** — this requires a Global Admin

### 4. Collect the required values

Open the app **Overview** page:

| .env variable | Where to find it |
|---|---|
| `MS_TENANT_ID` | **Directory (tenant) ID** on the Overview page |
| `MS_CLIENT_ID` | **Application (client) ID** on the Overview page |
| `MS_CLIENT_SECRET` | The secret value you copied in step 2 |

```dotenv
MS_TENANT_ID=11111111-2222-3333-4444-555555555555
MS_CLIENT_ID=00000000-0000-0000-0000-000000000000
MS_CLIENT_SECRET=your-client-secret-here
```

---

## Atlassian configuration

Authentication uses a **classic (unscoped) Atlassian API token**, sent against the site URL directly (`https://yourcompany.atlassian.net/rest/api/3/...`).

Atlassian's newer **scoped** API tokens (the "API token with scopes" flow) do *not* work here: the `group`/`group/member` endpoints this integration relies on aren't covered by scoped-token support and return `401 Unauthorized; scope does not match` no matter which scopes you grant the token — this was confirmed directly against Atlassian's API, not just documentation. Use a classic token instead.

### 1. Create a classic API token

1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token** → choose the plain **Create API token** option (not "with scopes")
3. Give it a name (e.g. `snipe-it-license-sync`) and set an expiry
4. Click **Create** → copy the token immediately (shown only once)

### 2. Required Jira permissions for the account

The Atlassian account whose email you use must have the **Browse users and groups** global permission in Jira. To verify:

1. Jira site → **Settings (cog)** → **System** → **Global permissions**
2. Confirm the account or its group has **Browse users and groups**

### 3. Set .env values

| .env variable | Value |
|---|---|
| `ATLASSIAN_SITE_URL` | Your Jira Cloud base URL, e.g. `https://yourcompany.atlassian.net` |
| `ATLASSIAN_USER` | Email address of the account that owns the token |
| `ATLASSIAN_API_TOKEN` | The classic token you copied in step 1 |

```dotenv
ATLASSIAN_SITE_URL=https://yourcompany.atlassian.net
ATLASSIAN_USER=admin@yourcompany.com
ATLASSIAN_API_TOKEN=your-classic-token-here
```

Confluence licensing isn't exposed through Jira's `applicationrole` endpoint (it's a Jira-only concept), so `atlassian_licenses.py` separately looks up the default `confluence-users-*` group via the groups picker — no extra config needed. Confluence guest access (a separate license) is discovered the same way via the `confluence-guests-*` group.

### Missing email addresses (guest/external accounts)

Some accounts — typically guests or external users not managed by your org — come back from Jira's API with no `emailAddress` at all, even when the token belongs to a Jira admin. Per [Atlassian's KB on this](https://confluence.atlassian.com/jirakb/resolving-email-visibility-issues-in-jira-cloud-rest-api-responses-1528536519.html), this is driven by one of two visibility settings:

- **Org-wide**: Jira's "User email visibility" setting (Settings → System → General Configuration) set to "Hidden"
- **Per-user**: the account holder's own Atlassian profile privacy (id.atlassian.com → Contact → Email → "Who can see this?") set to "Only you and admins" instead of "Anyone"

Importantly, when a user's setting is "Only you and admins," the `/rest/api/3/user` REST API hides the email **unconditionally — even for a token that belongs to a Jira admin account.** That exception only applies to the web UI/admin console (a separate, non-public code path); the REST API doesn't do an admin check on this field at all. The only API that can bypass this is Atlassian's separate **Organizations Admin API**, which requires an entirely different credential (an org-level API key from `admin.atlassian.com`, not a Jira personal token) — a heavier setup we've deliberately not added here. Instead, `atlassian_licenses.py` supports a manual override:

```dotenv
# accountId -> email, JSON object string. Find the accountId in a WARNING
# line printed by atlassian_licenses.py when it can't resolve an email.
ATLASSIAN_ACCOUNT_EMAIL_OVERRIDES={"5b10a2844c20165700ede21g":"jane.doe@example.com"}
```

---

## Slack configuration

Authentication uses a **Slack Bot Token** (`xoxb-...`).

### 1. Create a Slack app and bot token

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it (e.g. `snipe-it-license-sync`) and pick your workspace
3. Left sidebar → **OAuth & Permissions**
4. Under **Scopes → Bot Token Scopes** → **Add an OAuth Scope**, add:
   - `users:read`
   - `users:read.email`
5. Scroll to the top of that page → **Install to Workspace** (requires workspace admin approval)
6. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### 2. Set .env values

```dotenv
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
```

Each Slack member is tagged with a derived license type based on their role flags: `owner`, `admin`, `member`, `guest-multi-channel`, or `guest-single-channel`.

---

## Snipe-IT configuration

Authentication uses a **Snipe-IT personal API token** (a JWT bearer token, distinct from Snipe-IT's OAuth client feature — you want the personal token, not an OAuth client).

### 1. Create a personal API token

1. Log into your Snipe-IT instance
2. Click your account avatar (top right) → **Manage API Keys**
3. Click **Create New Token**
4. Copy the token immediately (shown only once)

### 2. Set .env values

| .env variable | Value |
|---|---|
| `SNIPE_IT_URL` | Your Snipe-IT base URL, e.g. `https://assets.yourcompany.com` |
| `SNIPE_IT_API_TOKEN` | The personal token you copied in step 1 |

```dotenv
SNIPE_IT_URL=https://yoursnipeit.example.com
SNIPE_IT_API_TOKEN=your-snipe-it-token-here
```

### 3. Map products to Snipe-IT license names

`snipe_it_sync.py` needs to know which Snipe-IT **License** record (by exact, case-sensitive name) each product/SKU key corresponds to. Set `SNIPE_IT_LICENSE_MAP` as a JSON object; see `.env.example` for the current default mapping and format. Any product key found in the fetched JSON without an entry here is skipped with a `WARNING` at runtime — check for these after first adding a new source.

### 4. (Optional) Reconcile split identities

If the same person appears under two different email addresses across systems (e.g. a `.ext` suffix added on one side during an account transition), set `SNIPE_IT_EMAIL_ALIASES` (alias → canonical email, JSON object) so they're treated as one identity in the diff instead of showing as a false checkout+checkin pair. See `.env.example` for the format. Leave unset if you don't have this situation — don't guess at aliases, since silently merging two genuinely different people would hide a real problem.

### 5. Dry run vs. apply

`SNIPE_IT_DRY_RUN` defaults to `true` — the script only reports the diff, no Snipe-IT writes happen. Set it to `false` to actually perform the checkout/checkin calls. Because this writes to a shared system, test with a small diff first (or check a couple of seats in the Snipe-IT UI right after a run) before trusting it for a full batch.

Within a license, check-ins are applied before check-outs, so a straight seat swap (one leaver, one joiner) reuses the freed seat instead of growing the license. If a checkout still has no free seat after that (a genuine net increase in license holders — these are cloud-vendor licenses, so the source system is the ground truth), the license's seat count in Snipe-IT is increased by 1 to match, rather than skipping the checkout.

---

## Running

Run the source fetch scripts first, then the Snipe-IT reconciliation:

```bash
# 1. Fetch licensed users from each source
.venv/bin/python microsoft_licenses.py
.venv/bin/python atlassian_licenses.py
.venv/bin/python bitbucket_licenses.py
.venv/bin/python slack_licenses.py

# 2. Reconcile against Snipe-IT (dry run by default)
.venv/bin/python snipe_it_sync.py
```

Or just run `./run.sh`, which does the same in order.

Each fetch script prints a summary to stdout and writes a JSON file:

- `microsoft_licenses.json` — users with M365 SKU names (e.g. `ENTERPRISEPREMIUM`, `TEAMS_EXPLORATORY`)
- `atlassian_licenses.json` — users with product keys (e.g. `jira-software`, `confluence`, `confluence-guest`)
- `bitbucket_licenses.json` — workspace members, tagged `bitbucket`
- `slack_licenses.json` — users with a derived Slack role (e.g. `owner`, `member`, `guest-multi-channel`)

All four use **email address** as the common key for matching users across systems and into Snipe-IT.

`snipe_it_sync.py` reads those JSON files, compares them against current Snipe-IT license seat holders (matched by email — see [Snipe-IT configuration](#snipe-it-configuration) for the alias option if the same person uses different emails across systems), and writes `snipe_it_diff.json` with who should be checked out/in per license.

### Exit codes (Nagios-compatible)

`snipe_it_sync.py` exits with a [standard monitoring-plugin code](https://nagios-plugins.org/doc/guidelines.html), so it can be wired up directly as a Nagios/Icinga check:

| Code | Meaning |
|---|---|
| `0` (OK) | Snipe-IT is in sync (dry run, no diff), or apply mode ran with no errors |
| `1` (WARNING) | Diff found in dry-run mode — some license(s) need checkout/checkin |
| `2` (CRITICAL) | Snipe-IT unreachable (network/VPN down), or apply mode hit errors |
| `3` (UNKNOWN) | Missing/invalid `.env` config or an unexpected error |

The last line of stdout is a short status line (`OK: ...` / `WARNING: ...` / `CRITICAL: ...` / `UNKNOWN: ...`) suitable as the check's summary output. Run it with `SNIPE_IT_DRY_RUN=true` (the default) for monitoring — it only ever reports drift, never mutates Snipe-IT.

For a monitoring run, also set `QUIET=true` — each fetch script then prints nothing but warnings/errors, and `snipe_it_sync.py` prints only the diff and the final status line.

### Running under Nagios (`NAGIOS=true`)

Many Nagios transports (and Nagios itself, depending on config) only render the plugin's output correctly if a multi-line status is pre-encoded as a single line with literal `\n` sequences marking line breaks, rather than real newlines — Nagios then splits on those into short vs. long output for the web UI and `$LONGSERVICEOUTPUT$` in notifications.

Set `NAGIOS=true` (implies `QUIET=true`) and the Docker entrypoint handles this for you: it runs the fetch scripts and `snipe_it_sync.py` as normal, then collapses the combined stdout into that single-line `\n`-escaped form and exits with the same status code. No wrapper script needed:

```bash
docker run --rm --env-file /etc/snipe-it-sync/.env -e NAGIOS=true snipe-it-integration
```

Wire that straight up as a Nagios `check_command` — its stdout is already in the format Nagios expects.

### Example output

```
user@example.com  (Jane Smith)
  - ENTERPRISEPREMIUM
  - TEAMS_EXPLORATORY
```

---

## Running with Docker

The container runs as a non-root user (`appuser`, uid 1000) and writes its output (`*_licenses.json`, `snipe_it_diff.json`) to `/data` inside the container, which you mount from the host.

**Important**: create the host output directory yourself (`mkdir -p out`) *before* the first run. If it doesn't exist, Docker auto-creates it owned by `root`, which the non-root container user then can't write into.

### docker compose (recommended)

```bash
mkdir -p out   # one-time — see note above
docker compose build
docker compose run --rm snipe-it-integration
```

Output lands in `./out/` on the host, owned by you. `docker-compose.yml` reads `.env` via `env_file` — your secrets are never baked into the image (see `.dockerignore`).

### Plain docker

```bash
mkdir -p out   # one-time — see note above
docker build -t snipe-it-integration .
docker run --rm --env-file .env -v "$(pwd)/out:/data" snipe-it-integration
```

Either way, this runs all four fetch scripts and `snipe_it_sync.py` in sequence, same order as `run.sh`. To schedule it (e.g. daily sync), run the same command from cron or your platform's job scheduler — there's no cron baked into the image, since a dry run is safe to run as often as you like and an apply run (`SNIPE_IT_DRY_RUN=false`) should stay a deliberate, monitored action rather than a silent unattended one.

---

## Tracked Atlassian products

By default the script tracks these application role keys:

- `jira-software`
- `jira-servicedesk`
- `jira-core`
- `confluence`

To add or remove products, edit the `TRACKED_PRODUCTS` set at the top of `atlassian_licenses.py`.

---

## Security notes

- Never commit `.env` to git — it is listed in `.gitignore`
- Rotate the Microsoft client secret before it expires (set a calendar reminder)
- Rotate the Atlassian API token if the owner account changes
- Rotate the Slack bot token and Snipe-IT personal API token periodically — check whether your Snipe-IT token has a far-future/non-expiring `exp` claim, since those warrant extra caution if ever exposed
- The Microsoft app only needs **read** permissions — do not grant write permissions
- Keep `SNIPE_IT_DRY_RUN=true` (the default) until you've reviewed a diff and are confident in the `SNIPE_IT_LICENSE_MAP`/`SNIPE_IT_EMAIL_ALIASES` config — setting it to `false` performs real checkout/checkin writes against Snipe-IT
- If working with an AI coding assistant on this repo: don't have it read `.env`
  (or any file holding live secrets) directly. Ask it to have you run the
  command yourself, or have it write a helper script that sources `.env`
  internally and prints only sanitized results (status codes, booleans,
  counts) — never the raw secret value.
