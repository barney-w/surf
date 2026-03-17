# SWA to nginx Container App Migration — Progress

## Goal

Replace Azure Static Web App (SWA) with an nginx Container App that serves the React SPA and reverse-proxies `/api/*` to the API container (which becomes internal-only). Akamai CDN sits in front — it gets a CNAME to the nginx container's FQDN.

---

## Completed: Phase 1 — nginx Container Files

### `web/nginx.conf`
- Listens on **port 8080** (non-root user can't bind port 80)
- SPA fallback: `try_files $uri $uri/ /index.html`
- API reverse proxy: `location /api/` proxies to `https://${API_INTERNAL_FQDN}` (HTTPS to internal Container Apps ingress)
  - Dynamic DNS resolution via `resolver ${RESOLVER}` (extracted from `/etc/resolv.conf` at startup)
  - `set $api_upstream` variable forces per-request DNS lookup (not cached at config load)
  - `proxy_ssl_server_name on` + `proxy_ssl_verify off` for internal auto-provisioned TLS
  - `proxy_http_version 1.1` for compatibility with Container Apps envoy
  - SSE support: `proxy_buffering off`, `proxy_cache off`, 300s timeouts
- Security headers: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`
- Gzip: text, JS, CSS, JSON, SVG (min 256 bytes)
- Cache: 1y immutable for `/assets/*` (Vite-hashed), `no-cache, no-store, must-revalidate` for `index.html`
- Health endpoint: `/healthz` returns 200 `ok`
- `${API_INTERNAL_FQDN}` and `${RESOLVER}` resolved at startup via `envsubst`
- Error log to `/dev/stderr` at `warn` level

### `web/docker-entrypoint.sh`
- Extracts DNS resolver from `/etc/resolv.conf` (works in Container Apps, Docker, Kubernetes)
- Runs `envsubst` to template `API_INTERNAL_FQDN` and `RESOLVER` into nginx.conf
- Starts nginx in foreground

### `web/Dockerfile`
- Base: `nginx:1.27-alpine`
- Pre-built dist approach: CI runs `npm run build` first, Dockerfile just copies `dist/`
- Runs as `nginx` user (non-root)
- Temp paths in `/tmp/` for non-root write access
- `EXPOSE 8080`
- `HEALTHCHECK` via `wget` on `/healthz`
- `ENTRYPOINT ["/docker-entrypoint.sh"]`

---

## Completed: Phase 2 — Bicep Infrastructure Changes

### `infra/modules/container-apps.bicep`
- **Added params**: `webImageTag`, `webMinReplicas`, `webMaxReplicas`
- **Added variable**: `webImage` (same bootstrap-placeholder pattern as `apiImage`/`ingestionImage`)
- **Added resource `surfWeb`** (`ca-web-{baseName}`):
  - External ingress, port 8080
  - Same user-assigned managed identity (ACR pull)
  - Env var: `API_INTERNAL_FQDN` = `surfApi.properties.configuration.ingress.fqdn`
  - Liveness + startup probes on `/healthz` port 8080
  - HTTP scaling rule (100 concurrent requests)
  - 0.25 CPU / 0.5Gi memory
- **Changed `surfApi` ingress**: hardcoded `external: false` (API is now internal-only)
- **Changed `surfApi` ingress**: `allowInsecure: true` (allows HTTP within VNet-isolated environment; API is internal-only with no public FQDN, VNet provides security boundary, envoy provides mTLS between containers)
- **Changed secret defaults**: `anthropicApiKeyExists` and `entraClientSecretExists` now default to `true` — prevents Key Vault references from being dropped during infra-only redeployments when raw secret params aren't provided
- **Removed param**: `apiIngressExternal` (no longer parameterised)
- **Added output**: `surfWebFqdn`
- **Removed output**: `surfApiResourceId` (was only for SWA linked backend)

### `infra/main.bicep`
- **Removed params**: `staticWebAppSku`, `staticWebAppLocation`, `apiIngressExternal`
- **Removed**: entire `staticWebApp` module block
- **Added params**: `webMinReplicas`, `webMaxReplicas`, `anthropicApiKeyInKv` (default `true`), `entraClientSecretInKv` (default `true`)
- **Changed**: secret-exists flags now use `!empty(secret) || secretInKv` so KV references persist across infra redeployments
- **Replaced output**: `staticWebAppHostname` → `surfWebFqdn`

### `infra/modules/static-web-app.bicep` — DELETED

### `infra/environments/dev.bicepparam`
- Removed: `staticWebAppSku`, `staticWebAppLocation`
- Updated: `apiCorsOrigins = '["http://localhost:3000"]'` (removed SWA URL)
- Added: `webMinReplicas = 0`, `webMaxReplicas = 1`

### `infra/environments/staging.bicepparam`
- Removed: `staticWebAppSku`, `staticWebAppLocation`
- Added: `webMinReplicas = 1`, `webMaxReplicas = 3`

### `infra/environments/prod.bicepparam`
- Removed: `staticWebAppSku`, `staticWebAppLocation`
- Added: `webMinReplicas = 2`, `webMaxReplicas = 10`

### `justfile`
- Rewrote `web-deploy` recipe: now builds SPA with Entra env vars, builds Docker image for `linux/amd64`, pushes to ACR, updates `ca-web-surf-dev` Container App (mirrors `api-deploy` pattern)
- No longer sets `VITE_SURF_API_URL` (relative `/api/v1` path works via nginx proxy)

---

## Completed: Deployment & Testing

### Deployed to Azure (dev environment)
- `ca-web-surf-dev` — external ingress, FQDN: `ca-web-surf-dev.lemongrass-396ccf8b.australiaeast.azurecontainerapps.io`
- `ca-api-surf-dev` — internal-only ingress, FQDN: `ca-api-surf-dev.internal.lemongrass-396ccf8b.australiaeast.azurecontainerapps.io`

### Test Results (all passing)
| Test | Result |
|---|---|
| `GET /healthz` | 200 `ok` |
| `GET /` (SPA root) | 200, serves `index.html` |
| `GET /chat/test-123` (SPA fallback) | 200, falls back to `index.html` |
| Security headers on `/` | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` |
| Asset caching `/assets/*.js` | `Cache-Control: max-age=31536000, public, immutable` |
| Root caching `/` | `Cache-Control: no-cache, no-store, must-revalidate` |
| `GET /api/v1/health` (proxy) | 401 (auth required — proves proxy works; API correctly rejects unauthenticated requests) |
| API ingress | `external: false`, FQDN contains `.internal.` |
| Gzip | `Content-Encoding: gzip` on root page |

### Issues Discovered & Fixed During Testing

1. **Port 80 bind permission denied** — nginx non-root user can't bind privileged port 80. Fixed by switching to port 8080 in nginx config, Dockerfile `EXPOSE`, Dockerfile `HEALTHCHECK`, and Bicep `targetPort`/probe ports.

2. **API secrets dropped on infra redeployment** — Bicep used `!empty(anthropicApiKey)` to conditionally include Key Vault secret references. During infra-only redeployments (where raw secret values aren't passed), this evaluated to `false` and the env vars `ANTHROPIC_API_KEY` / `ENTRA_CLIENT_SECRET` were stripped from new revisions, causing the API to refuse to start. Fixed by adding `anthropicApiKeyInKv` / `entraClientSecretInKv` boolean params (defaulting to `true`) so KV references always persist post-bootstrap.

3. **Container Apps internal envoy and HTTPS proxy** — Required `proxy_ssl_server_name on` and `proxy_http_version 1.1` for nginx to properly negotiate TLS with the Container Apps internal envoy. Without SNI, the envoy couldn't route the request. Without HTTP/1.1, got 426 "Upgrade Required" from HTTP/1.0 default.

4. **DNS resolution caching** — nginx resolves upstream hostnames once at config load. Fixed by using `resolver` directive (dynamically extracted from `/etc/resolv.conf`) and `set $variable` pattern to force per-request DNS resolution.

---

## Completed: Phase 3 — CI/CD Changes

### 3.1 Rewrote `.github/workflows/web-ci.yml`
Modelled after `api-ci.yml` structure. Replaced SWA deployment with Docker build + Container Apps deploy.

**New structure (3 jobs):**
1. **lint** — unchanged (npm ci, typecheck)
2. **build-push** (replaces `build-deploy`):
   - Checkout, Node 22, `npm ci`, `npm run build` with `VITE_ENTRA_CLIENT_ID` and `VITE_ENTRA_TENANT_ID` from `vars.*`
   - `VITE_SURF_API_URL` NOT set (defaults to `/api/v1` — nginx proxies it)
   - Azure OIDC login, ACR login
   - `docker build -f web/Dockerfile web/` — tag with short SHA
   - `docker push`, Trivy scan (CRITICAL/HIGH, ignore-unfixed), SBOM generation + upload
3. **deploy**:
   - Azure OIDC login
   - `azure/container-apps-deploy-action@v1` targeting `ca-web-surf-dev`

**Changes from previous workflow:**
- Removed `SWA_DEPLOYMENT_TOKEN` secret reference
- Removed `Azure/static-web-apps-deploy@v1` action
- Added Azure OIDC login (same pattern as api-ci.yml)
- Added ACR login, Docker build/push, Trivy scan, SBOM
- Added `container-apps-deploy-action` for deployment
- Added `VITE_ENTRA_CLIENT_ID` and `VITE_ENTRA_TENANT_ID` as build env vars
- Removed `VITE_SURF_API_URL` env var (no longer needed)

### 3.2 Rewrote `scripts/web-deploy.ps1`
Windows PowerShell equivalent of `just web-deploy`. Previously deployed to SWA via `swa deploy` + `SWA_DEPLOYMENT_TOKEN`.

**New flow (mirrors justfile `web-deploy`):**
1. Read Entra env vars from `web/.env.local` (or fall back to `az account show` for tenant ID)
2. `npm run build` in `web/` with `VITE_ENTRA_CLIENT_ID` + `VITE_ENTRA_TENANT_ID`
3. `az acr login`, `docker build --platform linux/amd64`, `docker push`
4. `az containerapp update --name ca-web-surf-dev`

**Changes from previous script:**
- Removed `$SwaName` and `$SwaResourceGroup` parameters
- Removed `az staticwebapp secrets list` + `npx swa deploy`
- Removed `VITE_SURF_API_URL` (no longer needed — nginx proxies `/api/`)
- Added `$AcrName` parameter (default: `acrsurfdev`)
- Added Docker build/push/deploy pattern matching justfile recipe

### 3.3 Verified: no changes needed elsewhere
- `pr-checks.yml` — no SWA references; `web-lint` + `web-build` jobs unaffected
- `infra-deploy.yml` — no SWA references; picks up bicepparam changes automatically

### 3.4 Validation performed
- All action SHAs consistent across `api-ci.yml`, `ingestion-ci.yml`, `web-ci.yml` (checkout, setup-node, azure/login, trivy-action, sbom-action, upload-artifact, container-apps-deploy-action)
- Container app name pattern `ca-${{ env.SERVICE_NAME }}-surf-dev` matches Bicep naming `ca-web-${baseName}`
- No remaining SWA references in any workflow or deployment script
- No remaining `VITE_SURF_API_URL` references in workflows or justfile
- Docker build context `web/` correctly contains `dist/` after `npm run build` step

---

## Completed: Phase 4 — Justfile Updates

Already done as part of Phase 2:
- `web-deploy` recipe rewritten to use Docker build + Container Apps update
- `deploy` recipe unchanged (still runs `api-deploy web-deploy`)

---

## Completed: Phase 5 — Cleanup

### 5.1 Deleted `web/staticwebapp.config.json`
No longer used. Its functions are replaced by:
- Auth → MSAL.js (already handles login/logout)
- SPA fallback → nginx `try_files`
- Security headers → nginx `add_header`

### 5.2 `staticwebapp.config.json` in `web/dist/`
Removed automatically on next `npm run build` (Vite doesn't copy it unless referenced).

### 5.3 Deleted SWA resources from Azure
Bicep doesn't auto-delete removed resources. Manually deleted:
```sh
az staticwebapp delete --name swa-surf-dev -g rg-surf-dev-ai --yes
az staticwebapp delete --name swa-surf-dev -g rg-surf-dev --yes
```
Both resource groups had a copy — both deleted. Verified with `az staticwebapp list` returning empty.

### 5.4 Cleaned up GitHub secrets and variables
- Removed `SWA_DEPLOYMENT_TOKEN` from GitHub Actions secrets
- `VITE_SURF_API_URL` was not present (already clean)
- Added `VITE_ENTRA_CLIENT_ID` (`08ff7e73-6758-4c55-bdb1-cc4f124de8ac`) to GitHub Actions variables
- Added `VITE_ENTRA_TENANT_ID` (`799af2de-e455-499c-babe-71a7929442ca`) to GitHub Actions variables

### 5.5 Updated Entra ID app registration (`surf-web`)
Updated SPA redirect URIs via Graph API:
- **Added**: `https://ca-web-surf-dev.lemongrass-396ccf8b.australiaeast.azurecontainerapps.io`
- **Added**: `https://tauri.localhost`
- **Kept**: `http://localhost:3000`, `http://localhost:3001`
- **Removed**: `https://delightful-wave-0bfadeb0f.6.azurestaticapps.net` (old SWA hostname)

---

## Deployment Sequence (Completed)

1. ~~**Deploy infra** — creates `ca-web-surf-dev`, API stays external temporarily during initial deployment~~ done
2. ~~**Build and deploy nginx image** — push to ACR, update `ca-web-surf-dev`~~ done
3. ~~**Verify** — hit `ca-web-surf-dev` FQDN, confirm SPA loads and `/api/*` proxies work~~ done
4. ~~**Switch API to internal** — redeploy infra with `external: false` on API ingress~~ done
5. ~~**Re-verify** — confirm nginx proxy still reaches the now-internal API~~ done
6. ~~**Update Entra redirect URIs** — add nginx FQDN, remove SWA hostname~~ done
7. **Give FQDN to Akamai team** for CNAME — pending (external team)
8. ~~**Delete SWA resource** from Azure~~ done
9. ~~**Clean up GitHub secrets/vars**~~ done

---

## GitHub Actions Variables Required

The following variables must be set in the GitHub `dev` environment for the web CI/CD workflow:

| Variable | Description | Example |
|---|---|---|
| `VITE_ENTRA_CLIENT_ID` | Entra app registration client ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `VITE_ENTRA_TENANT_ID` | Entra tenant ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `AZURE_CLIENT_ID` | OIDC service principal client ID | *(already set for api-ci)* |
| `AZURE_TENANT_ID` | Azure tenant ID | *(already set for api-ci)* |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID | *(already set for api-ci)* |
| `AZURE_RESOURCE_GROUP` | Resource group name | *(already set for api-ci)* |
| `ACR_NAME` | ACR short name | *(already set for api-ci)* |
| `ACR_LOGIN_SERVER` | ACR login server FQDN | *(already set for api-ci)* |

Only `VITE_ENTRA_CLIENT_ID` and `VITE_ENTRA_TENANT_ID` are new — all others are shared with `api-ci.yml` and `ingestion-ci.yml`.

---

## Current File State

| File | Status | Notes |
|---|---|---|
| `web/nginx.conf` | Created | Fully configured with proxy, caching, headers, health |
| `web/docker-entrypoint.sh` | Created | Dynamic resolver extraction + envsubst |
| `web/Dockerfile` | Created | nginx:1.27-alpine, non-root, port 8080 |
| `infra/modules/container-apps.bicep` | Modified | Added surfWeb resource, API internal, secret defaults fixed |
| `infra/main.bicep` | Modified | Removed SWA, added web params, fixed secret persistence |
| `infra/modules/static-web-app.bicep` | Deleted | No longer needed |
| `infra/environments/dev.bicepparam` | Modified | Removed SWA params, added web replicas, enabled auth |
| `infra/environments/staging.bicepparam` | Modified | Removed SWA params, added web replicas |
| `infra/environments/prod.bicepparam` | Modified | Removed SWA params, added web replicas |
| `justfile` | Modified | Rewrote `web-deploy` recipe |
| `.github/workflows/web-ci.yml` | Modified | Rewrote: SWA deploy → Docker build + Container Apps deploy |
| `scripts/web-deploy.ps1` | Modified | Rewrote: SWA deploy → Docker build + Container Apps deploy |
| `web/staticwebapp.config.json` | Deleted | No longer needed |

---

## Final Verification (2026-03-11)

All tests passing against `ca-web-surf-dev.lemongrass-396ccf8b.australiaeast.azurecontainerapps.io`:

| Test | Result |
|---|---|
| `GET /healthz` | 200 |
| `GET /` (SPA root) | 200, serves `index.html` with Vite-hashed assets |
| `GET /chat/test-123` (SPA fallback) | 200, falls back to `index.html` |
| Security headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` |
| Root caching | `Cache-Control: no-cache, no-store, must-revalidate` |
| Asset caching (`/assets/*.js`) | `Cache-Control: max-age=31536000, public, immutable` |
| Gzip | `Content-Encoding: gzip` |
| API proxy (`/api/v1/health`) | 401 with `WWW-Authenticate: Bearer` (proves proxy works; API correctly requires auth) |
| SWA resources | Deleted from Azure (both `rg-surf-dev-ai` and `rg-surf-dev`) |
| GitHub secrets | `SWA_DEPLOYMENT_TOKEN` removed; `VITE_ENTRA_*` vars added |
| Entra redirect URIs | Container App FQDN added; old SWA hostname removed |

### Migration status: COMPLETE
Only remaining external action: give FQDN to Akamai team for CNAME configuration.
