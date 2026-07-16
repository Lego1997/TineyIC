"""Opt-in live verification for M3 provider/auth bindings.

Set ``TINYIC_LIVE_CONFIG`` to a config whose selected preset uses credentials
that may consume quota, then run this module explicitly with ``-m live_api``.
The doctor seam requests ``OK`` and constrains output to one token where the
selected official/provider runtime exposes such a control.
"""

from __future__ import annotations

import os

import pytest

from tinyic.auth.doctor import ProbeStatus, run_doctor


pytestmark = pytest.mark.live_api


def test_doctor_live_verifies_every_required_selected_binding() -> None:
    config_path = os.environ.get("TINYIC_LIVE_CONFIG")
    if not config_path:
        pytest.skip("TINYIC_LIVE_CONFIG is not set")

    report = run_doctor(
        config_path=config_path,
        preset=os.environ.get("TINYIC_LIVE_PRESET"),
        live=True,
    )
    required = [probe for probe in report.probes if probe.required]

    assert required
    assert report.ok, report.to_json()
    assert all(probe.status is ProbeStatus.OK for probe in required)
