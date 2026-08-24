#!/usr/bin/env bash
# Runtime credential wiring for the agents image.
#
# Four of the five harnesses read their API key straight from the environment
# and need nothing here. codex does not: it defaults to ChatGPT OAuth and
# returns
#
#   401 Unauthorized: Missing bearer or basic authentication in header
#
# with OPENAI_API_KEY set and ignored. It wants an explicit
# `codex login --with-api-key`, which writes ~/.codex/auth.json.
#
# That cannot be an image layer -- there is no key at build time, and baking one
# into a layer would leave it in the image history forever. So it happens here,
# at container start, writing into the container's own disposable HOME.
set -euo pipefail

if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    # `login status` exits non-zero when logged out; only pay for the login once.
    if ! codex login status >/dev/null 2>&1; then
        # Piped from printenv so the key is never an argv entry, where it would
        # show up in `ps` for any other process in the container.
        if printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null 2>&1; then
            echo "[entrypoint] codex: logged in with API key" >&2
        else
            # Not fatal. codex's tier will fail and say so; the other four
            # harnesses have no reason to be blocked by it.
            echo "[entrypoint] codex: API key login FAILED -- its tier will fail" >&2
        fi
    fi
else
    echo "[entrypoint] codex: OPENAI_API_KEY unset -- its tier will fail" >&2
fi

exec "$@"
