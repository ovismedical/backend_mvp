"""Tools the gateway may run inside a task.

A `ToolSpec` is a name, a JSON-schema parameter block and an executor. The executor is a plain
callable of the decoded arguments: it never sees the model, and it is the only place a tool touches
application state. A `ToolRegistry` is the set of specs one request may call.

Registration is not permission. The routing policy's `tools` block decides whether a registered
tool may actually run, so adding an executor here is never on its own enough to make a tool
callable -- both the registry and the policy have to name it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class ToolArgumentError(ValueError):
    """The model called a tool with arguments it cannot use.

    Reported back to the model as a typed error so it can correct itself on the next hop; it never
    propagates out of the gateway and never ends the patient's turn.
    """


@dataclass(frozen=True)
class ToolCall:
    """One call the model asked for. `arguments` is raw JSON text exactly as the model emitted it."""
    name: str
    arguments: str
    call_id: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    executor: Callable[[dict], Any]

    def wire_format(self) -> dict:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """The tools one request may call, keyed by name."""

    def __init__(self, specs: Iterable[ToolSpec] = ()):
        self._specs: dict[str, ToolSpec] = {spec.name: spec for spec in specs}

    def __bool__(self) -> bool:
        return bool(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def wire_format(self) -> list[dict]:
        return [spec.wire_format() for spec in self._specs.values()]


__all__ = ["ToolArgumentError", "ToolCall", "ToolRegistry", "ToolSpec"]
