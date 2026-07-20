"""Studio research preflight and job running (HTTP-free, unit-testable).

Mirrors the CLI's persona-research flow (``persona_cli.research_persona``)
without printing or prompting: collision preflight first, provider imports
only after local checks pass, cost estimation from the shared
``personas.factory.estimate`` helpers. Nothing here spends money until
``ResearchJobs.start``.
"""

from __future__ import annotations

import contextlib
import io
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from tinyic.personas.factory import estimate as shared_estimate

__all__ = ["ResearchJobs", "ResearchPreflightError", "ResearchSeams", "preflight"]

# redirect_stdout/redirect_stderr swap process-global streams and are not
# concurrency-safe; studio request threads serialize their quiet first-import
# blocks through this lock so interleaved scopes can never corrupt sys.stdout.
_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ResearchSeams:
    """Injectable boundaries so studio tests never touch network/keyring."""

    credentials_factory: Any = None
    backend_maker: Any = None
    backend_selector: Any = None
    preset_loader: Any = None
    factory_class: Any = None


class ResearchPreflightError(Exception):
    """One reason-coded preflight failure, rendered as a JSON problem."""

    def __init__(self, status: int, reason: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.extra = extra


def _fail(status: int, reason: str, message: str, **extra: Any) -> None:
    raise ResearchPreflightError(status, reason, message, **extra)


def _validated_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce the JSON request body into a typed spec or fail with 400."""
    bad = lambda message: _fail(400, "invalid_research_request", message)
    name = spec.get("investor_name")
    if not isinstance(name, str) or not " ".join(name.split()):
        bad("investor_name must be a non-empty string")
    slug = spec.get("slug")
    if slug is not None and not isinstance(slug, str):
        bad("slug must be a string")
    model = spec.get("model")
    if model is not None and not isinstance(model, str):
        bad("model must be a provider/model string")
    max_searches = spec.get("max_searches")
    if max_searches is not None and (
        not isinstance(max_searches, int)
        or isinstance(max_searches, bool)
        or not 1 <= max_searches <= 16
    ):
        bad("max_searches must be an integer between 1 and 16")
    return {
        "investor_name": " ".join(name.split()),
        "slug": slug,
        "model": model,
        "max_searches": max_searches,
        "force": bool(spec.get("force")),
    }


def preflight(
    spec: Mapping[str, Any],
    seams: ResearchSeams,
    *,
    keep_backend: bool = False,
) -> tuple[dict[str, Any], Any | None]:
    """Run collision, lane, and cost preflight without spending.

    With ``keep_backend=True`` the selected backend stays open for the caller
    (the job runner reuses it); otherwise it is closed before returning.
    """
    request = _validated_spec(spec)
    from tinyic.personas.factory.pipeline import (
        PROTECTED_BUILTIN_SLUGS,
        resolve_personas_dir,
        slugify,
    )

    try:
        slug = slugify(
            request["slug"] if request["slug"] is not None else request["investor_name"]
        )
    except Exception as exc:
        _fail(400, "invalid_persona_slug", str(exc))
    if slug in PROTECTED_BUILTIN_SLUGS:
        _fail(
            409,
            "builtin_persona_collision",
            f"{slug!r} is a protected built-in persona; choose another slug",
        )
    output_dir = resolve_personas_dir()
    agent_path = output_dir / f"{slug}.agent.json"
    dossier_path = output_dir / f"{slug}.dossier.md"
    if not request["force"] and (agent_path.exists() or dossier_path.exists()):
        _fail(
            409,
            "persona_exists",
            f"persona {slug!r} already exists; retry with force to replace it",
        )

    backend: Any | None = None
    try:
        # Provider stack imports stay lazy and quiet, exactly like the CLI.
        with (
            _IMPORT_LOCK,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            from tinyic.auth import AuthManager
            from tinyic.models.binding import ModelBinding
            from tinyic.models.presets import load_preset
            from tinyic.models.research import (
                SEARCH_TOOL_FEES_USD,
                make_research_backend,
                select_research_backend,
            )
            from tinyic.usage import MODEL_PRICES_USD_PER_MILLION

        make_credentials = seams.credentials_factory or AuthManager.from_config
        credentials = make_credentials()
        budget_kwarg = (
            {} if request["max_searches"] is None
            else {"max_searches": request["max_searches"]}
        )
        if request["model"] is not None:
            maker = seams.backend_maker or make_research_backend
            backend = maker(ModelBinding(request["model"]), credentials, **budget_kwarg)
        else:
            load = seams.preset_loader or load_preset
            bindings = shared_estimate.configured_bindings(load(), ModelBinding)
            selector = seams.backend_selector or select_research_backend
            backend = selector(bindings, credentials, **budget_kwarg)
    except ResearchPreflightError:
        raise
    except Exception as exc:
        code = shared_estimate.reason_code(exc, "research_backend_error")
        if code == "no_search_capable_lane":
            _fail(
                403,
                code,
                "no verified API-key search lane; run 'tinyic onboard' for "
                "openai, grok, google, or kimi",
            )
        _fail(502, code, str(exc))

    try:
        provider = str(getattr(backend, "provider", "")).casefold()
        effective = shared_estimate.planned_budget(provider, request["max_searches"])
        from tinyic.personas.factory.pipeline import plan_queries

        try:
            plan = plan_queries(request["investor_name"], max_searches=effective)
        except Exception as exc:
            _fail(400, "invalid_research_budget", str(exc))
        priced = shared_estimate.priced_search_calls(
            provider, len(plan.queries), effective
        )
        model_ref = str(getattr(backend, "model_ref", request["model"] or "unknown"))
        estimate = shared_estimate.estimated_cost(
            model_ref,
            provider,
            priced,
            search_fees=SEARCH_TOOL_FEES_USD,
            price_table=MODEL_PRICES_USD_PER_MILLION,
        )
    except ResearchPreflightError:
        raise
    except Exception as exc:  # defensive: never leak a raw provider traceback
        shared_estimate.close_backend(backend)
        _fail(502, shared_estimate.reason_code(exc, "research_backend_error"), str(exc))

    payload = {
        "slug": slug,
        "investor_name": request["investor_name"],
        "model_ref": model_ref,
        "provider": provider,
        "effective_max_searches": effective,
        "estimate": estimate,
    }
    if keep_backend:
        return payload, backend
    shared_estimate.close_backend(backend)
    return payload, None


_JOB_BUFFER_LIMIT = 1000


class _Job:
    """One research run's event buffer; written by its worker, read by SSE."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.condition = threading.Condition()


class ResearchJobs:
    """Single-active-job runner feeding studio-local SSE buffers."""

    def __init__(self, seams: ResearchSeams | None = None, *, stderr=None) -> None:
        self._seams = seams or ResearchSeams()
        self._stderr = stderr
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._active_id: str | None = None
        self._starting = False

    def get(self, job_id: str) -> _Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_job(self) -> _Job | None:
        """The most recently started job, if any (done or not)."""
        with self._lock:
            return self._jobs.get(self._active_id) if self._active_id else None

    def notify_all_waiters(self) -> None:
        """Wake every SSE waiter (used by server shutdown)."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            with job.condition:
                job.condition.notify_all()

    def start(self, spec: Mapping[str, Any]) -> str:
        """Preflight, then run the factory on a worker thread. Returns job_id."""
        # ``_starting`` reserves the single-job slot across the slow preflight
        # so two concurrent confirms cannot both start paid runs.
        with self._lock:
            active = self._jobs.get(self._active_id) if self._active_id else None
            if self._starting or (active is not None and not active.done):
                raise ResearchPreflightError(
                    409, "research_job_active", "a research job is already running"
                )
            self._starting = True
        try:
            payload, backend = preflight(spec, self._seams, keep_backend=True)
        except BaseException:
            with self._lock:
                self._starting = False
            raise
        job = _Job(secrets.token_hex(4))
        with self._lock:
            self._jobs[job.job_id] = job
            self._active_id = job.job_id
            self._starting = False
        self._emit(job, "job_started", payload)
        worker = threading.Thread(
            target=self._run,
            args=(job, spec, payload, backend),
            name=f"tinyic-research-{job.job_id}",
            daemon=True,
        )
        worker.start()
        return job.job_id

    # -- worker ---------------------------------------------------------- #

    def _run(self, job: _Job, spec: Mapping[str, Any], payload: dict, backend: Any) -> None:
        try:
            factory_class = self._seams.factory_class
            if factory_class is None:
                with (
                    _IMPORT_LOCK,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    from tinyic.personas.factory import PersonaFactory

                factory_class = PersonaFactory
            from tinyic.personas.factory.pipeline import (
                ResearchRequest,
                resolve_personas_dir,
            )

            factory = factory_class(
                backend, progress=lambda stage: self._emit_stage(job, stage)
            )
            result = factory.run(
                ResearchRequest(
                    investor_name=payload["investor_name"],
                    output_dir=resolve_personas_dir(),
                    slug=payload["slug"],
                    max_searches=payload["effective_max_searches"],
                    force=bool(spec.get("force")),
                )
            )
        except Exception as exc:
            self._emit(
                job,
                "job_error",
                {
                    "reason": shared_estimate.reason_code(exc, "research_failed"),
                    "message": str(exc),
                },
            )
        else:
            usage = result.usage
            self._emit(
                job,
                "job_completed",
                {
                    "slug": result.slug,
                    "agent_path": str(result.agent_path),
                    "dossier_path": str(result.dossier_path),
                    "source_count": result.source_count,
                    "domain_count": result.domain_count,
                    "quality": result.quality,
                    "usage": {
                        "calls": getattr(usage, "calls", 0),
                        "search_calls": getattr(usage, "search_calls", 0),
                        "cost_usd": getattr(usage, "cost_usd", None),
                    },
                },
            )
        finally:
            shared_estimate.close_backend(backend)
            with job.condition:
                job.done = True
                job.condition.notify_all()

    # -- event emission ---------------------------------------------------- #

    def _emit(self, job: _Job, event_type: str, payload: dict) -> None:
        envelope = {
            "v": 1,
            "job_id": job.job_id,
            "seq": 0,  # assigned under the condition below
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "type": event_type,
            "payload": payload,
        }
        # Terminal events are exempt from the cap: the stream must always end
        # with an outcome the client can render, even for pathological runs.
        terminal = event_type in ("job_completed", "job_error")
        with job.condition:
            envelope["seq"] = len(job.events) + 1
            if terminal or len(job.events) < _JOB_BUFFER_LIMIT:
                job.events.append(envelope)
            job.condition.notify_all()

    def _emit_stage(self, job: _Job, stage: str) -> None:
        text = str(stage)
        if text.startswith("search:"):
            self._emit(job, "stage", {"stage": "search", "angle": text.split(":", 1)[1]})
        else:
            self._emit(job, "stage", {"stage": text})
