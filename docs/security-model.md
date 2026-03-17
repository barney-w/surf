# Security Model

This document describes the security controls implemented across the Surf platform. It covers authentication, authorisation, input validation, prompt injection defences, data isolation, secret management, and operational safety mechanisms.

---

## 1. Authentication

Surf supports three authentication modes, determined by the `AUTH_ENABLED` setting and the token type presented.

### 1.1 Entra ID (Organisational Users)

Organisational users authenticate via Microsoft Entra ID. The API validates JWTs using RS256 with keys fetched from the Microsoft multi-tenant JWKS endpoint (`https://login.microsoftonline.com/common/discovery/v2.0/keys`).

Key validation steps in `api/src/middleware/auth.py`:

- **Algorithm**: RS256 only for Entra tokens.
- **Audience**: Must match the configured `ENTRA_CLIENT_ID` (or its `api://` URI).
- **Required claims**: `exp`, `iss`, `aud`, `oid`.
- **Issuer validation**: Post-decode check that `iss` starts with `https://login.microsoftonline.com/` or `https://sts.windows.net/`. The `verify_iss` option is set to `False` during decode to support multi-tenant issuers, with manual validation afterwards.
- **JWKS caching**: The `PyJWKClient` caches keys for 300 seconds (`_get_jwks_client()`).

The validated token's claims populate a `UserContext` dataclass with `user_id` (from `oid`), `name`, `email`, `department`, `job_title`, and `tid` (tenant ID).

### 1.2 Guest Tokens (Anonymous Access)

Guest access uses short-lived JWTs signed with HS256 HMAC. The server issues tokens via `POST /api/v1/auth/guest` (`api/src/routes/guest.py`).

- **Signing secret**: Server-side `GUEST_TOKEN_SECRET` environment variable.
- **Token lifetime**: Configurable via `GUEST_TOKEN_TTL_MINUTES` (default 30 minutes).
- **Required claims**: `exp`, `iss`, `sub`.
- **Issuer**: Fixed to `surf-api`.
- **Subject**: A random `guest-{hex12}` identifier.
- **Token routing**: `_is_guest_token()` inspects the unverified JWT header for `alg == "HS256"` to distinguish guest tokens from Entra tokens before validation.
- **Rate limiting**: Guest token issuance is rate-limited to 5 requests per minute.
- **Feature gate**: Guest access is disabled entirely when `GUEST_TOKEN_SECRET` is empty.

### 1.3 Dev Bypass

When `AUTH_ENABLED=false`, the `get_current_user()` function returns a static `UserContext` with `user_id="dev-user"`. This mode is enforced to be dev-only: the application refuses to start in non-dev environments with auth disabled (`api/src/main.py`, lifespan startup checks).

### 1.4 Production Safety Guards

The lifespan startup in `api/src/main.py` enforces multiple invariants for non-dev environments:

- `auth_enabled` must be `True`
- `debug` must be `False`
- CORS origins must not contain `*` (wildcard)
- CORS origins must not contain `localhost` (except `tauri.localhost`)
- `postgres_ssl` must be `True`

Violating any of these causes the application to refuse to start (`SystemExit(1)`).

---

## 2. Authorisation

### 2.1 AuthLevel Enum

The `AuthLevel` enum in `api/src/agents/_base.py` defines three tiers:

| Level | Value | Description |
|---|---|---|
| `PUBLIC` | `"public"` | Accessible to guests and all authenticated users |
| `MICROSOFT_ACCOUNT` | `"microsoft"` | Requires a Microsoft account (personal or organisational) |
| `ORGANISATIONAL` | `"organisational"` | Requires an organisational (work/school) account |

### 2.2 Caller Level Resolution

`_resolve_caller_auth_level()` in `api/src/routes/agents.py` determines the caller's effective level:

- **Guest users** (`is_guest=True`): `PUBLIC`
- **Organisational accounts** (tenant ID present and not the Microsoft consumer tenant `9188040d-...`): `ORGANISATIONAL`
- **Personal Microsoft accounts** (authenticated but consumer tenant or no `tid`): `MICROSOFT_ACCOUNT`

### 2.3 Auth-Filtered Agent Graphs

At startup, `build_agent_graph()` in `api/src/orchestrator/builder.py` constructs separate agent graphs filtered by auth level. The hierarchy filter ensures that a `PUBLIC`-level graph only includes agents with `auth_level=PUBLIC`, while the full graph includes all agents.

```python
app.state.agent_graphs = {
    AuthLevel.PUBLIC: public_graph,       # guests see only public agents
    AuthLevel.MICROSOFT_ACCOUNT: full_graph,
    AuthLevel.ORGANISATIONAL: full_graph,
}
```

