"""Routing policy gate: may this task run on that provider, right now?

The policy is data (`routing_policy.yaml`, reviewed by compliance), loaded into a
`Policy`. A `Router` turns a policy plus the live situation (is the request
scrubbed, which providers are configured, which are healthy, which flags are set
in the environment) into a `Decision`. Flags are read from the environment at
decision time, never cached, so flipping `COMPLIANCE_DPA_OK` takes effect on the
next call and tests can monkeypatch it.

Nothing in this module sees message content.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ovis.inference.router")

POLICY_PATH_ENV = "INFERENCE_POLICY_PATH"
DEFAULT_POLICY_PATH = Path(__file__).with_name("routing_policy.yaml")

TRUTHY = frozenset({"1", "true", "yes"})
SCRUBBED_REQUIREMENT = "scrubbed"
ON_REFUSE_VALUES = frozenset({"scripted_fallback", "pending_clinician_review", "skip"})

# Every reason a Decision can carry. Call sites and tests key off these strings.
REASON_OK = "ok"
REASON_DPA_NOT_CONFIRMED = "dpa_not_confirmed"
REASON_NOT_SCRUBBED = "not_scrubbed"
REASON_PROVIDER_UNCONFIGURED = "provider_unconfigured"
REASON_PROVIDER_UNHEALTHY = "provider_unhealthy"
REASON_LEAK_CHECK_FAILED = "leak_check_failed"
REASON_POLICY_MISSING_TASK = "policy_missing_task"
REASONS = (
    REASON_OK, REASON_DPA_NOT_CONFIRMED, REASON_NOT_SCRUBBED, REASON_PROVIDER_UNCONFIGURED,
    REASON_PROVIDER_UNHEALTHY, REASON_LEAK_CHECK_FAILED, REASON_POLICY_MISSING_TASK,
)
# A false flag maps to a refusal reason; unknown flags fall back to "<flag>_not_confirmed".
FLAG_REASONS = {"dpa_ok": REASON_DPA_NOT_CONFIRMED}


class PolicyError(ValueError):
    """The policy file is missing, unreadable, or does not describe a valid policy."""


@dataclass(frozen=True)
class Flag:
    name: str
    env: str
    description: str = ""


@dataclass(frozen=True)
class ProviderPolicy:
    name: str
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskPolicy:
    name: str
    provider: str
    on_refuse: str


@dataclass(frozen=True)
class Decision:
    allow: bool
    provider: str | None
    reason: str
    on_refuse: str | None
    task: str

    def refused(self, reason: str) -> "Decision":
        """The same routing, but refused for `reason` (used for post-decision checks such as the leak check)."""
        return Decision(allow=False, provider=self.provider, reason=reason, on_refuse=self.on_refuse, task=self.task)


@dataclass(frozen=True)
class Policy:
    version: int
    flags: Mapping[str, Flag]
    providers: Mapping[str, ProviderPolicy]
    tasks: Mapping[str, TaskPolicy]
    source: str = "<dict>"

    # -- construction --------------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Policy":
        """Load the YAML policy: explicit `path` > $INFERENCE_POLICY_PATH > the file next to this module."""
        chosen = Path(path or os.getenv(POLICY_PATH_ENV) or DEFAULT_POLICY_PATH)
        try:
            raw = chosen.read_text(encoding="utf-8")
        except OSError as e:
            raise PolicyError(f"cannot read routing policy {chosen}: {type(e).__name__}") from None
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise PolicyError(f"routing policy {chosen} is not valid YAML: {type(e).__name__}") from None
        return cls.from_dict(data, source=str(chosen))

    @classmethod
    def from_dict(cls, data: Any, source: str = "<dict>") -> "Policy":
        if not isinstance(data, Mapping):
            raise PolicyError(f"{source}: policy must be a mapping")
        version = data.get("version")
        if version != 1:
            raise PolicyError(f"{source}: unsupported policy version {version!r} (expected 1)")

        flags: dict[str, Flag] = {}
        for name, spec in _mapping(data.get("flags") or {}, f"{source}: flags").items():
            spec = _mapping(spec, f"{source}: flags.{name}")
            env = spec.get("env")
            if not isinstance(env, str) or not env:
                raise PolicyError(f"{source}: flag {name!r} needs a non-empty 'env'")
            flags[str(name)] = Flag(name=str(name), env=env, description=str(spec.get("description") or ""))

        providers: dict[str, ProviderPolicy] = {}
        for name, spec in _mapping(data.get("providers") or {}, f"{source}: providers").items():
            spec = _mapping(spec or {}, f"{source}: providers.{name}")
            requires = spec.get("requires") or []
            if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
                raise PolicyError(f"{source}: providers.{name}.requires must be a list of names")
            for requirement in requires:
                if requirement != SCRUBBED_REQUIREMENT and requirement not in flags:
                    raise PolicyError(f"{source}: providers.{name} requires undeclared flag {requirement!r}")
            providers[str(name)] = ProviderPolicy(name=str(name), requires=tuple(requires))

        tasks: dict[str, TaskPolicy] = {}
        for name, spec in _mapping(data.get("tasks") or {}, f"{source}: tasks").items():
            spec = _mapping(spec, f"{source}: tasks.{name}")
            provider = spec.get("provider")
            if provider not in providers:
                raise PolicyError(f"{source}: tasks.{name} routes to undeclared provider {provider!r}")
            on_refuse = spec.get("on_refuse")
            if on_refuse not in ON_REFUSE_VALUES:
                raise PolicyError(f"{source}: tasks.{name}.on_refuse must be one of {sorted(ON_REFUSE_VALUES)}")
            tasks[str(name)] = TaskPolicy(name=str(name), provider=str(provider), on_refuse=str(on_refuse))

        return cls(version=1, flags=flags, providers=providers, tasks=tasks, source=source)

    # -- queries -------------------------------------------------------------
    def provider_for(self, task: str) -> str | None:
        policy = self.tasks.get(task)
        return policy.provider if policy else None

    def on_refuse_for(self, task: str) -> str | None:
        policy = self.tasks.get(task)
        return policy.on_refuse if policy else None

    def flag_value(self, name: str) -> bool:
        """Read a flag from the environment now. Unknown flags are false (fail closed)."""
        flag = self.flags.get(name)
        if flag is None:
            return False
        return (os.getenv(flag.env) or "").strip().lower() in TRUTHY

    def flag_values(self) -> dict[str, bool]:
        return {name: self.flag_value(name) for name in self.flags}

    def describe(self) -> dict:
        """Content-free summary for /florence/test and the startup log."""
        return {
            "flags": self.flag_values(),
            "tasks": {name: {"provider": t.provider, "on_refuse": t.on_refuse} for name, t in self.tasks.items()},
        }


def _mapping(value: Any, where: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{where} must be a mapping")
    return value


class Health:
    """Gateway-level circuit breaker: N consecutive call failures mark a provider unhealthy for a while."""

    def __init__(self, threshold: int = 3, cooldown_s: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._failures: dict[str, int] = {}
        self._unhealthy_until: dict[str, float] = {}

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._unhealthy_until.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        count = self._failures.get(provider, 0) + 1
        self._failures[provider] = count
        if count >= self.threshold:
            self._unhealthy_until[provider] = self._clock() + self.cooldown_s
            logger.warning("inference provider %s marked unhealthy after %d consecutive failures", provider, count)

    def failures(self, provider: str) -> int:
        return self._failures.get(provider, 0)

    def is_healthy(self, provider: str) -> bool:
        until = self._unhealthy_until.get(provider)
        if until is None:
            return True
        if self._clock() >= until:
            # Cooldown over: let one call through. Failures are kept so a further failure re-trips at once.
            self._unhealthy_until.pop(provider, None)
            return True
        return False

    def snapshot(self, providers: Iterable[str]) -> dict[str, bool]:
        return {name: self.is_healthy(name) for name in providers}


class Router:
    """Applies a `Policy` to one request's circumstances and returns a `Decision`."""

    def __init__(self, policy: Policy):
        self.policy = policy

    def decide(self, task: str, *, scrubbed: bool, providers: Mapping[str, Any], health: Health | None = None,
               provider: str | None = None) -> Decision:
        """Decide whether `task` may run.

        `provider` is an override (constructor `routes` or an `INFERENCE_ROUTE_*` env var) that takes
        precedence over the policy's `tasks.<task>.provider`. `providers` is the gateway's registry of
        configured providers. Checks run in this order: task known, provider configured, provider
        declared in the policy, provider healthy, then every `requires` entry of the provider.
        """
        on_refuse = self.policy.on_refuse_for(task)
        wanted = provider or self.policy.provider_for(task)
        if not wanted:
            return Decision(allow=False, provider=None, reason=REASON_POLICY_MISSING_TASK, on_refuse=on_refuse, task=task)

        if wanted not in providers:
            return Decision(allow=False, provider=wanted, reason=REASON_PROVIDER_UNCONFIGURED, on_refuse=on_refuse, task=task)
        declared = self.policy.providers.get(wanted)
        if declared is None:
            logger.warning("inference provider %s is configured but not declared in the routing policy; refusing", wanted)
            return Decision(allow=False, provider=wanted, reason=REASON_PROVIDER_UNCONFIGURED, on_refuse=on_refuse, task=task)

        if health is not None and not health.is_healthy(wanted):
            return Decision(allow=False, provider=wanted, reason=REASON_PROVIDER_UNHEALTHY, on_refuse=on_refuse, task=task)

        for requirement in declared.requires:
            if requirement == SCRUBBED_REQUIREMENT:
                if not scrubbed:
                    return Decision(allow=False, provider=wanted, reason=REASON_NOT_SCRUBBED, on_refuse=on_refuse, task=task)
            elif not self.policy.flag_value(requirement):
                reason = FLAG_REASONS.get(requirement, f"{requirement}_not_confirmed")
                return Decision(allow=False, provider=wanted, reason=reason, on_refuse=on_refuse, task=task)

        return Decision(allow=True, provider=wanted, reason=REASON_OK, on_refuse=on_refuse, task=task)

    def flag_value(self, name: str) -> bool:
        return self.policy.flag_value(name)

    def warnings(self, effective_routes: Mapping[str, str | None]) -> list[str]:
        """Operator-facing warnings about the current environment, e.g. a false flag that will refuse tasks.

        `effective_routes` maps task -> provider after overrides, so the warning names only providers
        that a task actually routes to.
        """
        messages: list[str] = []
        routed = {p for p in effective_routes.values() if p}
        for flag_name in self.policy.flags:
            if self.policy.flag_value(flag_name):
                continue
            affected = sorted(
                name for name, spec in self.policy.providers.items()
                if flag_name in spec.requires and name in routed
            )
            if affected:
                messages.append(f"inference policy {flag_name}=false: {'/'.join(affected)}-routed tasks will refuse")
        return messages
