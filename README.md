# Surf

Multi-Agent AI Platform.

## Structure

- `api/` - FastAPI backend service
- `ingestion/` - Document ingestion pipeline
- `infra/` - Azure infrastructure (Bicep)
- `docs/` - Project documentation

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task running
- Azure CLI (for deployment)

## Getting Started (Local Dev)

Surf uses Azure services (OpenAI, Cosmos DB, AI Search, Storage). The `setup-dev` recipe deploys minimal, publicly-accessible versions of these resources and generates a `.env` file automatically.

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for dependency management
- [just](https://github.com/casey/just) for task running
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az`)
- An Azure subscription with access to Azure OpenAI (gpt-5.1 registration required)

### Setup

```bash
# 1. Log in to Azure
az login

# 2. Install Python dependencies
cd api && uv sync && cd ../ingestion && uv sync && cd ..

# 3. Deploy dev Azure resources and generate .env (~5 min)
just setup-dev

# 4. Start the API
just dev

# 5. Verify
curl http://localhost:8000/api/v1/health
curl -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "Hello"}'
```

> **Note:** RBAC role propagation can take a few minutes after deployment. If you get 403 errors, wait a minute and retry.

### Teardown

AI Search (~$75/mo) is the main cost driver. Tear down when not actively testing:

```bash
just teardown-dev
```

## Common Commands

```bash
just dev             # Run API with hot reload (port 8090)
just devui           # Launch DevUI — interactive agent chat for testing (port 8091)
just test            # Run API tests
just test-ingestion  # Run ingestion tests
just lint            # Lint all code
just typecheck       # Type-check all code
just format          # Format all code
just setup-dev       # Deploy dev Azure resources + generate .env
just teardown-dev    # Delete dev Azure resources
```

## DevUI

The DevUI is an interactive chat interface for testing the AI workflow directly, without needing the surf-kit frontend. It connects to the same Azure OpenAI and AI Search resources as the API.

```bash
just devui
# Opens http://localhost:8091 automatically in your browser
```

The DevUI provides:
- Full multi-turn conversation testing
- Per-agent message tracing (see which agent handled each turn)
- Tool call visibility (RAG search queries and results)
- Streaming responses

> **Note:** The DevUI runs on a separate port (8091) from the main API (8090). Both can run simultaneously — `just dev` and `just devui` in separate terminals.
