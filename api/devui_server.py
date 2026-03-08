"""Launch Surf in DevUI with per-run workflow isolation."""

from collections.abc import AsyncGenerator
from typing import Any

from agent_framework import CheckpointStorage, Workflow, WorkflowEvent
from agent_framework.devui import serve
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient

from src.config.settings import get_settings
from src.orchestrator.builder import build_orchestrator, create_model_client
from src.rag.tools import set_search_client


class DevUIStatelessWorkflow:
    """Wrap workflow execution so each DevUI run uses a fresh workflow instance."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._client = create_model_client(self._settings)
        self._template = build_orchestrator(self._client)
        self.name = getattr(self._template, "name", "surf")
        self.description = "Surf workflow (stateless DevUI wrapper)"

    @property
    def executors(self) -> dict[str, Any]:
        return self._template.executors

    def get_executors_list(self) -> list[Any]:
        return self._template.get_executors_list()

    def get_start_executor(self) -> Any:
        return self._template.get_start_executor()

    def to_dict(self) -> dict[str, Any]:
        return self._template.to_dict()

    def to_json(self) -> str:
        return self._template.to_json()

    async def _stream_with_auto_finalize(
        self, workflow: Workflow, message: Any | None, **kwargs: Any
    ) -> AsyncGenerator[WorkflowEvent, None]:
        pending_request_ids: list[str] = []
        async for event in workflow.run(message, stream=True, **kwargs):
            if event.type == "request_info":
                pending_request_ids.append(event.request_id)  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                continue
            yield event

        # Handoff workflows emit request_info to ask for "next user turn".
        # Auto-resolve these with empty responses so DevUI doesn't accumulate
        # stale "Workflow needs your input" cards between normal chat turns.
        while pending_request_ids:
            responses: dict[str, list[Any]] = {request_id: [] for request_id in pending_request_ids}
            pending_request_ids = []
            async for event in workflow.run(stream=True, responses=responses):
                if event.type == "request_info":
                    pending_request_ids.append(event.request_id)  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
                    continue
                yield event

    def run(
        self,
        message: Any | None = None,
        *,
        stream: bool = False,
        responses: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
        checkpoint_storage: CheckpointStorage | None = None,
        include_status_events: bool = False,
        **kwargs: Any,
    ) -> Any:
        workflow = build_orchestrator(self._client)
        if not stream:
            return workflow.run(
                message=message,
                stream=False,
                responses=responses,
                checkpoint_id=checkpoint_id,
                checkpoint_storage=checkpoint_storage,
                include_status_events=include_status_events,
                **kwargs,
            )

        if responses is not None:
            # Explicit HIL continuation from DevUI UI should pass through untouched.
            return workflow.run(
                stream=True,
                responses=responses,
                checkpoint_id=checkpoint_id,
                checkpoint_storage=checkpoint_storage,
                include_status_events=include_status_events,
                **kwargs,
            )

        return self._stream_with_auto_finalize(
            workflow,
            message,
            checkpoint_id=checkpoint_id,
            checkpoint_storage=checkpoint_storage,
            include_status_events=include_status_events,
            **kwargs,
        )


settings = get_settings()
if settings.azure_search_endpoint:
    set_search_client(
        SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_index_name,
            credential=DefaultAzureCredential(),
        )
    )

serve(entities=[DevUIStatelessWorkflow()], port=8091, auto_open=True)
