#!/usr/bin/env bash
# The tests that cost money and minutes.
#
#   ./spendy_tests.sh              every one of them
#   ./spendy_tests.sh roster      only tests/**/test_*roster*.py
#   ./spendy_tests.sh -k listener  anything else is passed straight to pytest
#
# There is no list of tests to keep up to date. The argument matches filenames,
# so a file you drop in the directory is runnable by name the moment it exists.
#
# Same shape as the `e2e` service in docker-compose.yml, so a local run and a
# container run leave the same kind of evidence behind: `--basetemp=.e2e`
# names each test's directory after itself (see `per_test_log_file` in
# tests/agent_bus/integration/conftest.py) instead of scattering them under
# pytest's own tmp root, `AGENT_BUS_LOG_LEVEL` defaults to INFO because
# unset means WARNING -- silent for the passing run this script exists to
# produce evidence from -- and empty directories are pruned after, not
# before: `-depth` deletes children before the parent, so a directory empty
# only because its own contents were just removed is caught in the same
# pass. The exit code is captured and restored around the prune so `set -e`
# does not skip it on exactly the run most worth keeping evidence from.
#
# Gitignored, and pytest empties an explicit basetemp at the start of every
# run, so `.e2e/` always holds exactly the last one -- nothing to clean up
# by hand between runs.
set -euo pipefail

cd "$(dirname "$0")"
export AGENT_BUS_RUN_SPENDY_E2E_TESTS=1
export AGENT_BUS_LOG_LEVEL="${AGENT_BUS_LOG_LEVEL:-INFO}"
BASETEMP="$(pwd)/.e2e"

_prune() {
    local rc=$1
    find "$BASETEMP" -mindepth 1 -depth -type d -empty -delete 2>/dev/null || true
    exit "$rc"
}

if [ $# -eq 0 ]; then
    rc=0
    uv run python -m pytest tests -m spendy -q -s --basetemp="$BASETEMP" || rc=$?
    _prune "$rc"
fi

# A leading dash means you know what you want; hand it over untouched.
case "$1" in
    -*)
        rc=0
        uv run python -m pytest tests -m spendy -q -s --basetemp="$BASETEMP" "$@" || rc=$?
        _prune "$rc"
        ;;
esac

# No mapfile: macOS ships bash 3.2 and does not have it.
files=()
while IFS= read -r f; do files+=("$f"); done < <(find tests -name "test_*$1*.py" | sort)
if [ ${#files[@]} -eq 0 ]; then
    echo "no test file matching '$1'. There are:" >&2
    find tests -name 'test_*.py' -path '*integration*' -o -name 'test_e2e.py' \
        | sort | sed 's/^/  /' >&2
    exit 2
fi

printf 'running:\n'
printf '  %s\n' "${files[@]}"
rc=0
uv run python -m pytest "${files[@]}" -m spendy -q -s --basetemp="$BASETEMP" "${@:2}" || rc=$?
_prune "$rc"
