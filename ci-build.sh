#!/usr/bin/env bash
# The build gate: lint, unit suite, tier 1.
#
# One copy, three callers -- cloudbuild.yaml (before publishing),
# cloudbuild.test.yaml (on every pull request) and the `ci-build` compose
# service (locally). They used to hold identical inline copies of these
# commands, which meant adding the ruff step was the same edit made twice and
# remembered twice.
#
# Runs in the bare uv image, so it assumes nothing but uv and a Debian base.
set -euo pipefail

# agent-bus reads process start times with `ps -o lstart=` to guard against pid
# reuse. The uv image ships no ps, and without it proc_start() returns None and
# the guard degrades to a bare pid check -- silently. Skipped where ps already
# exists, so this is a no-op in an image that has it.
if ! command -v ps >/dev/null 2>&1; then
    apt-get update && apt-get install -y --no-install-recommends procps
fi

uv sync --group dev

# Lint first: ruff is deterministic and takes about a second, so it fails fast
# and never flakily. A lint failure then hides the test result, which is the
# accepted trade -- a ruff finding is fixable without knowing how tests went.
uv run ruff check

# The whole unit suite -- everything under tests/, which is where the CLI
# surface tests and the source guards live too. The integration tests are
# collected here as well and skip themselves without AGENT_BUS_INTEGRATION,
# which is why the next line exists.
uv run python -m pytest tests/ -q

# Tier 1 is the only credential-free tier: CLI only, no model, no network.
# Tiers 2-5 drive real agents and cost money per run; they live behind the
# manual trigger in cloudbuild.e2e.yaml.
AGENT_BUS_INTEGRATION=1 uv run python -m pytest tests/integration -q -k tier1