The coordinator agent's prompt is built from the filtered registry, so it cannot even describe or route to agents the caller is not authorised to access.

### 2.4 Per-Agent Access Control

Each `DomainAgent` subclass declares its `auth_level` property (defaults to `PUBLIC`). When a user directly targets an agent via `body.agent`, the chat route checks `_can_access()` and returns HTTP 403 if the caller's level is insufficient (`api/src/routes/chat.py`, `_resolve_workflow_factory()`).

---

## 3. Input Validation

### 3.1 Request Body Size Limits

`BodySizeLimitMiddleware` in `api/src/middleware/body_limit.py` enforces two tiers:

| Paths | Limit |
|---|---|
| `/api/v1/chat`, `/api/v1/chat/stream` | 20 MB (`MAX_UPLOAD_BODY_BYTES`) |
| All other endpoints | 64 KB (`MAX_BODY_BYTES`) |

The middleware checks both the `Content-Length` header (fast reject) and the actual body length (guards against omitted or spoofed headers). Oversized requests receive HTTP 413.

### 3.2 Message Length Limit

`validate_message()` in `api/src/middleware/input_validation.py` rejects messages exceeding 10,000 characters (`MAX_MESSAGE_LENGTH`) with HTTP 422. This is called from `prepare_chat_request()` in `api/src/services/chat_service.py` on every chat request.

### 3.3 Control Character Stripping

The same `validate_message()` function strips null bytes and control characters (U+0000-U+0008, U+000B, U+000C, U+000E-U+001F, U+007F) while preserving harmless whitespace (`\n`, `\r`, `\t`). The regex `_CONTROL_CHAR_RE` handles this.

---

## 4. Prompt Injection Defence-in-Depth

Surf does not rely on regex-based prompt injection detection patterns. Instead, the architecture uses a layered approach where each layer independently reduces the attack surface and blast radius.

### 4.1 Layer 1: Scoped RAG (Domain Isolation)

Each agent's `search_knowledge_base` tool is factory-created with a fixed `RAGScope` (`api/src/rag/tools.py`, `create_rag_tool()`). The scope injects immutable filters (e.g. `domain`, `content_source`, `document_type`) into every search query. An agent handling HR queries cannot retrieve IT documents, regardless of what the user or a prompt injection attempt requests.

The scope is defined per agent in `api/src/agents/_base.py` via the `RAGScope` dataclass and bound at startup in `api/src/orchestrator/builder.py`.

### 4.2 Layer 2: Structured JSON Output Enforcement

Domain agents are required to produce responses as a strict JSON object matching `AgentResponseModel`. The `_JSON_OUTPUT_PREAMBLE` in `api/src/orchestrator/builder.py` instructs the model that its first character must be `{` and provides the exact schema. This constrains the model's output format, making it harder for injected instructions to produce arbitrary free-text responses that bypass structured parsing.

The `parse_agent_output()` function in `api/src/agents/_output.py` validates and parses the output, falling back to plain-text wrapping if JSON parsing fails.

### 4.3 Layer 3: Quality Gate Post-Response Validation

`run_quality_gate()` in `api/src/rag/quality_gate.py` applies deterministic checks after the agent produces its response:

- **SEARCH_INFRASTRUCTURE_ERROR**: Detects RAG infrastructure failures and replaces the response with a safe fallback message.
- **SEARCH_SKIPPED**: Flags when the agent never called `search_knowledge_base`.
- **RESULTS_IGNORED**: Catches when the agent claims no knowledge despite RAG returning substantive results (using phrase-matching patterns like "couldn't find", "no relevant", etc.).
- **SOURCES_MISSING**: Detects when search returned results but the agent omitted sources.

When issues are detected, the gate can remediate the response (e.g. recovering sources from RAG output and adjusting confidence).

### 4.4 Layer 4: MessageFieldExtractor Source-Pollution Guard

`MessageFieldExtractor` in `api/src/services/streaming.py` guards the streaming output path. It extracts the `message` field from the streaming JSON and buffers the first 10 characters (matching `len("=== SOURCE")`). If the message field starts with RAG source markers (`=== SOURCE`), the extractor suppresses all streaming output. The complete, sanitised response is then delivered via the `done` event instead.

This prevents the LLM from leaking raw RAG source blocks into the user-visible message stream, whether caused by prompt injection or model non-compliance.

The corresponding batch-path sanitisation is handled by `sanitize_agent_response()` in `api/src/agents/_output.py`, which strips `=== SOURCE === ... === END SOURCE ===` blocks from the message field and recovers them as structured `Source` objects.

