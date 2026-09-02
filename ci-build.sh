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

# #190: a Protocol's isinstance check verifies member presence, never
# signatures, so a Transport contract drifted for weeks with every check
# green. ruff cannot catch that -- it never looks at a signature against a
# call site. src/ is what pyproject.toml's [tool.basedpyright] covers;
# cloud/ is excluded there deliberately (a separate deployable, its own
# venv, not installed here -- see the comment on that exclusion) and gets
# its own pass from its own project when someone picks that up (#228).
uv run basedpyright src/

# Everything that does not cost money. Tests marked `spendy` start a real
# coding agent or a Claude session and skip themselves here; `./spendy_tests.sh`
# is what runs those, as does `docker compose run --rm e2e`.
#
# This used to end with a second pytest call selecting one group by name,
# because the whole integration directory was gated whether or not a test in it
# needed an agent. The ones that only drive the CLI are no longer gated, so
# they run in the line above with everything else.
uv run python -m pytest tests/ -q

# The cloud server's own suite. Two invocations rather than one `testpaths`
# edit, because `cloud/` is a separate deployable with its own pyproject and
# its own dependencies -- `agent-bus-team` declares `dependencies = []` and
# means it, and the bus must never grow a dependency on Firestore to run its
# tests. `uv run` picks up cloud/pyproject.toml on its own; nothing is shared.
#
# This was missing entirely until #81. Five cloud pull requests came back green
# having executed none of the tests they added: ruff covers `cloud/` from the
# root, so the lint half looked right, which is the more misleading half.
#
# Ten of these need a Firestore emulator. They skip themselves when there is
# none -- correct on a laptop, and the skip reason carries the start command.
#
# In CI there IS one, started as a prior build step, and FIRESTORE_EMULATOR_HOST
# points at it. Then a skip is the bug rather than the behaviour: #81 was five
# pull requests going green having run no cloud test at all, and "the emulator
# did not come up so we quietly ran eight fewer tests" is the same failure with
# a smaller blast radius. So when the variable is set, the emulator is required.
if [ -n "${FIRESTORE_EMULATOR_HOST:-}" ]; then
    host="${FIRESTORE_EMULATOR_HOST%:*}"
    port="${FIRESTORE_EMULATOR_HOST##*:}"
    echo "waiting for the Firestore emulator on ${FIRESTORE_EMULATOR_HOST}..."
    for _ in $(seq 1 60); do
        # bash's /dev/tcp, so this needs no nc in an image that has none.
        (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null && break
        sleep 1
    done
    if ! (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
        echo "FIRESTORE_EMULATOR_HOST=${FIRESTORE_EMULATOR_HOST} was set and" >&2
        echo "nothing ever answered there. Refusing to run the store tests as" >&2
        echo "skips: a green build that tested less than it says is the whole" >&2
        echo "reason #81 and #85 exist." >&2
        exit 1
    fi
fi

( cd cloud && uv run --with pytest python -m pytest tests -q )
