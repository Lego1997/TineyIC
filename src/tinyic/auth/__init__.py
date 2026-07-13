"""Authentication profiles and provider subscription-lane support."""

from .doctor import (
    DOCTOR_SCHEMA_VERSION,
    DoctorReport,
    ProbeResult,
    ProbeStatus,
    build_report,
    render_human,
    run_doctor,
)
from .manager import AuthCandidate, AuthManager, AuthResolutionError
from .profiles import (
    AuthLane,
    AuthProfile,
    ProfileKind,
    ProfileStore,
    ProfileStoreError,
)

__all__ = [
    "DOCTOR_SCHEMA_VERSION",
    "DoctorReport",
    "ProbeResult",
    "ProbeStatus",
    "build_report",
    "render_human",
    "run_doctor",
    "AuthCandidate",
    "AuthManager",
    "AuthResolutionError",
    "AuthLane",
    "AuthProfile",
    "ProfileKind",
    "ProfileStore",
    "ProfileStoreError",
]
