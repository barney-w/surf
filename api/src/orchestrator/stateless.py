from agent_framework import BaseContextProvider, SessionContext


class StatelessContextProvider(BaseContextProvider):
    """No-op provider that keeps agent runs stateless across workflow invocations."""

    async def before_run(self, *, agent, session, context: SessionContext, state: dict, **kw):
        return
