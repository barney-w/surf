import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential
from azure.identity import get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncAzureOpenAI

from src.config.settings import get_settings
from src.middleware.auth import get_current_user
from src.middleware.body_limit import BodySizeLimitMiddleware
from src.middleware.error_handler import add_error_handlers
from src.middleware.logging import reset_logging_context, set_logging_context, setup_logging
from src.middleware.telemetry import setup_telemetry
from src.orchestrator.builder import build_agent_graph, create_model_client
from src.orchestrator.history import ConversationHistoryProvider
from src.rag.tools import clear_search_clients, set_embed_func, set_search_client
from src.routes.chat import router as chat_router
from src.routes.user import router as user_router
from src.services.conversation import ConversationService
from src.services.graph import GraphService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monkey-patch: agent_framework_orchestrations HandoffBuilder injects
# ``store=False`` into agent options (an OpenAI Responses-API concept).
# The Anthropic client doesn't support ``store`` and forwards it to
# ``messages.create()``, causing a TypeError.  Strip it here until the
# framework ships a fix.
# ---------------------------------------------------------------------------
try:
    from agent_framework.anthropic import AnthropicClient as _AnthropicClient

    _orig_prepare = _AnthropicClient._prepare_options  # pyright: ignore[reportPrivateUsage]

    def _patched_prepare(  # type: ignore[override]
        self: object,
        messages: object,
        options: object,
        **kwargs: object,
    ) -> dict[str, object]:
        result = _orig_prepare(self, messages, options, **kwargs)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType,reportArgumentType]
        result.pop("store", None)  # pyright: ignore[reportUnknownMemberType]
        return result  # pyright: ignore[reportReturnType]

    _AnthropicClient._prepare_options = _patched_prepare  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
