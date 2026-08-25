"""The opt-in gate for tests that spend money.

One home for it, because the failure mode is silence: without the variable
every test in this directory skips, and a run that tested nothing looks exactly
like a run that passed.
"""

from __future__ import annotations

import os

import pytest

VAR = "AGENT_BUS_RUN_SPENDY_E2E_TESTS"
OLD_VAR = "AGENT_BUS_INTEGRATION"

ENABLED = os.environ.get(VAR) == "1"

# Renamed because the old name described a category and not a consequence.
# Someone who set it once and moved on would otherwise get the silent skip --
# the exact failure the name now warns about -- so say so instead.
if not ENABLED and os.environ.get(OLD_VAR) == "1":
    raise RuntimeError(
        f"{OLD_VAR} is set but has been renamed. Use {VAR}=1 instead. "
        "Left alone, these tests would have skipped and the run would have "
        "passed having tested nothing."
    )

skip_unless_opted_in = pytest.mark.skipif(
    not ENABLED,
    reason=f"set {VAR}=1 to run tests that start real agents and cost money",
)
