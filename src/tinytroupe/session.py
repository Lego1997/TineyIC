"""Session-scoped registries for TinyTroupe agents and environments.

The upstream registry API is process-global.  TinyIC keeps that API available
through :func:`default_session`, while allowing each debate to own an isolated
``Session`` so repeated or concurrent work can safely reuse names.
"""

from __future__ import annotations

import threading
from typing import Any


class Session:
    """Own the agent and environment registries for one logical run.

    Registration and lifecycle changes are protected by a re-entrant lock.
    The dictionaries remain public for compatibility with TinyTroupe's legacy
    registry helpers; callers that mutate them directly are responsible for
    their own synchronization.
    """

    def __init__(self) -> None:
        self.agents: dict[str, Any] = {}
        self.environments: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether this session has ended and can no longer accept objects."""
        with self._lock:
            return self._closed

    def register_agent(self, agent: Any) -> None:
        """Register *agent*, allowing only the same identity to re-register."""
        with self._lock:
            self._ensure_open()
            if agent.name in self.agents:
                if self.agents[agent.name] is agent:
                    return
                raise ValueError(f"Agent name {agent.name} is already in use.")
            self.agents[agent.name] = agent

    def unregister_agent(self, agent: Any) -> None:
        """Remove only the registry entry whose value is exactly *agent*."""
        with self._lock:
            if self.agents.get(agent.name) is agent:
                del self.agents[agent.name]
                return
            for registered_name, registered_agent in tuple(self.agents.items()):
                if registered_agent is agent:
                    del self.agents[registered_name]
                    return

    def register_environment(self, environment: Any) -> None:
        """Register *environment*, allowing only its identity to re-register."""
        with self._lock:
            self._ensure_open()
            if environment.name in self.environments:
                if self.environments[environment.name] is environment:
                    return
                raise ValueError(
                    "Environment names must be unique, "
                    f"but '{environment.name}' is already defined."
                )
            self.environments[environment.name] = environment

    def unregister_environment(self, environment: Any) -> None:
        """Remove only the entry whose value is exactly *environment*."""
        with self._lock:
            if self.environments.get(environment.name) is environment:
                del self.environments[environment.name]
                return
            for registered_name, registered_environment in tuple(
                self.environments.items()
            ):
                if registered_environment is environment:
                    del self.environments[registered_name]
                    return

    def get_agent(self, name: str) -> Any | None:
        """Return the agent registered as *name*, if any."""
        with self._lock:
            return self.agents.get(name)

    def get_environment(self, name: str) -> Any | None:
        """Return the environment registered as *name*, if any."""
        with self._lock:
            return self.environments.get(name)

    def agent_names(self) -> list[str]:
        """Return a stable snapshot of registered agent names."""
        with self._lock:
            return list(self.agents)

    def agent_values(self) -> tuple[Any, ...]:
        """Return a stable snapshot of registered agents."""
        with self._lock:
            return tuple(self.agents.values())

    def environment_values(self) -> tuple[Any, ...]:
        """Return a stable snapshot of registered environments."""
        with self._lock:
            return tuple(self.environments.values())

    def clear_agents(self) -> None:
        """Clear the agent registry in place."""
        with self._lock:
            self.agents.clear()

    def clear_environments(self) -> None:
        """Clear the environment registry in place."""
        with self._lock:
            self.environments.clear()

    def close(self) -> None:
        """End the session and release its registries; safe to call repeatedly."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.agents.clear()
            self.environments.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Cannot register objects in a closed Session.")

    def __enter__(self) -> Session:
        with self._lock:
            self._ensure_open()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


_DEFAULT_SESSION = Session()


def default_session() -> Session:
    """Return the permanent compatibility session for unscoped callers."""
    return _DEFAULT_SESSION


__all__ = ["Session", "default_session"]
