# Surf - Multi-Agent Orchestration Platform

# Start the OpenTelemetry collector for local telemetry development
otel:
    docker compose up -d otel-collector

# Start local Langfuse (trace viewer) at http://localhost:3100
langfuse:
    docker compose up -d langfuse langfuse-worker

# Start local Postgres
db:
    docker compose up -d postgres

# Wait for Postgres to be ready
db-wait:
    @until docker compose exec postgres pg_isready -U surf -d surf > /dev/null 2>&1; do sleep 0.5; done

# Open psql shell to local Postgres
db-shell:
    docker compose exec postgres psql -U surf -d surf

# Reset database (truncate all tables, keep schema)
db-reset:
    docker compose exec postgres psql -U surf -d surf -c "TRUNCATE conversations, messages, feedback CASCADE;"

# Clean up expired conversations (default: 90 days)
db-cleanup:
    cd api && uv run python -c "\
    import asyncio;\
    from src.config.settings import get_settings;\
    from src.services.conversation import ConversationService;\
    async def main():\
        s = get_settings();\
        svc = ConversationService(s);\
        await svc.initialize();\
        count = await svc.cleanup_expired_conversations(s.conversation_ttl_days);\
        print(f'Deleted {count} expired conversations');\
        await svc.close();\
    asyncio.run(main())"

# Full teardown (remove volume — clean slate)
db-destroy:
    docker compose down -v

# Open the dev admin page in browser (cross-platform)
admin:
    python3 -m webbrowser http://localhost:8090/api/v1/admin/

# Check that all development prerequisites are installed
check-prereqs:
    #!/usr/bin/env bash
    set -euo pipefail
    ok=true
    for cmd in az uv node npm docker just git python3; do
        if command -v "$cmd" > /dev/null 2>&1; then
            ver=$("$cmd" --version 2>&1 | head -1)
            printf "  %-10s %s\n" "$cmd" "$ver"
        else
            printf "  %-10s MISSING\n" "$cmd"
            ok=false
        fi
    done
    $ok || { echo "Some prerequisites are missing."; exit 1; }
    echo "All prerequisites installed."

# Run web frontend against a deployed API (no local API/DB needed)
dev-remote url="":
    cd web && API_PROXY_TARGET={{url}} npm run dev

# Run database migrations
db-migrate:
    cd api && uv run alembic upgrade head

# Run API in development mode with hot reload
dev: db db-wait db-migrate
    cd api && uv run uvicorn src.main:app --reload --port 8090

# Launch the DevUI (interactive chat UI for testing agents)
devui:
    cd api && uv run python devui_server.py

# Run API tests (unit, security, integration — excludes eval)
test:
    cd api && uv run pytest

# Run integration tests against real Postgres (requires Docker)
test-integration:
    docker compose -f docker-compose.test.yml up -d --wait
    cd api && TEST_DATABASE_URL=postgresql://surf:test@localhost:5433/surf_test uv run pytest tests/integration/ -v --no-cov -m "integration or not integration" || true
    docker compose -f docker-compose.test.yml down

# Run E2E chat evaluation suite (requires running API on :8090)
eval:
    cd api && uv run pytest tests/eval/ -v --tb=short -s

# Run Playwright smoke tests (requires running API + web)
smoke:
    cd web && npx playwright test

# Run ingestion tests
test-ingestion:
    cd ingestion && uv run pytest

# Run all tests (unit, security, integration)
test-all: test test-integration

# Run security audits (pip-audit)
audit:
    cd api && uv run pip-audit
    cd ingestion && uv run pip-audit

# Lint all Python code
lint:
    cd api && uv run ruff check . && cd ../ingestion && uv run ruff check .

# Type-check all Python code
typecheck:
    cd api && uv run pyright && cd ../ingestion && uv run pyright

# Format all Python code
format:
    cd api && uv run ruff format . && cd ../ingestion && uv run ruff format .

# Create all search index schemas on the configured Azure AI Search service
setup-indexes:
    cd ingestion && uv run python -m src init-index --all

# Migrate all search indexes from a source service to the configured destination
# Usage: just migrate-search-index https://old-search-service.search.windows.net
migrate-search-index source_endpoint:
    cd ingestion && uv run python -m src migrate-index --source-endpoint "{{source_endpoint}}" --all

