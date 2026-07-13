# TinyTroupe fork provenance

TinyIC vendors the Python package from
[microsoft/TinyTroupe](https://github.com/microsoft/TinyTroupe) under the
upstream MIT license in [`LICENSE`](LICENSE).

## Current upstream base

- Upstream tag: `v0.7.0` (a lightweight tag)
- Commit: `a6244b358a1fe1c71bf751f7ba0f8dfa368ec5a4`
- Tree: `ba09d5781c1d7a1bdeb6e49437df130f98855ca7`
- Upstream release commit date: 2026-03-28
- Verification: GitHub reports the commit signature as valid. The vendored
  package files were copied from the clean worktree at that exact commit.
- License: MIT, copyright Microsoft Corporation. The vendored `LICENSE` is
  byte-for-byte identical to the upstream file at this commit.

## Rebase method

1. Fetch the `v0.7.0` tag directly from the Microsoft repository and resolve
   it to the commit above.
2. Replace the contents of this directory with the contents of upstream's
   `tinytroupe/` package directory.
3. Keep only the local workspace packaging wrapper described below.
4. Compare every vendored package file other than the files listed under
   retained divergences against `v0.7.0` byte-for-byte.

This method intentionally adopts upstream's vision support and JSON API cache
format (`openai_api_cache.json`).

## Retained divergences

### Workspace packaging wrapper (`pyproject.toml`)

Upstream keeps `pyproject.toml` beside its `tinytroupe/` package directory.
TinyIC's uv workspace uses this vendored directory itself as a workspace
member, so it needs a local setuptools wrapper with `package-dir` mapping the
flattened source tree to the `tinytroupe` import package. The dependency set
and package version match upstream 0.7.0; `requires-python` is narrowed from
upstream's `>=3.10` to TinyIC's project-wide Python 3.12 requirement. FR-0.4
also adds the `Private :: Do Not Upload` classifier because v1 is distributed
from git source and must fail safe against accidental registry publication.

### Fork documentation and license (`FORK.md`, `LICENSE`)

These files record provenance and preserve the upstream license in the
vendored subtree. They are metadata, not changes to the upstream Python
package.

### Session-scoped registries (`session.py`, `agent/tiny_person.py`,
`environment/tiny_world.py`)

TinyIC adds a runtime-only `Session` ownership boundary around TinyTroupe's
agent and environment registries. Each session has locked name maps and an
idempotent lifecycle; closed sessions reject new registrations. The upstream
`TinyPerson.all_agents` and `TinyWorld.all_environments` APIs remain as
in-place aliases to a permanent default session for compatibility.

`TinyPerson` accepts an explicit session, registers and unregisters by object
identity, and rolls registration back if prompt reset or simulation attachment
fails. Specification loading, cloning, registry lookup, clearing, and global
cost helpers use an explicit session when supplied. The owning session is used
for complete-state relationship lookup and is excluded from JSON and complete
state serialization. An identity-safe `move_to_session` operation lets a
higher-level controller assemble separately loaded personas into one explicit
debate scope without touching the permanent compatibility registry; moving an
agent that is already attached to a world is rejected to preserve scoped state
lookup. Cross-session transfer is a single-threaded setup operation, not a
general concurrent migration primitive.

`TinyWorld` likewise accepts an explicit session and scopes registry lookup,
clearing, simulation attachment, and global cost helpers to it. Constructor
failure unregisters only the partially built world and detaches agents already
added to it. Complete-state encoding excludes the session and decoding resolves
agents within the world's owning session.
Real `TinyPerson` instances from a different session are rejected before they
can be attached to a world, preserving the ownership boundary. A world also
rejects agents already attached elsewhere and restores each object's prior
environment if constructor population fails partway through. Its idempotent
`dispose` operation detaches agents and unregisters subclass instances when
later initialization or a debate invocation fails.

These divergences fix TinyIC review defect A1: repeated debates in one process
can reuse persona and committee names without leaking process-global state.

### M1 output-integrity fixes (`__init__.py`,
`extraction/results_extractor.py`, `utils/behavior.py`, `utils/config.py`)

TinyIC changes `ConfigManager.config_defaults` so an explicit positional or
keyword `None` is preserved only for parameters mapped to
`max_content_display_length`; in that context it means unlimited rendering.
Other configured APIs retain upstream's `None`-means-default behavior. The
decorator reconstructs calls from bound arguments, avoiding the upstream
duplicate-argument error for positional `None`.

Bulk result extraction isolates exceptions per agent, records `None` for the
failed agent, and continues in input order. This prevents one provider failure
from discarding already extracted or subsequent committee votes.

Action repetition similarity uses multiset Jaccard over case-normalized token
bigrams rather than character multisets. This preserves frequency and local
claim order: exact and near repetition remain above the guard while reordered
opposite claims and two distinct 1,700-character investment speeches remain
below it. The upstream public helper names and `0.85` threshold stay
compatible.

