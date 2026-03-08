from typing import Any

from agent_framework import AgentSession, BaseContextProvider, SessionContext, SupportsAgentRun


class StatelessContextProvider(BaseContextProvider):
    """No-op provider that keeps agent runs stateless across workflow invocations."""

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        return
