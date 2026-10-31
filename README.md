# Azure Delta Tracker

A small Flask app, shipped as an Alpine container, that shows who changed what in Azure. It's a blame thrower for click-ops.

- Reads **Azure Activity Logs** at whatever scope you pick: the whole tenant, a management group, a subscription or a resource group.
- Lists every change in time order with the resource, the operation, and who made it and how (Portal, CLI, PowerShell, Terraform, another service principal).
- Pulls **before/after property values** from Resource Graph `resourcechanges` for changes in the last 14 days.
- Has a **time slider** that covers the full 90-day Activity Log retention, plus presets.
- Leaves out sandbox and pre-prod **subscriptions** by default. You can change the exclusions per view.
- A toggle hides changes made by your **Terraform Enterprise** service principals.
- **Click-Ops Report**: the top N people (10 by default) making changes as themselves, with counts by change type and client.
- **CSV export** for both views. Every filter is kept in the URL, so you can share a link to an exact view.
- Sign-in with a **local admin** account and/or **Microsoft Entra ID** (OIDC).
- **Mock mode** generates realistic data, so you can run it locally without Azure access.

## Quick start (mock data)

```bash
cp .env.example .env          # MOCK_MODE=true is already set
docker compose up --build
```

Go to http://localhost:8000 and sign in as `admin` / `admin`. In mock mode the password defaults to `admin` when none is configured.

The mock tenant has 6 management groups, 9 subscriptions (sandbox and pre-prod ones are excluded by default), 13 users with skewed activity, 3 TFE service principals and 3 other automation identities. It holds about 1,800 operations over 90 days, including failed calls and read-type noise, so you can check that filtering works. Activity Log and Resource Graph records are generated in the same raw JSON shape the Azure APIs return.

Without Docker:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
MOCK_MODE=true flask --app wsgi run
pytest
```

## Azure setup

### 1. App registration (data access)

1. Create an App Registration and a client secret.
2. Give its service principal **Reader** at the scope you want to see. For tenant-wide visibility, assign it on the **Tenant Root Group** management group. Reader covers Activity Log reads (`Microsoft.Insights/eventtypes/*`), Resource Graph and management group enumeration. `Monitoring Reader` together with `Management Group Reader` also works.
3. Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`, and set `MOCK_MODE=false`.

### 2. Terraform Enterprise filter

Set `TFE_CLIENT_IDS` to a comma-separated list of the **client (application) IDs** your TFE workspaces authenticate with, one per TFE instance. A change is treated as Terraform when the Activity Log `claims.appid` or `caller` matches one of them.

### 3. Entra ID sign-in (optional)

You can reuse the same App Registration or create a separate one:

1. Under **Authentication**, add a *Web* platform redirect URI: `https://<your-host>/auth/callback`.
2. Set `ENTRA_AUTH_ENABLED=true`. `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` and `ENTRA_CLIENT_SECRET` fall back to the `AZURE_*` values if left empty.
3. Optionally limit access with `ENTRA_ALLOWED_GROUPS` (group object IDs, which needs the *groups* claim configured in the token) and/or `ENTRA_ALLOWED_ROLES` (app role values).
4. Behind a reverse proxy, set `ENTRA_REDIRECT_URI` explicitly and `SESSION_COOKIE_SECURE=true`. The app honours `X-Forwarded-*` headers.

### 4. Local admin

Prefer a password hash over a plain password:

```bash
docker run --rm azure-delta-tracker python -c \
  "from werkzeug.security import generate_password_hash as g; print(g('your-password'))"
```

Put the output in `LOCAL_ADMIN_PASSWORD_HASH`. In a compose `.env` file, escape each `$` as `$$`. Set `LOCAL_ADMIN_ENABLED=false` to allow Entra sign-in only.

Always set a fixed `SECRET_KEY` outside mock mode. Otherwise sessions reset every time the container restarts.

## How changes are classified

| Rule | Detail |
| --- | --- |
| What counts as a change | `Administrative` events with status `Succeeded` whose operation ends in `/write`, `/delete` or `/action`. Read-type actions (`list*`, `validate`, `whatIf`, `checkNameAvailability`, policy audit) are ignored. |
| Duplicates | Begin/End and async status events are collapsed to one row per correlation ID, resource and operation. |
| Create vs update | Taken from the Resource Graph change type when available, otherwise from the Activity Log `subStatus` (`Created`). |
| Caller type | **User** if the caller is a UPN or the token `idtyp` is `user`. **Terraform** if the app ID matches `TFE_CLIENT_IDS`. Otherwise **Service Principal**. |
| Click-ops | Any change made by a **User**. The "Via" column (from Resource Graph `clientType` or well-known first-party app IDs) shows whether it was the Portal, CLI or PowerShell. The report has a *Portal only* toggle for strict click-ops counting. |
| Before/after | Joined from Resource Graph `resourcechanges` on correlation ID and resource ID. That table keeps **14 days** of history (shaded on the time slider). Older changes show who and what, but not property values. |

## Configuration reference

See [.env.example](.env.example) for every setting. The main ones are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MOCK_MODE` | `false` | Use generated data instead of Azure |
| `DEFAULT_EXCLUDED_SUBSCRIPTIONS` | `*sandbox*,*sbx*,*preprod*,*pre-prod*` | Subscription IDs or name globs excluded by default |
| `TFE_CLIENT_IDS` | *(empty)* | TFE service principal client IDs |
| `TFE_FILTER_DEFAULT` | `true` | Whether "Hide Terraform Enterprise" starts on |
| `CLICKOPS_TOP_N` | `10` | Default leaderboard size |
| `MAX_LOOKBACK_DAYS` | `90` | Slider range (Activity Log retention) |
| `USE_RESOURCE_GRAPH_CHANGES` | `true` | Enrich with before/after values |
| `CACHE_TTL_SECONDS` | `300` | In-memory cache of Azure responses |
| `FETCH_CONCURRENCY` | `8` | Parallel per-subscription Activity Log reads |

## Architecture

```
app/
  azure_client.py   ARM REST client (MSAL client credentials): subscriptions, MGs, Activity Log, Resource Graph
  mock_client.py    Same interface, deterministic generated data
  service.py        Scope resolution, filtering, normalisation, Resource Graph join, report, CSV
  auth.py           Local admin + Entra OIDC (MSAL auth code flow), CSRF
  routes.py         Pages and JSON/CSV API
  templates/, static/   UI (vanilla JS, no CDN dependencies; strict CSP)
```

API endpoints (all need a signed-in session): `/api/changes`, `/api/changes.csv`, `/api/report`, `/api/report.csv`, `/api/scopes`, `/api/resource-groups`, `/api/config`, `POST /api/refresh`. The query parameters are `scope`, `id`, `rg`, `start`, `end`, `exclude`, `hide_tf`, `top` and `portal_only`. `/healthz` needs no sign-in.

## Notes and limits

- Activity Logs are read **per subscription**, so tenant-wide queries over long ranges in big estates take a while. Results are cached for `CACHE_TTL_SECONDS`, and *Refresh from Azure* clears the cache. For estates with hundreds of subscriptions, consider exporting Activity Logs to Log Analytics and pointing a future version at that instead.
- The container runs a single gunicorn worker with 8 threads, so the cache is shared between requests. Scale out with more replicas rather than more workers.
- A user running the Azure CLI or PowerShell counts as click-ops in the default report, because it's still an unmanaged change. Turn on *Portal only* to count Portal changes alone.
