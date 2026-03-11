# Surf Desktop App (Tauri 2)

The Surf web app is wrapped as a native desktop application using [Tauri 2](https://v2.tauri.app/). The same React codebase runs inside a native WebView — WKWebView on macOS, WebView2 on Windows — with no Electron overhead.

## Architecture

```
web/
  src/                    # React app (shared between web and desktop)
    auth/
      platform.ts         # isTauri(), needsPopupAuth(), getApiBase()
      AuthProvider.tsx     # MSAL auth with popup/redirect branching
      authConfig.ts       # MSAL configuration (shared)
    App.tsx               # OfflineBanner, update checker
    pages/ChatPage.tsx    # Uses getApiBase() for API URL
  src-tauri/
    tauri.conf.json       # Window config, CSP, bundle, updater
    Cargo.toml            # Rust dependencies (Tauri + plugins)
    src/
      main.rs             # Entry point (calls lib::run)
      lib.rs              # Plugin registration, DevTools setup
    capabilities/
      main.json           # OS-level permissions (principle of least privilege)
    icons/                # Generated app icons (all platforms)
  vite.config.ts          # Tauri-aware dev server config
  package.json            # Tauri CLI + plugin npm packages
```

## Running Locally

### Prerequisites

- **Rust** >= 1.94.0 (`rustup update stable`)
- **Node.js** 22+
- **surf-kit** monorepo checked out at `../../surf-kit/` relative to `web/`
- Platform-specific:
  - **macOS**: Xcode Command Line Tools
  - **Windows**: Visual Studio Build Tools 2022 (C++ workload), WebView2 Runtime

### Development

```bash
cd web
npm install       # First time only
npm run tauri:dev # Starts Vite + compiles Rust + opens native window
```

First run compiles ~240 Rust crates (3-5 minutes). Subsequent runs use incremental compilation (<10 seconds). Vite hot-module-reload works normally inside the WebView.

### Production Build

```bash
cd web
npm run tauri:build
```

Outputs to `web/src-tauri/target/release/bundle/`:
- **Windows**: `nsis/*.exe` (installer) + `nsis/*.sig` (update signature)
- **macOS**: `dmg/*.dmg` and `macos/*.app`

## Platform Detection

Three utility functions in `web/src/auth/platform.ts`:

```typescript
// True when running inside Tauri (any mode)
isTauri()         // checks window.__TAURI_INTERNALS__

// True only in production Tauri builds (origin: tauri.localhost)
// Used to decide popup vs redirect auth — see Auth section
needsPopupAuth()

// Returns the correct API base URL for the current environment
getApiBase()      // VITE_SURF_API_URL > VITE_DESKTOP_API_URL > /api/v1
```

## Authentication (MSAL)

The app uses Microsoft Entra ID (MSAL.js) for authentication. The auth flow differs between web and desktop:

| Context | Origin | Auth Flow | Why |
|---------|--------|-----------|-----|
| Web (browser) | `https://your-domain.com` | Redirect | Standard web flow |
| Tauri dev | `http://localhost:3000` | Redirect | Vite dev server — redirects work normally |
| Tauri production | `https://tauri.localhost` | Popup | WebView can't handle full-page redirects to external URLs reliably |

The branching logic lives in `AuthProvider.tsx`. The `needsPopupAuth()` function returns `true` only when the origin contains `tauri.localhost` (production builds). In all other cases, including Tauri dev mode, the standard redirect flow is used.

### Auth flow detail

1. **Login**: `loginRedirect()` (web/dev) or `loginPopup()` (production desktop)
2. **Token acquisition**: `acquireTokenSilent()` first, falls back to redirect/popup on `InteractionRequiredAuthError`
3. **Logout**: `logoutRedirect()` or `logoutPopup()` matching the login method

### Azure App Registration

The Entra ID app registration needs these redirect URIs:
- `http://localhost:3000` — local development (web and Tauri dev)
- `https://tauri.localhost` — production Tauri builds
- Your production web domain

## API Connectivity

| Context | How API is reached | Config |
|---------|-------------------|--------|
| Web (dev) | Vite proxy: `/api/v1` -> `localhost:8090` | `vite.config.ts` proxy |
| Tauri dev | Same Vite proxy (WebView loads from `localhost:3000`) | Same |
| Web (prod) | Nginx reverse proxy: `/api/v1` -> container app | `nginx.conf` |
| Tauri prod | Direct HTTPS to API | `VITE_DESKTOP_API_URL` set at build time |

The `getApiBase()` function in `platform.ts` resolves the correct URL:

1. `VITE_SURF_API_URL` — explicit override (highest priority)
2. `VITE_DESKTOP_API_URL` — used when `isTauri()` is true (production desktop)
3. `/api/v1` — default fallback (works with proxy in dev, nginx in web prod)

### CORS

The API at `api/src/config/settings.py` allows these origins by default:
- `http://localhost:3000` — development
- `https://tauri.localhost` — production desktop

The production guard in `api/src/main.py` rejects `localhost` origins in non-dev environments but explicitly allows `tauri.localhost` through.

## Security

### Content Security Policy

Set in `tauri.conf.json` under `app.security.csp`:

```
default-src 'self';
script-src  'self';
style-src   'self' 'unsafe-inline' https://fonts.googleapis.com;
font-src    'self' data: https://fonts.gstatic.com;
img-src     'self' blob: data: https:;
connect-src 'self' https://login.microsoftonline.com
                   https://*.login.microsoftonline.com
                   https://graph.microsoft.com
                   https://*.azurecontainerapps.io;
frame-src   https://login.microsoftonline.com
```

Notes:
- `unsafe-inline` in `style-src` is required by Tailwind CSS / React inline styles
- `unsafe-eval` is **not** granted — React 19 + Vite don't need it
- `connect-src` includes `*.azurecontainerapps.io` as a wildcard for Azure Container Apps; replace with your specific domain in production

### Prototype Freeze

`freezePrototype` is set to `false`. While `true` would prevent prototype pollution attacks, Microsoft's login page JavaScript modifies prototypes and breaks with frozen prototypes. This is a known incompatibility when using MSAL redirect flows inside a Tauri WebView.

### Capabilities (OS Permissions)

Defined in `web/src-tauri/capabilities/main.json`. The app follows the principle of least privilege:

**Granted:**
- `core:default`, `core:window:default` — basic window operations
- Window management: set-title, close, minimize, maximize, toggle-maximize, set-focus
- `updater:default` — check for and install app updates
- `window-state:default` — remember window size/position across sessions
- `single-instance:default` — prevent multiple app instances

**Not granted (blocked by default):**
- `fs:` — no filesystem access
- `shell:` — no spawning child processes
- `http:` — no arbitrary HTTP from the Rust layer (all networking goes through the WebView's native `fetch()`)
- `clipboard:` — no clipboard access
- `global-shortcut:` — no global hotkeys

## Tauri Plugins

| Plugin | Purpose | Status |
|--------|---------|--------|
| `tauri-plugin-single-instance` | Prevents multiple app windows | Active (Rust-only, no npm package) |
| `tauri-plugin-window-state` | Remembers window size/position | Active |
| `tauri-plugin-updater` | In-app update checking and installation | Active |
| `tauri-plugin-notification` | OS notifications | Disabled — IPC conflicts with external pages (Microsoft login). Re-enable when Tauri scopes IPC to app origin only. |

Plugins are registered in `web/src-tauri/src/lib.rs` and their npm counterparts (where they exist) are in `web/package.json`.

## Desktop Polish

### App Icons

Generated from `web/public/surf.png` using `npx tauri icon`. The source image was padded to a square (700x700) since Tauri requires square icons. Generated variants live in `web/src-tauri/icons/` and include:
- `icon.ico` — Windows taskbar/installer
- `icon.icns` — macOS
- `icon.png` + size variants — general use
- Android and iOS variants (for future mobile builds)

### Offline Banner

`App.tsx` includes an `OfflineBanner` component that listens to browser `online`/`offline` events and shows an amber notification bar when connectivity is lost. It appears above the header and disappears automatically when the connection is restored.

### Update Checker

On app launch, if running inside Tauri, the app dynamically imports `@tauri-apps/plugin-updater` and checks for updates. If an update is available, the user is prompted via `window.confirm()`. The check uses dynamic `import()` so the Tauri-specific module is never bundled into web builds. Failures are silently caught.

The updater is configured in `tauri.conf.json` under `plugins.updater`:
- **Public key**: Placeholder (`REPLACE_WITH_PUBLIC_KEY`) — must be replaced after running `npx tauri signer generate`
- **Endpoint**: `https://surf-releases.blob.core.windows.net/desktop/latest.json` — placeholder for Azure Blob Storage manifest
- **Install mode**: `passive` (progress bar, no user prompts during install)

### DevTools

In debug builds (`npm run tauri:dev`), Chrome DevTools open automatically. This is controlled by the `#[cfg(debug_assertions)]` block in `lib.rs`. DevTools are not available in release builds.

## Vite Configuration

`web/vite.config.ts` has Tauri-specific adjustments:

- `strictPort: true` — fail if port 3000 is taken (Tauri expects exactly this port)
- `host: process.env.TAURI_DEV_HOST || false` — allows Tauri to specify a custom host
- `open: !process.env.TAURI_ENV_PLATFORM` — don't auto-open browser when Tauri launches (Tauri sets `TAURI_ENV_PLATFORM`)
- `build.target: 'es2022'` — matches WebView2/WKWebView capabilities
- All existing aliases, plugins, and proxy config are preserved unchanged

## CI/CD

### GitHub Actions Workflow

`.github/workflows/desktop-build.yml` builds a signed Windows installer:

**Triggers:**
- Push tags matching `desktop-v*` (e.g., `desktop-v0.2.0`)
- Manual `workflow_dispatch`

**Pipeline:**
1. Check out `surf-lab` and `surf-kit` repos
2. Set up Node.js 22 (npm cache) and Rust stable (x86_64-pc-windows-msvc target)
3. Cache Rust compilation (`Swatinem/rust-cache` scoped to `web/src-tauri`)
4. Install Azure Trusted Signing CLI for code signing
5. `npm ci` + `npm run tauri:build`
6. Upload `.exe` installer and `.sig` signature as artifacts

**Required GitHub Secrets:**

| Secret | Purpose |
|--------|---------|
| `ENTRA_CLIENT_ID` | Azure Entra app client ID (baked into the build) |
| `ENTRA_TENANT_ID` | Azure Entra tenant ID (baked into the build) |
| `DESKTOP_API_URL` | Production API URL for the desktop app |
| `SURF_KIT_TOKEN` | PAT to clone the `surf-kit` repo |
| `AZURE_SIGNING_CLIENT_ID` | Azure Trusted Signing — service principal |
| `AZURE_SIGNING_CLIENT_SECRET` | Azure Trusted Signing — secret |
| `AZURE_TENANT_ID` | Azure tenant for signing |
| `TAURI_UPDATE_PRIVATE_KEY` | Ed25519 private key for update signing |
| `TAURI_UPDATE_KEY_PASSWORD` | Password for the update signing key |

### Bundle Configuration

In `tauri.conf.json`:
- `bundle.createUpdaterArtifacts: true` — generates `.sig` files alongside installers
- `bundle.windows.nsis.installMode: "both"` — user chooses per-user or per-machine install

## Manual Steps Before First Release

These require human action and cannot be automated by agents:

1. **Generate update signing keypair**: `npx tauri signer generate -w ~/.tauri/surf-update.key`
2. **Replace placeholder** in `tauri.conf.json` `plugins.updater.pubkey` with the generated public key
3. **Azure Entra ID**: Add `https://tauri.localhost` as a redirect URI in the app registration
4. **Azure Trusted Signing**: Set up identity verification (1-7 business days)
5. **Azure Blob Storage**: Create container for hosting `latest.json` update manifest
6. **GitHub Secrets**: Configure all secrets listed above
7. **Windows testing**: Test on actual Windows machines (SmartScreen warnings, corporate proxy, etc.)

## Environment Variables

| Variable | Where Set | Purpose |
|----------|-----------|---------|
| `VITE_ENTRA_CLIENT_ID` | `.env` / CI secrets | Azure Entra app client ID |
| `VITE_ENTRA_TENANT_ID` | `.env` / CI secrets | Azure Entra tenant ID |
| `VITE_SURF_API_URL` | `.env` (optional) | Override API URL for all contexts |
| `VITE_DESKTOP_API_URL` | CI secrets | API URL for production desktop builds |
| `TAURI_DEV_HOST` | Tauri CLI (automatic) | Custom dev server host |
| `TAURI_ENV_PLATFORM` | Tauri CLI (automatic) | Set during Tauri builds — used to suppress browser auto-open |

## Known Issues and Limitations

1. **Notification plugin disabled**: Tauri injects IPC scripts into all pages loaded in the WebView, including external pages like Microsoft's login. The notification plugin's `is_permission_granted` check runs on these pages and fails because IPC is not available outside the app origin. The plugin is commented out in `lib.rs` until Tauri scopes IPC injection to the app origin only.

2. **`freezePrototype` disabled**: Microsoft's login page JavaScript modifies `Object.prototype` and other built-in prototypes. Tauri's `freezePrototype: true` setting prevents this, causing `TypeError: Attempted to assign to readonly property` and a blank login page. This is a fundamental incompatibility with MSAL redirect flows.

3. **First build is slow**: The initial `cargo` compile downloads and builds ~240 crates. Allow 3-5 minutes. Subsequent incremental builds take <10 seconds.

4. **macOS vs Windows WebView**: Development happens on macOS (WKWebView) but the production target is Windows (WebView2). Behaviour may differ — always test on Windows before release. The capabilities file is scoped to `platforms: ["windows"]`.

5. **Popup auth untested in production**: The `needsPopupAuth()` path (MSAL popup flow for `tauri.localhost` origin) has not been tested in a production Tauri build. It will need validation once the first Windows release build is created.
