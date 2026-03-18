# ---------------------------------------------------------------------------
# Agent discovery — scans for agent packages at startup.
#
# Convention: each agent lives in api/src/agents/<domain>/agent.py.
# This module imports every such file, which triggers the
# DomainAgent.__init_subclass__ hook and auto-registers the agent.
#
# Called once during app startup (typically from the orchestrator builder).
# ---------------------------------------------------------------------------

import contextlib
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path


def discover_agents() -> None:
    from src.agents._base import DomainAgent
    from src.agents._registry import AgentRegistry

    # Scan api/src/agents/ for sub-packages (hr/, it/, website/, etc.)
    agents_dir = Path(__file__).parent
    for _, module_name, is_pkg in pkgutil.iter_modules([str(agents_dir)]):
        # Skip private packages like _base, _registry, _discovery
        if is_pkg and not module_name.startswith("_"):
            # Import <domain>.agent — e.g. src.agents.hr.agent
            full_name = f"src.agents.{module_name}.agent"
            with contextlib.suppress(ModuleNotFoundError):
                importlib.import_module(full_name)

                # If the module was already imported (e.g. by a test),
                # __init_subclass__ won't fire again. Walk the module's
                # classes and register any DomainAgent subclasses that
                # the registry missed.
                mod = sys.modules.get(full_name)
                if mod:
                    for _, obj in inspect.getmembers(mod, inspect.isclass):
                        if (
                            issubclass(obj, DomainAgent)
                            and obj is not DomainAgent
                            and AgentRegistry.get(obj().name) is None
                        ):
                            AgentRegistry.register(obj)
