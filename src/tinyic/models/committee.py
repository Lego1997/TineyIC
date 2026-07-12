"""Resolve a :class:`~tinyic.models.presets.Preset` into live binding clients.

A :class:`Committee` is the debate's realized model plan: one
:class:`~tinyic.models.binding_client.BindingClient` per persona (keyed by the
persona's *display* name, matching ``TinyPerson.name`` in the orchestrator), one
for the aggregator (extraction + memo synthesis), and an optional moderator.
Building it here keeps preset resolution, credential wiring, and transport
construction out of the orchestrator, and gives tests one seam
(``transport_factory``) to inject fake transports for a fully offline debate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .binding import ModelBinding
from .binding_client import BindingClient, build_transport
from .credentials import CredentialProvider, EnvCredentialProvider
from .presets import Preset, validate_preset_thinking
from .types import Transport

#: Builds a bound transport for a binding; overridable in tests.
TransportFactory = Callable[[ModelBinding, CredentialProvider], Transport]


_COUNTER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "model_calls",
    "cached_calls",
)


@dataclass(frozen=True)
class Committee:
    """The resolved per-role binding clients for one debate."""

    persona_clients: dict[str, BindingClient]
    aggregator: BindingClient
    persona_bindings: dict[str, ModelBinding]
    aggregator_binding: ModelBinding
    moderator: BindingClient | None = None
    moderator_binding: ModelBinding | None = None
    preset_name: str = "default"
    stream: bool = True

    def client_for(self, persona_display_name: str) -> BindingClient | None:
        """The binding client routing ``persona_display_name``'s turns, if any."""
        return self.persona_clients.get(persona_display_name)

    def _all_clients(self) -> list[BindingClient]:
        clients = [*self.persona_clients.values(), self.aggregator]
        if self.moderator is not None:
            clients.append(self.moderator)
        return clients

    def aggregate_cost_stats(self) -> dict:
        """Sum every binding client's own usage into a model-attributed record.

        Returns a ``base_stats``-shaped dict (with ``by_model``) built from the
        per-binding counters — no process-global counter diff — so a debate's
        cost is priced per model even in a mixed-provider committee.
        """
        totals = {field: 0 for field in _COUNTER_FIELDS}
        by_model: dict[str, dict[str, int]] = {}
        for client in self._all_clients():
            stats = client.get_cost_stats()
            for field in _COUNTER_FIELDS:
                totals[field] += int(stats.get(field, 0))
            model_stats = stats.get("by_model") or {}
            for model_ref, per_model in model_stats.items():
                bucket = by_model.setdefault(
                    model_ref, {field: 0 for field in _COUNTER_FIELDS}
                )
                for field in _COUNTER_FIELDS:
                    bucket[field] += int(per_model.get(field, 0))
        result: dict = dict(totals)
        if by_model:
            result["by_model"] = by_model
        return result


def build_committee(
    preset: Preset,
    personas: Sequence[tuple[str, str]],
    *,
    credentials: CredentialProvider | None = None,
    registry: object | None = None,
    transport_factory: TransportFactory | None = None,
    stream: bool = True,
    validate_thinking: bool = True,
) -> Committee:
    """Realize ``preset`` for ``personas`` into a :class:`Committee`.

    ``personas`` is a sequence of ``(registry_name, display_name)`` pairs — the
    registry name selects the preset's persona binding, the display name keys
    the resulting client (so the orchestrator can look it up by ``agent.name``).
    ``transport_factory`` defaults to the provider-registry transport builder;
    tests pass one returning scripted transports for an offline debate.

    ``validate_thinking`` runs strict config-time thinking validation (FR-1.3)
    before any transport is built, so an unsupported preset level fails fast with
    a role-named error. Callers applying a per-debate ``--model``/``--thinking``
    override pass ``False`` to keep runtime remap semantics (the config was
    already validated at load; the override is remapped per call by the adapter).
    """
    creds = credentials if credentials is not None else EnvCredentialProvider()

    if validate_thinking:
        validate_preset_thinking(preset, registry=registry)

    def make_transport(binding: ModelBinding) -> Transport:
        if transport_factory is not None:
            return transport_factory(binding, creds)
        return build_transport(binding, creds, registry=registry)

    persona_clients: dict[str, BindingClient] = {}
    persona_bindings: dict[str, ModelBinding] = {}
    for registry_name, display_name in personas:
        binding = preset.persona_binding(registry_name)
        persona_bindings[display_name] = binding
        persona_clients[display_name] = BindingClient(
            binding, make_transport(binding), stream=stream
        )

    aggregator_binding = preset.aggregator_binding()
    aggregator = BindingClient(
        aggregator_binding, make_transport(aggregator_binding), stream=stream
    )

    moderator_binding = preset.moderator_binding()
    moderator = BindingClient(
        moderator_binding, make_transport(moderator_binding), stream=stream
    )

    return Committee(
        persona_clients=persona_clients,
        aggregator=aggregator,
        persona_bindings=persona_bindings,
        aggregator_binding=aggregator_binding,
        moderator=moderator,
        moderator_binding=moderator_binding,
        preset_name=preset.name,
        stream=stream,
    )


__all__ = ["Committee", "TransportFactory", "build_committee"]
