"""Context-scoped routing of the vendored ``client()`` to a binding client.

FR-1.1 makes every LLM consumer resolve its own :class:`ModelBinding`.  The
vendored TinyTroupe act loop and vote extractor reach the model through the
module-level ``tinytroupe.clients.client()`` singleton, so this module owns the
single seam that lets each persona (and the aggregator) route through their own
:class:`~tinyic.models.binding_client.BindingClient` without editing those call
sites: a context variable holds the *active* binding client and the vendored
``client()`` consults it first (via ``set_client_resolver``), falling back to
the legacy process-global client whenever nothing is active.

The active client is a **context variable**, not a module global.  Each thread
(and each ``asyncio`` task) sees only the value set within its own context, so
two debates running in separate worker threads never observe one another's
routing — this is what closes M1's concurrent-usage-contamination handoff at
the routing layer.  ``activate`` is the only writer and always restores the
previous value, so nested activations (a persona turn inside which no aggregator
runs, or vice versa) compose correctly.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# The currently routed client for this execution context (``None`` = legacy).
_active_client: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "tinyic_active_binding_client", default=None
)
_resolver_installed = False


def active_client() -> Any | None:
    """Return the binding client routed for this context, or ``None``."""
    return _active_client.get()


def install_client_resolver() -> None:
    """Install the vendored ``client()`` resolver once (idempotent).

    Safe to call repeatedly; the hook simply delegates to :func:`active_client`,
    so installing it has no effect on unrouted runs (the resolver returns
    ``None`` and the legacy client is used unchanged).
    """
    global _resolver_installed
    if _resolver_installed:
        return
    from tinytroupe.clients import set_client_resolver

    set_client_resolver(active_client)
    _resolver_installed = True


@contextmanager
def activate(client: Any | None) -> Iterator[Any | None]:
    """Route the vendored ``client()`` to ``client`` for the duration.

    Installs the resolver lazily on first use so importing the model layer
    mutates no global state.  A ``None`` client is a no-op scope (keeps the
    legacy path), which lets callers activate unconditionally with an optional
    binding.
    """
    install_client_resolver()
    token = _active_client.set(client)
    try:
        yield client
    finally:
        _active_client.reset(token)


__all__ = ["activate", "active_client", "install_client_resolver"]
