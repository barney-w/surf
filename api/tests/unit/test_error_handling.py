"""Tests for error handling and graceful degradation across the chat endpoint."""

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import Settings
from src.middleware.error_handler import LLM_TIMEOUT_SECONDS, add_error_handlers
from src.middleware.input_validation import MAX_MESSAGE_LENGTH, validate_message
from src.routes.chat import router as chat_router
from src.services.conversation import ConversationService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(
    workflow: object = None,
    conversation_service: ConversationService | None = None,
) -> FastAPI:
    """Build a minimal FastAPI app with chat routes and error handlers."""
    app = FastAPI()
    add_error_handlers(app)
    app.include_router(chat_router)

    if conversation_service is None:
        conversation_service = _make_conversation_service()
    app.state.workflow = workflow
    app.state.conversation_service = conversation_service
    return app


def _make_conversation_service() -> ConversationService:
    """Create a ConversationService with mocked PostgreSQL pool."""
    settings = Settings(_env_file=None, postgres_password="test")  # pyright: ignore[reportCallIssue]
    svc = ConversationService(settings)
    mock_conn = AsyncMock()
    # asyncpg's conn.transaction() is a regular method returning an async
    # context manager — use MagicMock so it isn't wrapped as a coroutine.
    txn_cm = MagicMock()
    txn_cm.__aenter__ = AsyncMock(return_value=None)
    txn_cm.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=txn_cm)
    mock_pool = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_conn
    cm.__aexit__.return_value = None
    mock_pool.acquire.return_value = cm
    svc._pool = mock_pool
    return svc


# ---------------------------------------------------------------------------
# Test 1 — LLM timeout returns structured error
# ---------------------------------------------------------------------------


class TestLLMTimeout:
    def test_llm_timeout_returns_504_structured_error(self):
        """When the workflow exceeds 30s the endpoint must return a 504 with type=llm_timeout."""

        async def _slow_run(
            msg: str, *, stream: bool = False, **kwargs: object
        ) -> AsyncGenerator[object, None]:
            await asyncio.sleep(LLM_TIMEOUT_SECONDS + 5)
            yield  # pragma: no cover – should never reach here

        workflow_obj = AsyncMock()
        workflow_obj.run = _slow_run

        svc = _make_conversation_service()
        app = _make_app(workflow=lambda: workflow_obj, conversation_service=svc)

        # Patch the timeout to be very short so the test is fast
        with patch("src.routes.chat.LLM_TIMEOUT_SECONDS", 0.05):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/chat",
                json={"message": "Hello"},
            )

        assert resp.status_code == 504
        body = resp.json()
        assert body["error"]["type"] == "llm_timeout"
        assert "too long" in body["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Test 2 — RAG failure produces low-confidence response
# ---------------------------------------------------------------------------


class TestRAGFailure:
    def test_rag_failure_returns_low_confidence(self):
        """If the workflow raises a generic error (e.g. RAG failure), the
        response must have confidence='low' and mention unavailable sources."""

        async def _failing_run(
            msg: str, *, stream: bool = False, **kwargs: object
        ) -> AsyncGenerator[object, None]:
            # Must be an async generator that raises
            if False:
                yield  # pragma: no cover — makes this an async generator
            raise RuntimeError("Search index unavailable")

        workflow_obj = AsyncMock()
        workflow_obj.run = _failing_run

        svc = _make_conversation_service()
        app = _make_app(workflow=lambda: workflow_obj, conversation_service=svc)
        client = TestClient(app)

        resp = client.post("/api/v1/chat", json={"message": "What is the leave policy?"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["response"]["confidence"] == "low"
        assert "unavailable" in body["response"]["message"].lower()


# ---------------------------------------------------------------------------
# Test 3 — Database unavailability doesn't crash the chat endpoint
# ---------------------------------------------------------------------------


class TestDatabaseUnavailability:
    def test_database_down_still_returns_response(self):
        """When the database throws, the chat endpoint should still work
        and return a warning header instead of crashing."""

        async def _ok_run(
            msg: str, *, stream: bool = False, **kwargs: object
        ) -> AsyncGenerator[object, None]:
            yield SimpleNamespace(
                type="output",
                data=SimpleNamespace(text="All good!", value=None),
            )

        workflow_obj = AsyncMock()
        workflow_obj.run = _ok_run

        # Make every database call explode
        svc = _make_conversation_service()
        db_err = Exception("Database down")
        svc.create_conversation = AsyncMock(side_effect=db_err)
        svc.get_conversation = AsyncMock(side_effect=db_err)
        svc.add_message = AsyncMock(side_effect=db_err)

        app = _make_app(workflow=lambda: workflow_obj, conversation_service=svc)
        client = TestClient(app)

        resp = client.post("/api/v1/chat", json={"message": "Hi there"})

        assert resp.status_code == 200
        assert resp.headers.get("X-Surf-Warning") == "db-unavailable"
        body = resp.json()
        # The AI response should still be present
        assert "All good" in body["response"]["message"]


# ---------------------------------------------------------------------------
# Test 4 — Overly long messages are rejected with 422
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_overlong_message_rejected_with_422(self):
        """Messages exceeding MAX_MESSAGE_LENGTH must be rejected before reaching the LLM."""
        svc = _make_conversation_service()
        app = _make_app(workflow=AsyncMock(), conversation_service=svc)
        client = TestClient(app)

        long_message = "a" * (MAX_MESSAGE_LENGTH + 1)
        resp = client.post("/api/v1/chat", json={"message": long_message})

        # Pydantic max_length on ChatRequest.message fires first (422)
        assert resp.status_code == 422

    def test_validate_message_strips_control_chars(self):
        """Control characters (null bytes, etc.) are removed from the message."""
        dirty = "Hello\x00World\x01!"
        clean = validate_message(dirty)
        assert "\x00" not in clean
        assert "\x01" not in clean
        assert clean == "HelloWorld!"

    def test_validate_message_allows_normal_whitespace(self):
        """Tabs, newlines, and carriage returns should be preserved."""
        msg = "Line one\nLine two\tTabbed\rReturn"
        assert validate_message(msg) == msg