except (ImportError, ModuleNotFoundError, AttributeError):
    pass  # framework not installed or API changed — nothing to patch

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Handle application startup and shutdown events."""
    # --- Logging (first, so everything else logs in JSON) ---
    setup_logging(settings.log_level)

    # --- Telemetry (before agents, so their traces are captured) ---
    setup_telemetry(app, settings)

    # --- Production safety guards ---
    if settings.environment != "dev":
        if not settings.auth_enabled:
            logger.critical(
                "auth_enabled is False in '%s' environment — refusing to start",
                settings.environment,
            )
            raise SystemExit(1)
        if settings.debug:
            logger.critical(
                "debug mode is enabled in '%s' environment — refusing to start",
                settings.environment,
            )
            raise SystemExit(1)
        if "*" in settings.api_cors_origins:
            logger.critical(
                "CORS wildcard '*' is not allowed in '%s' environment — refusing to start",
                settings.environment,
            )
            raise SystemExit(1)
        if any(
            "localhost" in origin and "tauri.localhost" not in origin
            for origin in settings.api_cors_origins
        ):
            logger.critical(
                "CORS origins contain localhost in '%s' environment — refusing to start: %s",
                settings.environment,
                settings.api_cors_origins,
            )
            raise SystemExit(1)
        if not settings.postgres_ssl:
            logger.critical(
                "POSTGRES_SSL must be true in '%s' environment — refusing to start",
                settings.environment,
            )
            raise SystemExit(1)
    logger.info("CORS origins: %s", settings.api_cors_origins)

    # --- Azure AI Search ---
    if settings.azure_search_endpoint:
        credential = DefaultAzureCredential()
        search_client = SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index_name,
            credential=credential,
        )
        set_search_client(search_client)
        logger.info(
            "Azure AI Search client initialised (index=%s)",
            settings.azure_search_index_name,
        )

        if settings.azure_search_sharepoint_index:
            sp_search_client = SearchClient(
                endpoint=settings.azure_search_endpoint,
                index_name=settings.azure_search_sharepoint_index,
                credential=credential,
            )
            set_search_client(sp_search_client)
            logger.info(
                "SharePoint search client initialised (index=%s)",
                settings.azure_search_sharepoint_index,
            )
    else:
        logger.warning("AZURE_SEARCH_ENDPOINT not set — RAG tool will not be available")

    # --- Embedding client (for RAG hybrid search) ---
    if settings.azure_openai_endpoint:
        _token_provider = get_bearer_token_provider(
            SyncDefaultAzureCredential(),
            "https://cognitiveservices.azure.com/.default",
        )
        _openai_embed_client = AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            azure_ad_token_provider=_token_provider,
            api_version=settings.azure_openai_api_version,
        )
        _embed_deployment = settings.azure_openai_embedding_deployment_name

        async def _embed_query(text: str) -> list[float]:
            response = await _openai_embed_client.embeddings.create(
                input=text,
                model=_embed_deployment,
            )
            return response.data[0].embedding

        set_embed_func(_embed_query)
        logger.info(
            "Embedding client initialised (deployment=%s)",
            settings.azure_openai_embedding_deployment_name,
        )

    # --- Conversation service ---
    conversation_service: ConversationService | None = None
    if settings.postgres_enabled:
        conversation_service = ConversationService(settings)
        await conversation_service.initialize()

        # Run Alembic migrations
        import subprocess
        from pathlib import Path

        api_dir = Path(__file__).resolve().parent.parent  # api/src -> api/
        ssl_param = "?ssl=require" if settings.postgres_ssl else ""
        db_url = (
            f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}"
            f"/{settings.postgres_database}{ssl_param}"
        )
        migration_env = {**os.environ, "DATABASE_URL": db_url}
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=str(api_dir),
            capture_output=True,
            text=True,
            env=migration_env,
        )
        if result.returncode != 0:
            logger.error("Alembic migration failed: %s", result.stderr)
            raise SystemExit(1)
        logger.info("Database migrations applied")
        logger.info("ConversationService initialised")
    else:
        logger.warning("PostgreSQL disabled — conversation history will not persist")

    app.state.conversation_service = conversation_service

    # --- Graph API service (OBO for user profile / photo) ---
    graph_service = GraphService()
    app.state.graph_service = graph_service

    # --- History context provider ---
    history_provider = (
        ConversationHistoryProvider(conversation_service) if conversation_service else None
    )
    app.state.history_provider = history_provider

    # --- AI workflow ---
    if settings.azure_openai_endpoint:
        has_anthropic_creds = bool(
            settings.anthropic_api_key or settings.anthropic_foundry_base_url
        )
        if not has_anthropic_creds:
            logger.critical(
                "Anthropic credentials required when AZURE_OPENAI_ENDPOINT is set."
                " Set ANTHROPIC_API_KEY (direct) or ANTHROPIC_FOUNDRY_BASE_URL"
                " + ANTHROPIC_FOUNDRY_API_KEY (Azure AI Foundry) — refusing to start"
            )
            raise SystemExit(1)
        client = create_model_client(settings)

        # Build agents once; only the Workflow is recreated per request
        # (agent_framework Workflow is stateful and does not allow concurrent runs).
        context_providers = [history_provider] if history_provider else []
        agent_graph = build_agent_graph(client, settings, context_providers=context_providers)
        app.state.workflow = agent_graph.build_workflow
        logger.info("AI workflow factory initialised")
    else:
        logger.warning("AZURE_OPENAI_ENDPOINT not set — running in dev mode without AI workflow")
        app.state.workflow = None

    yield

    # Shutdown
    clear_search_clients()
    logger.info("Search clients cleared")
    await graph_service.close()
    logger.info("GraphService closed")
    if conversation_service:
        await conversation_service.close()
        logger.info("ConversationService closed")


app = FastAPI(
    title="Surf API",
    version="0.1.0",
    description="Multi-agent AI workplace assistant platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.api_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Conversation-ID", "X-User-ID"],
)
app.add_middleware(BodySizeLimitMiddleware)

from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402

from src.middleware.rate_limit import limiter  # noqa: E402

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]

add_error_handlers(app)
app.include_router(chat_router)
app.include_router(user_router)

if settings.environment == "dev" and settings.postgres_enabled:
    from src.routes.admin import router as admin_router

    app.include_router(admin_router)
    logger.warning("Admin dashboard enabled (dev environment)")


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log every request/response with timing and contextual IDs."""
    start = time.perf_counter()

    # Extract contextual IDs from headers (set by the frontend / gateway)
    set_logging_context(
        conversation_id=request.headers.get("x-conversation-id"),
        user_id=request.headers.get("x-user-id"),
        action=f"{request.method} {request.url.path}",
    )

    logger.info("Request started: %s %s", request.method, request.url.path)

    response: Response = await call_next(request)

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed: %s %s — %d (%.1fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )

    reset_logging_context()
    return response


@app.get("/api/v1/health")
async def health_check(request: Request, deep: bool = False) -> dict[str, object]:
    """Health check endpoint.

    Pass ?deep=true to verify connectivity to the database and Azure AI Search.
    Deep checks require authentication.
    """
    result: dict[str, object] = {"status": "healthy"}
    if not deep:
        return result

    # Deep health checks expose infrastructure details — require auth.
    await get_current_user(request)

    checks: dict[str, str] = {}
    conversation_service = getattr(app.state, "conversation_service", None)
    if conversation_service:
        try:
            healthy = await conversation_service.health_check()
            checks["database"] = "ok" if healthy else "error"
            if not healthy:
                result["status"] = "degraded"
        except Exception:
            checks["database"] = "error"
            result["status"] = "degraded"
    else:
        checks["database"] = "not_configured"

    # TODO: expose _search_client from tools.py for a live ping
    checks["search"] = "not_configured"

    result["checks"] = checks
    return result