---

## 5. Data Isolation

### 5.1 User-Scoped Conversations

All conversation operations in `api/src/services/conversation.py` include a `user_id` parameter. Queries use `WHERE ... AND user_id = $2` to ensure a user can only access their own conversations. The `add_message()` method explicitly verifies ownership within a transaction (`SELECT user_id FROM conversations WHERE id = $1 FOR UPDATE`), raising `ValueError("Conversation not found or access denied")` on mismatch.

### 5.2 CASCADE Deletes

The database schema (`api/alembic/versions/001_initial_schema.py`) uses `ON DELETE CASCADE` on foreign keys from `messages` and `feedback` to `conversations`. Deleting a conversation automatically removes all associated messages and feedback records.

### 5.3 Conversation Expiry

`cleanup_expired_conversations()` in `api/src/services/conversation.py` deletes conversations older than a configurable TTL (`CONVERSATION_TTL_DAYS`, default 90 days).

---

## 6. Secret Management

### 6.1 Zero CI/CD Secrets

All GitHub Actions workflows authenticate to Azure using OIDC federated credentials (`id-token: write` permission). The `azure/login` action receives `client-id`, `tenant-id`, and `subscription-id` from GitHub repository variables (not secrets), and exchanges a GitHub-issued OIDC token for an Azure access token. No long-lived credentials are stored in CI/CD.

Relevant workflows:
- `.github/workflows/infra-deploy.yml`
- `.github/workflows/api-ci.yml`
- `.github/workflows/web-ci.yml`
- `.github/workflows/ingestion-ci.yml`

### 6.2 Key Vault for Runtime Secrets

The `AZURE_KEYVAULT_URL` setting points to an Azure Key Vault instance that stores runtime secrets (e.g. API keys, client secrets). The Key Vault URL is configured as a non-secret environment variable on the container; actual secret values are fetched at runtime.

### 6.3 Managed Identity Access

All Azure service connections (AI Search, Azure OpenAI embeddings, Azure Storage) use `DefaultAzureCredential`, which resolves to the container's managed identity in production. No service connection strings or API keys are stored in environment variables for Azure-native services.

---

## 7. Operational Safety

### 7.1 Rate Limiting

The API uses [slowapi](https://github.com/laurentS/slowapi) for per-user rate limiting. The rate limit key is the authenticated user's `user_id` (falling back to IP address for unauthenticated requests), resolved by `get_user_key()` in `api/src/middleware/rate_limit.py`.

Per-endpoint limits:

| Endpoint | Limit |
|---|---|
| `POST /api/v1/auth/guest` | 5/minute |
| `POST /api/v1/chat` | 10/minute |
| `POST /api/v1/chat/stream` | 10/minute |
| `GET /api/v1/chat/{id}` | 60/minute |
| `DELETE /api/v1/chat/{id}` | 20/minute |
| `POST /api/v1/chat/{id}/feedback` | 30/minute |
| `GET /api/v1/me` | 30/minute |
| `GET /api/v1/me/photo` | 10/minute |

### 7.2 LLM Timeout Enforcement

`LLM_TIMEOUT_SECONDS` (90 seconds) is defined in `api/src/middleware/error_handler.py`. Both `asyncio.TimeoutError` and `LLMTimeoutError` are caught by registered exception handlers, returning HTTP 504 with a user-friendly message suggesting the user retry with a simpler question.

### 7.3 Graceful Degradation

The application degrades gracefully when infrastructure components are unavailable:

- **Database failure**: When `POSTGRES_ENABLED=false` or the database is unreachable, the application starts but conversation history is not persisted. The `ConversationService` is set to `None` and callers check for its presence.
- **RAG failure**: In dev mode, RAG infrastructure errors at startup are logged as warnings but do not prevent startup. In non-dev environments, RAG failures cause the application to refuse to start. At runtime, `SearchInfrastructureError` is caught by the RAG tool and returned as `SEARCH_INFRASTRUCTURE_ERROR:`, which the quality gate intercepts and replaces with a safe user-facing message.
- **Upstream rate limiting**: `RateLimitError` from upstream APIs is caught and returned as HTTP 429 with a `Retry-After` header.
- **Unhandled exceptions**: A catch-all exception handler returns HTTP 500 with a generic message. Stack traces are only included in the response when `debug=True` (which is blocked in non-dev environments).

### 7.4 CORS Configuration

CORS is configured explicitly via `API_CORS_ORIGINS` with production guards that reject wildcards and localhost origins in non-dev environments. Allowed methods are restricted to `GET`, `POST`, `DELETE`, and `OPTIONS`.
