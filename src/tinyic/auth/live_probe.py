"""The single production one-token live verification seam.

Both the headless ``tinyic doctor`` live check (:mod:`tinyic.auth.doctor`) and
the interactive ``tinyic onboard`` VERIFY gate (:mod:`tinyic.tui.onboard`) gate a
route on *exactly one* real completion.  Keeping that check in one place means
the two surfaces can never drift: they share this function as their default
``live_probe`` / ``verify_probe`` seam.

This never runs in the offline suite — every offline test injects a fake probe —
so it only fires for real when a human runs ``tinyic onboard``/``tinyic doctor
--live`` or under the ``live_api`` marker, where it may consume a token of quota.
"""

from __future__ import annotations

from typing import Any

__all__ = ["live_token_probe"]


def live_token_probe(binding: Any, candidate: Any | None) -> str:
    """Perform the explicit one-token live completion through the normal adapter.

    Returns the reason code ``"ok"`` when the provider produces a final message
    for a one-token request, else ``"probe_failed"``.  The binding is capped to a
    single token and the candidate carries the ephemeral credential under test.
    """
    from tinyic.models import ChatMessage, ChatRequest, FinalMessage, Role
    from tinyic.models.credentials import StaticCredentialProvider
    from tinyic.models.registry import get_provider

    probe_binding = binding.with_params(max_tokens=1)
    provider = get_provider(probe_binding.provider)
    credentials = candidate or StaticCredentialProvider({})
    transport = provider._new_child_transport(probe_binding, credentials)
    request = ChatRequest(
        (ChatMessage(Role.USER, "Reply with exactly one token: OK"),),
        probe_binding,
        stream=False,
    )
    return (
        "ok"
        if any(isinstance(event, FinalMessage) for event in transport.generate(request))
        else "probe_failed"
    )
