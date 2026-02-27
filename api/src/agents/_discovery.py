import contextlib
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path


def discover_agents() -> None:
    from src.agents._base import DomainAgent
    from src.agents._registry import AgentRegistry

    agents_dir = Path(__file__).parent
    for _, module_name, is_pkg in pkgutil.iter_modules([str(agents_dir)]):
        if is_pkg and not module_name.startswith("_"):
            full_name = f"src.agents.{module_name}.agent"
            with contextlib.suppress(ModuleNotFoundError):
                importlib.import_module(full_name)
                # If the module was already imported, the __init_subclass__
                # hook won't fire again. Ensure any DomainAgent subclasses
                # found in the module are registered.
                mod = sys.modules.get(full_name)
                if mod:
                    for _, obj in inspect.getmembers(mod, inspect.isclass):
                        if (
                            issubclass(obj, DomainAgent)
                            and obj is not DomainAgent
                            and AgentRegistry.get(obj().name) is None
                        ):
                            AgentRegistry.register(obj)
