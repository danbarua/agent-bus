#!/usr/bin/env bash
# The tests that cost money and minutes.
#
#   ./spendy_tests.sh              every one of them
#   ./spendy_tests.sh roster      only tests/**/test_*roster*.py
#   ./spendy_tests.sh -k listener  anything else is passed straight to pytest
#
# There is no list of tests to keep up to date. The argument matches filenames,
# so a file you drop in the directory is runnable by name the moment it exists.
set -euo pipefail

cd "$(dirname "$0")"
export AGENT_BUS_RUN_SPENDY_E2E_TESTS=1

if [ $# -eq 0 ]; then
    exec uv run python -m pytest tests -m spendy -q -s
fi

# A leading dash means you know what you want; hand it over untouched.
case "$1" in
    -*) exec uv run python -m pytest tests -m spendy -q -s "$@" ;;
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
exec uv run python -m pytest "${files[@]}" -m spendy -q -s "${@:2}"
