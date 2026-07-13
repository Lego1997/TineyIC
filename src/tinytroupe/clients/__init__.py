import logging

from tinytroupe import config_manager, utils

from .azure_client import AzureClient
from .ollama_client import OllamaClient
from .openai_client import LLMCacheBase, OpenAIClient

logger = logging.getLogger("tinytroupe")

###########################################################################
# Exceptions
###########################################################################


class InvalidRequestError(Exception):
    """
    Exception raised when the request to the OpenAI API is invalid.
    """

    pass


class NonTerminalError(Exception):
    """
    Exception raised when an unspecified error occurs but we know we can retry.
    """

    pass


###########################################################################
# Clients registry
#
# We can have potentially different clients, so we need a place to
# register them and retrieve them when needed.
#
# We support both OpenAI and Azure OpenAI Service API by default.
# Thus, we need to set the API parameters based on the choice of the user.
# This is done within specialized classes.
#
# It is also possible to register custom clients, to access internal or
# otherwise non-conventional API endpoints.
###########################################################################
_api_type_to_client = {}
_api_type_override = None

# TinyIC divergence (M2 FR-1.1/FR-1.5): a single, optional resolver hook lets a
# higher layer route ``client()`` to a per-consumer, binding-backed client
# without editing every vendored call site (the persona act loop in
# ``agent/action_generator.py`` and vote extraction in
# ``extraction/results_extractor.py`` both call ``client()``). The hook is
# consulted first; returning ``None`` (its inert default) preserves the legacy
# process-global client exactly, so unrouted runs are byte-for-byte unchanged.
_client_resolver = None


def set_client_resolver(resolver):
    """Install (or clear with ``None``) the optional per-call client resolver.

    ``resolver`` is a zero-argument callable returning either a client-like
    object implementing ``send_message`` or ``None`` to fall back to the
    configured process-global client. TinyIC installs one that consults a
    context-scoped active :class:`ModelBinding` client so each persona and the
    aggregator route through their own model; nothing else uses it.
    """
    global _client_resolver
    _client_resolver = resolver


def register_client(api_type, client):
    """
    Registers a client for the given API type.

    Args:
    api_type (str): The API type for which we want to register the client.
    client: The client to register.
    """
    _api_type_to_client[api_type] = client


def _get_client_for_api_type(api_type):
    """
    Returns the client for the given API type.

    Args:
    api_type (str): The API type for which we want to get the client.
    """
    try:
        return _api_type_to_client[api_type]
    except KeyError:
        raise ValueError(
            f"API type {api_type} is not supported. Please check the 'config.ini' file."
        )


def client():
    """
    Returns the client for the configured API type.
    """
    # TinyIC divergence (M2): consult the optional per-call resolver first. When
    # it yields a client (a routed persona/aggregator turn), use it; otherwise
    # fall through to the unchanged configured process-global client.
    if _client_resolver is not None:
        resolved = _client_resolver()
        if resolved is not None:
            return resolved

    api_type = (
        config_manager.get("api_type")
        if _api_type_override is None
        else _api_type_override
    )

    logger.debug(f"Using  API type {api_type}.")
    return _get_client_for_api_type(api_type)


# TODO simplify the custom configuration methods below


def force_api_type(api_type):
    """
    Forces the use of the given API type, thus overriding any other configuration.

    Args:
    api_type (str): The API type to use.
    """
    global _api_type_override
    _api_type_override = api_type


@config_manager.config_defaults(cache_file_name="cache_file_name")
def force_api_cache(cache_api_calls, cache_file_name=None):
    """
    Forces the use of the given API cache configuration, thus overriding any other configuration.

    Args:
    cache_api_calls (bool): Whether to cache API calls.
    cache_file_name (str): The name of the file to use for caching API calls.
    """
    # set the cache parameters on all clients
    for client in _api_type_to_client.values():
        client.set_api_cache(cache_api_calls, cache_file_name)


# default client
register_client("openai", OpenAIClient())
register_client("azure", AzureClient())
register_client("ollama", OllamaClient())