File logging defaults to INFO in TinyIC's root configuration. The logging
initializer now persists its root level correctly, and every console/file
handler redacts known credential shapes and configured credential environment
values before output; exception tracebacks are omitted from ordinary logs
because provider bodies can embed secrets. Full prompts remain DEBUG-only.

The console `StreamHandler` created by `start_logger` (and lazily by
`set_console_loglevel`) writes to `sys.stderr` rather than upstream's
`sys.stdout` (M6). `tinyic debate --json` makes STDOUT a machine channel
carrying ONLY the append-only event JSONL (the public agent contract in
`docs/event-schema.md`); a diagnostic log line on STDOUT would corrupt that
stream. Routing the handler to STDERR keeps STDOUT clean while human-readable
progress and diagnostics stay on STDERR. This is the only behavioral change to
this file for M6.

### M1 client correctness (`clients/openai_client.py`,
`clients/ollama_client.py`)

The 0.7.0 rebase already removed TinyIC's old forced-stream proxy path and
preserved Pydantic `response_format` through OpenAI's native structured-output
method. M1 keeps those outcomes under explicit rebase-proof tests and does not
re-enable streaming in the legacy client; real token streaming remains owned
by the M2 provider adapters.

Request preparation is immutable so model-specific field adaptation cannot
change retry cache keys. The shared preparation seam adds
`stream_options={"include_usage": true}` whenever a caller explicitly requests
streaming, allowing M2 to reuse the tested contract without a schema change.
Local response-cache hits increment the cache-hit counter without adding the
stored response's tokens to billable usage a second time.
Reasoning-model requests remove unsupported optional parameters safely while
retaining `max_completion_tokens` and native structured-output parsing.

The Ollama compatibility client uses its local endpoint when the shared base
URL is unset, copies caller messages, and adds either a generic JSON-object
instruction or delimited JSON Schema when structured output is requested.
Typed returns use the shared Pydantic contract, and thread-scoped cache
invalidation lets a retry recover from a malformed structured response.

### M2 binding-routing hook (`clients/__init__.py`)

`client()` gains one optional resolver hook (`set_client_resolver`). The
resolver is consulted first and, when it returns a client-like object, that
object is used in place of the configured process-global client; returning
`None` (the inert default when no resolver is installed) preserves the legacy
path byte-for-byte. This is the single seam that lets the M2 model layer route
each persona's act loop and the aggregator's extraction/memo calls — both of
which reach the LLM through the module-level `client()` in
`agent/action_generator.py` and `extraction/results_extractor.py` — to their
own `ModelBinding`-backed client without editing those call sites. TinyIC
installs a resolver reading a context-scoped active binding client
(`tinyic.models.routing`); nothing in upstream uses the hook. The legacy
global `client()` remains the default-binding fallback until M6 removes it.

### M2 file-logging default (`config.ini`)

`LOGLEVEL_FILE` in the vendored default config is changed from `DEBUG` to
`INFO`. Config resolution overlays the vendored `src/tinytroupe/config.ini`
first, so a process whose working directory is the vendored package (or any
directory without a `config.ini` override) previously defaulted file logging to
DEBUG, which persists full prompt dumps to a `tinytroupe.<timestamp>.log` file.
Defaulting to INFO closes that cwd-gated prompt-to-disk leak (review D2) so the
divergence matches TinyIC's root `config.ini`; prompts remain DEBUG-only and
gated behind an explicit debug flag rather than the current directory.

### Known session-scope limits retained from upstream

M0 scopes the TinyIC debate path and the core agent/world complete-state APIs.
Upstream transaction-cache reference decoding in `control.py`,
`extraction/results_reducer.py`, `TinySocialNetwork`, and `TinyPersonFactory`
still resolve through legacy/default registries. TinyIC's M0 debate path does
not use those lookups. The PRD explicitly batches the fork-internal control
cache defect outside the product path into later cleanup; these paths must gain
an owning Session and acceptance tests before they are used by the v2 engine.

## Intentionally dropped TinyIC patches

- The OpenAI proxy patch that read a custom base URL, forced `stream=True`,
  collected streams back into a synthetic response, removed
  `response_format`, and special-cased GPT-5 models. The v2 provider adapters
  replace this path; preserving it would violate structured-output and usage
  requirements.
- Defensive `llama-index` import guards in `__init__.py`, `agent/grounding.py`,
  and `agent/memory.py`. Upstream 0.7.0 imports its declared dependencies
  directly, and TinyIC's supported installation resolves them.
- The local Pydantic `ConfigDict` conversion in
  `validation/simulation_validator.py`. It was not required to import or run
  TinyIC on the supported dependency set, so the upstream 0.7.0 source is
  retained unchanged.
- The old pickle cache default. Upstream 0.7.0's JSON cache implementation and
  `openai_api_cache.json` default are used.
