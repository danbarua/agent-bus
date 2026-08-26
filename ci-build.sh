#!/usr/bin/env bash
# The build gate: lint, then every test that does not cost money.
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

# Everything that does not cost money. Tests marked `spendy` start a real
# coding agent or a Claude session and skip themselves here; `./spendy_tests.sh`
# is what runs those, as does `docker compose run --rm e2e`.
#
# This used to end with a second pytest call selecting one group by name,
# because the whole integration directory was gated whether or not a test in it
# needed an agent. The ones that only drive the CLI are no longer gated, so
# they run in the line above with everything else.
uv run python -m pytest tests/ -q
