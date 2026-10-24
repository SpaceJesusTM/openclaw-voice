"""Named resolver and verifier plugins.

The hot-context cache and the job manager look components up by the names in
agent configuration. Built-in generic components register on import; optional
plugins (``plugins: [day_planner]`` in an agent file) register when loaded.
Adding a topic type means writing one resolver function and naming it in YAML.
"""

from __future__ import annotations

import importlib
from typing import Any, Awaitable, Callable

Resolver = Callable[..., Awaitable[Any]]
Verifier = Callable[..., Awaitable[Any]]


class ComponentRegistry:
    def __init__(self) -> None:
        self.resolvers: dict[str, Resolver] = {}
        self.verifiers: dict[str, Verifier] = {}
        self.plugins: set[str] = set()

    def resolver(self, name: str):
        def decorate(fn: Resolver) -> Resolver:
            self.resolvers[name] = fn
            return fn

        return decorate

    def verifier(self, name: str):
        def decorate(fn: Verifier) -> Verifier:
            self.verifiers[name] = fn
            return fn

        return decorate

    def load_plugin(self, name: str) -> None:
        if name in self.plugins:
            return
        module_name = name if "." in name else f"clawvoice.plugins.{name}"
        module = importlib.import_module(module_name)
        register = getattr(module, "register", None)
        if not callable(register):
            raise ValueError(f"Plugin {name!r} has no register(registry) function")
        register(self)
        self.plugins.add(name)

    def check_agent(self, agent) -> list[str]:
        """Names referenced by an agent's configuration that nothing provides."""
        missing = [f"resolver {t.resolver!r} ({t.key})" for t in agent.topics if t.resolver not in self.resolvers]
        missing += [
            f"verifier {r.verifier!r} ({r.name})"
            for r in agent.receipt_types.values()
            if r.verifier not in self.verifiers
        ]
        return missing


def default_registry() -> ComponentRegistry:
    registry = ComponentRegistry()
    from clawvoice.context import builtin
    from clawvoice.jobs import verifiers

    builtin.register(registry)
    verifiers.register(registry)
    return registry
