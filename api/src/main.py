import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from azure.identity import DefaultAzureCredential as SyncDefaultAzureCredential, get_bearer_token_provider
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncAzureOpenAI

from src.config.settings import get_settings
from src.middleware.error_handler import add_error_handlers
from src.middleware.logging import reset_logging_context, set_logging_context, setup_logging
from src.middleware.telemetry import setup_telemetry
from src.orchestrator.builder import build_orchestrator, create_model_client
from src.orchestrator.history import ConversationHistoryProvider
from src.rag.tools import set_embed_func, set_search_client
from src.routes.chat import router as chat_router
from src.services.conversation import ConversationService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monkey-patch: agent_framework_orchestrations HandoffBuilder injects
# ``store=False`` into agent options (an OpenAI Responses-API concept).
# The Anthropic client doesn't support ``store`` and forwards it to
# ``messages.create()``, causing a TypeError.  Strip it here until the
# framework ships a fix.
# ---------------------------------------------------------------------------
try:
    from agent_framework.anthropic import AnthropicClient as _AC

    _orig_prepare = _AC._prepare_options

    def _patched_prepare(self, messages, options, **kwargs):  # type: ignore[override]
        result = _orig_prepare(self, messages, options, **kwargs)
        result.pop("store", None)
        return result

    _AC._prepare_options = _patched_prepare  # type: ignore[assignment]
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

    # --- Azure AI Search ---
    if settings.azure_search_endpoint:
        search_client = SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index_name,
            credential=DefaultAzureCredential(),
        )
        set_search_client(search_client)
        logger.info("Azure AI Search client initialised")
    else:
        logger.warning(
            "AZURE_SEARCH_ENDPOINT not set — RAG tool will not be available"
        )

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
    conversation_service = ConversationService(settings)
    if settings.cosmos_endpoint:
        await conversation_service.initialize()
        logger.info("ConversationService initialised")
    else:
        logger.warning(
            "COSMOS_ENDPOINT not set — ConversationService not connected"
        )
    app.state.conversation_service = conversation_service

    # --- History context provider ---
    history_provider = ConversationHistoryProvider(conversation_service)
    app.state.history_provider = history_provider

    # --- AI workflow ---
    if settings.azure_openai_endpoint:
        client = create_model_client(settings)
        # Store a factory so each request gets a fresh Workflow instance.
        # agent_framework Workflow is stateful and does not allow concurrent runs.
        def _make_workflow():
            return build_orchestrator(client, context_providers=[history_provider])

        app.state.workflow = _make_workflow
        logger.info("AI workflow factory initialised")
    else:
        logger.warning(
            "AZURE_OPENAI_ENDPOINT not set — running in dev mode without AI workflow"
        )
        app.state.workflow = None

    yield

    # Shutdown
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

add_error_handlers(app)
app.include_router(chat_router)


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: object) -> Response:
    """Log every request/response with timing and contextual IDs."""
    start = time.perf_counter()

    # Extract contextual IDs from headers (set by the frontend / gateway)
    set_logging_context(
        conversation_id=request.headers.get("x-conversation-id"),
        user_id=request.headers.get("x-user-id"),
        action=f"{request.method} {request.url.path}",
    )

    logger.info("Request started: %s %s", request.method, request.url.path)

    response: Response = await call_next(request)  # type: ignore[call-arg]

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
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