# Deploy minimal Azure resources for local development
# OpenAI is deployed to eastus2 (embedding model); everything else to australiaeast.
# Chat uses Anthropic Claude directly — set ANTHROPIC_API_KEY in .env after setup.
setup-dev rg="rg-surf-dev" rg_ai="rg-surf-dev-ai" location="australiaeast" location_ai="eastus2":
    #!/usr/bin/env bash
    set -euo pipefail

    RG="{{rg}}"
    RG_AI="{{rg_ai}}"
    LOCATION="{{location}}"
    LOCATION_AI="{{location_ai}}"
    STAMP="surf-dev-$(date +%Y%m%d%H%M%S)"

    SUB_NAME=$(az account show --query name -o tsv)
    SUB_ID=$(az account show --query id -o tsv)
    echo "Active subscription: $SUB_NAME ($SUB_ID)"
    read -rp "Continue with this subscription? [y/N] " CONFIRM
    [[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted. Use 'az account set -s <name>' to switch."; exit 1; }

    echo "Getting signed-in user..."
    USER_OID=$(az ad signed-in-user show --query id -o tsv)

    echo "Creating resource groups..."
    az group create --name "$RG"    --location "$LOCATION"    --output none
    az group create --name "$RG_AI" --location "$LOCATION_AI" --output none

    echo "Deploying Azure OpenAI to $RG_AI ($LOCATION_AI)..."
    az deployment group create \
      --resource-group "$RG_AI" \
      --template-file infra/dev-local-openai.bicep \
      --parameters userObjectId="$USER_OID" \
      --name "${STAMP}-ai" \
      --output none

    echo "Deploying remaining infrastructure to $RG ($LOCATION)..."
    az deployment group create \
      --resource-group "$RG" \
      --template-file infra/dev-local.bicep \
      --parameters userObjectId="$USER_OID" \
      --name "$STAMP" \
      --output none

    echo "Fetching deployment outputs..."
    AI_OUTPUTS=$(az deployment group list \
      --resource-group "$RG_AI" \
      --query "sort_by([?starts_with(name,'surf-dev-')],&properties.timestamp)[-1].properties.outputs" \
      --output json)

    OUTPUTS=$(az deployment group list \
      --resource-group "$RG" \
      --query "sort_by([?starts_with(name,'surf-dev-')],&properties.timestamp)[-1].properties.outputs" \
      --output json)

    OPENAI_ENDPOINT=$(echo "$AI_OUTPUTS" | python3 -c "import sys,json; print(json.load(sys.stdin)['openAiEndpoint']['value'])")
    SEARCH_ENDPOINT=$(echo "$OUTPUTS"    | python3 -c "import sys,json; print(json.load(sys.stdin)['searchEndpoint']['value'])")
    STORAGE_ENDPOINT=$(echo "$OUTPUTS"   | python3 -c "import sys,json; print(json.load(sys.stdin)['storageBlobEndpoint']['value'])")

    echo "Writing .env file..."
    cat > .env <<EOF
    # Generated by 'just setup-dev' — do not commit

    # Anthropic — chat agents (set your API key below)
    ANTHROPIC_API_KEY=
    ANTHROPIC_MODEL_ID=claude-sonnet-4-6

    # Azure OpenAI — embeddings only
    AZURE_OPENAI_ENDPOINT=${OPENAI_ENDPOINT}
    AZURE_OPENAI_API_VERSION=2024-12-01-preview

    # Azure AI Search
    AZURE_SEARCH_ENDPOINT=${SEARCH_ENDPOINT}
    AZURE_SEARCH_INDEX_NAME=surf-index

    # PostgreSQL (local Docker — defaults match docker-compose.yml)
    POSTGRES_HOST=localhost
    POSTGRES_PORT=5432
    POSTGRES_DATABASE=surf
    POSTGRES_USER=surf
    POSTGRES_PASSWORD=localdev
    POSTGRES_SSL=false

    # Azure Storage
    AZURE_STORAGE_ACCOUNT_URL=${STORAGE_ENDPOINT}

    # Application
    ENVIRONMENT=dev
    LOG_LEVEL=INFO
    EOF

    echo ""
    echo "Done! Your .env has been created."
    echo "IMPORTANT: Set ANTHROPIC_API_KEY in .env before running."
    echo "Run 'just dev' to start the API."

# Run web frontend in development mode
web:
    cd web && npm run dev

# Build web frontend for production
web-build:
    cd web && npm run build

# Lint and typecheck web frontend
web-lint:
    cd web && npm run typecheck

# Run desktop app in development mode (Tauri + Vite)
desktop:
    cd web && npm run tauri:dev

# Build desktop app for production
desktop-build:
    cd web && npm run tauri:build

# Install web frontend dependencies
web-install:
    cd web && npm install

# Install mobile app dependencies
mobile-install:
    cd mobile && npm install

# Start mobile app (Expo dev server)
mobile:
    cd mobile && npx expo start

# Start mobile app for iOS simulator
mobile-ios:
    cd mobile && npx expo start --ios

# Start mobile app for Android emulator
mobile-android:
    cd mobile && npx expo start --android

# Ask the dev agent a question about the codebase
ask *QUESTION:
    cd tools/dev-agent && npx tsx src/cli.ts {{QUESTION}}

# Start interactive dev agent REPL
ask-repl:
    cd tools/dev-agent && npx tsx src/cli.ts

# Install dev agent dependencies
dev-agent-install:
    cd tools/dev-agent && npm install

# Deploy API container to Azure Container Apps
api-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    TAG=$(git rev-parse --short HEAD)
    ACR_SERVER=$(az acr show --name acrsurfdev --resource-group rg-surf-dev --query loginServer -o tsv)
    IMAGE="${ACR_SERVER}/surf-api:${TAG}"
    echo "Logging in to ACR..."
    az acr login --name acrsurfdev
    echo "Building ${IMAGE}..."
    docker build --platform linux/amd64 -t "${IMAGE}" -f api/Dockerfile api/
    echo "Pushing ${IMAGE}..."
    docker push "${IMAGE}"
    echo "Updating Container App..."
    az containerapp update --name ca-api-surf-dev --resource-group rg-surf-dev --image "${IMAGE}" --output none
    echo "API deployed: ${IMAGE}"
    echo "Waiting for health check..."
    FQDN=$(az containerapp show --name ca-api-surf-dev --resource-group rg-surf-dev --query properties.configuration.ingress.fqdn -o tsv)
    for i in $(seq 1 30); do
        if curl -sf "https://${FQDN}/api/v1/health" > /dev/null 2>&1; then
            echo "API is healthy"
            break
        fi
        sleep 2
    done
    if [ "$i" -eq 30 ]; then
        echo "WARNING: Health check did not pass within 60 seconds"
    fi

# Deploy web frontend to Azure Container Apps (nginx reverse proxy)
web-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    TAG=$(git rev-parse --short HEAD)
    ACR_SERVER=$(az acr show --name acrsurfdev --resource-group rg-surf-dev --query loginServer -o tsv)
    IMAGE="${ACR_SERVER}/surf-web:${TAG}"
    # Read Entra env vars from web/.env.local (Vite doesn't load .env.local for production builds)
    if [[ -f web/.env.local ]]; then
        export $(grep -E '^VITE_ENTRA_' web/.env.local | xargs)
    fi
    VITE_ENTRA_TENANT_ID="${VITE_ENTRA_TENANT_ID:-$(az account show --query tenantId -o tsv)}"
    echo "Entra Client ID: ${VITE_ENTRA_CLIENT_ID:-not set}"
    echo "Entra Tenant ID: ${VITE_ENTRA_TENANT_ID}"
    echo "Building web SPA..."
    cd web && VITE_ENTRA_CLIENT_ID="${VITE_ENTRA_CLIENT_ID:-}" VITE_ENTRA_TENANT_ID="${VITE_ENTRA_TENANT_ID}" npm run build && cd ..
    echo "Logging in to ACR..."
    az acr login --name acrsurfdev
    echo "Building ${IMAGE}..."
    docker build --platform linux/amd64 -t "${IMAGE}" -f web/Dockerfile web/
    echo "Pushing ${IMAGE}..."
    docker push "${IMAGE}"
    echo "Updating Container App..."
    az containerapp update --name ca-web-surf-dev --resource-group rg-surf-dev --image "${IMAGE}" --output none
    echo "Web deployed: ${IMAGE}"
    echo "Waiting for health check..."
    FQDN=$(az containerapp show --name ca-web-surf-dev --resource-group rg-surf-dev --query properties.configuration.ingress.fqdn -o tsv)
    for i in $(seq 1 30); do
        if curl -sf "https://${FQDN}/healthz" > /dev/null 2>&1; then
            echo "Web is healthy"
            break
        fi
        sleep 2
    done
    if [ "$i" -eq 30 ]; then
        echo "WARNING: Health check did not pass within 60 seconds"
    fi

# Deploy infrastructure (Bicep) to Azure
infra-deploy rg="rg-surf-dev":
    #!/usr/bin/env bash
    set -euo pipefail
    RG="{{rg}}"
    echo "Validating Bicep template..."
    az deployment group validate \
      --resource-group "$RG" \
      --template-file infra/main.bicep \
      --parameters infra/environments/dev.bicepparam
    echo "Deploying infrastructure..."
    az deployment group create \
      --resource-group "$RG" \
      --template-file infra/main.bicep \
      --parameters infra/environments/dev.bicepparam \
      --name "surf-dev-$(date +%Y%m%d%H%M%S)"
    echo "Infrastructure deployed."

# Deploy ingestion container to Azure Container Apps
ingestion-deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    TAG=$(git rev-parse --short HEAD)
    ACR_SERVER=$(az acr show --name acrsurfdev --resource-group rg-surf-dev --query loginServer -o tsv)
    IMAGE="${ACR_SERVER}/surf-ingestion:${TAG}"
    echo "Logging in to ACR..."
    az acr login --name acrsurfdev
    echo "Building ${IMAGE}..."
    docker build --platform linux/amd64 -t "${IMAGE}" -f ingestion/Dockerfile ingestion/
    echo "Pushing ${IMAGE}..."
    docker push "${IMAGE}"
    echo "Updating Container App..."
    az containerapp update --name ca-ingestion-surf-dev --resource-group rg-surf-dev --image "${IMAGE}" --output none
    echo "Ingestion deployed: ${IMAGE}"

# Deploy both API and web frontend
deploy: api-deploy web-deploy

# Deploy everything (infra + all containers)
deploy-all: infra-deploy api-deploy web-deploy ingestion-deploy

# Sync files and pages from SharePoint to blob storage
sync-sharepoint *ARGS:
    cd ingestion && uv run python -m src sync-sharepoint {{ARGS}}

# Create the indexer pipeline (data source, index, skillset, indexer)
setup-indexer *ARGS:
    cd ingestion && uv run python -m scripts.setup_sharepoint_indexer {{ARGS}}

# Trigger an indexer run and optionally wait for completion
run-indexer *ARGS:
    cd ingestion && uv run python -m scripts.run_indexer {{ARGS}}

# Verify Microsoft Graph API access to SharePoint
verify-graph:
    cd ingestion && uv run python -m scripts.verify_graph_access

# Query the SharePoint index and validate results
validate-sharepoint *ARGS:
    cd ingestion && uv run python -m scripts.validate_sharepoint_index {{ARGS}}

# Diagnose the SharePoint indexing pipeline (blobs, index, indexer status)
diagnose-sharepoint *ARGS:
    cd ingestion && uv run python -m scripts.diagnose_sharepoint {{ARGS}}

# Upload a file to SharePoint via Graph API
upload-sharepoint FILE *ARGS:
    cd ingestion && uv run python -m scripts.upload_to_sharepoint "{{FILE}}" {{ARGS}}

# Full end-to-end: sync -> setup indexer -> run indexer -> validate
test-sharepoint-e2e *ARGS:
    cd ingestion && uv run python -m scripts.test_e2e_sharepoint {{ARGS}}

# Ingest local PDF files with manifest metadata
ingest *ARGS:
    cd ingestion && uv run python -m src ingest {{ARGS}}

# Deploy/update search index schema
init-index *ARGS:
    cd ingestion && uv run python -m src init-index {{ARGS}}

# Show search index statistics
index-status *ARGS:
    cd ingestion && uv run python -m src status {{ARGS}}

# Delete both dev resource groups and all their resources
teardown-dev rg="rg-surf-dev" rg_ai="rg-surf-dev-ai":
    #!/usr/bin/env bash
    set -euo pipefail
    SUB_NAME=$(az account show --query name -o tsv)
    echo "⚠ This will DELETE {{rg}} and {{rg_ai}} in subscription: $SUB_NAME"
    read -rp "Type 'yes' to confirm: " CONFIRM
    [[ "$CONFIRM" == "yes" ]] || { echo "Aborted."; exit 1; }
    az group delete --name "{{rg}}"    --yes --no-wait
    az group delete --name "{{rg_ai}}" --yes --no-wait
    echo "Resource group deletion initiated (runs in background)."
