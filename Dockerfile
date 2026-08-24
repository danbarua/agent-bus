# Two targets:
#
#   ci      python + uv + the repo. Unit suite and tier 1. No agent binaries,
#           no credentials. This is what Cloud Build uses.
#   agents  ci + the five coding agents. The full e2e suite.
#
# Why two: CI needs none of the ~1GB of agent binaries, and pulling them for a
# unit run is waste. Both targets share the `base` stage, so the python side has
# one source of truth.
#
# Everything runs INSIDE one container. Do not bind-mount /tmp/cc-socks or
# ~/.claude/sessions from the host: agent-bus identifies peers by pid, and a
# host/container split puts the pid in sessions/<pid>.json, the pid in the socket
# filename, and the pid getpeereid() reports in three different namespaces.

# Stage order matters. Source is copied LAST in each target, and the harness
# installs live in their own stage, so editing a python file does not invalidate
# a gigabyte of npm downloads. Source changes constantly; harnesses almost never.

# Same base Cloud Build already uses (cloudbuild.yaml), so CI and local agree.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm AS base

# procps is not optional. agent-bus reads process start times with
# `ps -o lstart=` (src/agent_bus/process.py) to guard against pid reuse. Debian
# slim images ship no ps, and the failure is SILENT: proc_start() returns None,
# procStart is written as null, and the guard degrades to a bare os.kill(pid, 0)
# that cannot tell a live agent from a recycled pid. tests/test_process.py has a
# test that fails loudly if this package is missing.
#
# git is needed at build time because hatch-vcs derives the version from tags.
RUN apt-get update && apt-get install -y --no-install-recommends \
        procps \
        git \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# An unprivileged user, because Claude Code refuses to run:
#   --dangerously-skip-permissions cannot be used with root/sudo privileges
# The headless peer in tiers 3 and 4 needs that flag, so as root it exits rc=1
# before it can publish a session and there is nothing for a peer to message.
RUN useradd --create-home --uid 1000 --shell /bin/bash agent

# expanduser("~") is used for every default path (paths.py, store.py). Without an
# explicit HOME a passwd-less image resolves "~" to the literal string.
ENV HOME=/root

# Keep the venv OUTSIDE the working tree. docker-compose bind-mounts the source
# over /workspace/agent-bus for live editing, which would shadow a .venv created
# here at build time and leave `uv run` with nothing.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /workspace/agent-bus


# -------------------------------------------------------------------- ci target

FROM base AS ci

COPY . /workspace/agent-bus

# Dev deps are PEP 735 [dependency-groups], which plain pip cannot install.
RUN uv sync --group dev

# Prove the package imports and reports an honest version. If .git failed to
# reach the build context this prints 0+unknown, which is the signal that the
# MCP handshake would lie to peers about which agent-bus it is.
RUN uv run python -c "import agent_bus; print('agent-bus', agent_bus.__version__)"


# --------------------------------------------------------------- harness stage

FROM base AS harnesses

# Pin every harness. This is the whole point of the target: reproducing a
# suspected regression is
#   docker build --target agents --build-arg GROK_VERSION=1.0.4 .
# rather than a bisect against whatever the installer serves today.
# Defaults match the maintainer's machine, so container and host agree.
ARG CLAUDE_VERSION=2.1.241
ARG CODEX_VERSION=0.149.0
ARG PI_VERSION=0.84.2
ARG OMP_VERSION=18.0.3
ARG GROK_VERSION=1.0.5

# Debian bookworm ships Node 18; these packages want newer.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Three of the five publish usable npm packages, which pin exactly.
#
# omp is NOT among them despite having one. Its npm bin is `#!/usr/bin/env bun`
# and it loads a native module (pi_natives.linux-<arch>.node) that a plain
# `npm install -g` never fetches, so the binary lands on PATH and dies on first
# run. Installing bun does not fix it -- the native is still missing. It gets a
# prebuilt release binary below instead.
RUN npm install -g --no-fund --no-audit \
        "@anthropic-ai/claude-code@${CLAUDE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
        "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force

# omp: the prebuilt release artifact, which is what omp.sh/install fetches
# internally. Going straight to the URL rather than through the installer is
# deliberate -- the installer's own --ref flag drops into a from-source build
# that fails on unresolvable catalog deps, so it cannot pin a version. This can.
RUN set -e; \
    case "$(uname -m)" in \
        aarch64) omparch=arm64 ;; \
        x86_64)  omparch=x64 ;; \
        *) echo "unsupported arch for omp: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/can1357/oh-my-pi/releases/download/v${OMP_VERSION}/omp-linux-${omparch}" \
         -o /usr/local/bin/omp; \
    chmod +x /usr/local/bin/omp

# grok has no npm package. Its installer takes the version as a positional arg
# (`install.sh | bash -s 1.0.5`) and drops the binary in ~/.local/bin.
# Installed with HOME pointed at the agent user. The installer unpacks into
# $HOME/.grok/bin and symlinks /usr/local/bin/grok at it -- so installing as root
# leaves a world-visible symlink into an unreadable /root, and the unprivileged
# user gets "permission denied" from a binary that looks present. Same HOME also
# puts grok's config where its trust file below is written.
# HOME must be set on the BASH side of the pipe. `HOME=x curl ... | bash` sets it
# for curl, which does not care, and the installer still writes /root -- leaving
# /usr/local/bin/grok dangling for every other user. Cost an e2e run to find.
RUN curl -fsSL https://x.ai/cli/install.sh | HOME=/home/agent bash -s "${GROK_VERSION}"

# grok will not START a project-scoped MCP server in an untrusted folder -- it
# lists the server and then never launches it -- so its tier cannot run without
# this. tests/integration/README.md says a *test* must never write this file, and
# that rule stands: granting trust on a developer's own machine, silently, behind
# their back, is what the prompt exists to prevent.
#
# An image layer is not that. This container is a disposable sandbox, built
# deliberately by a human who typed `docker build`, containing a checkout at a
# path that exists nowhere else. Granting trust to /workspace/agent-bus inside it
# grants nothing on the host.
RUN mkdir -p /home/agent/.grok && printf '%s\n' \
    '# Written by the Dockerfile. See the comment there for why this is not the' \
    '# thing tests/integration/README.md forbids.' \
    '[folders."/workspace/agent-bus"]' \
    'trusted = true' \
    'decided_at = 0' \
    > /home/agent/.grok/trusted_folders.toml

# codex refuses MCP tool calls it considers unapproved, and the refusal is
# quiet in the worst way: the model reports success it did not have. Measured in
# this image --
#   mcp: agent-bus/register (failed)
#   MCP tool call requires approval, but approval policy is never
#   JOINED=smoke-codex          <- claimed anyway
# The tier only caught it because it asserts on the delivered message rather
# than on what codex printed.
#
# Default sandbox here is read-only, under which MCP calls need approval and
# there is nobody to give it. These two settings mirror the maintainer's own
# ~/.codex/config.toml, and are what a disposable container is for.
RUN mkdir -p /home/agent/.codex && printf '%s\n' \
    'sandbox_mode = "danger-full-access"' \
    'approval_policy = "never"' \
    > /home/agent/.codex/config.toml

RUN chown -R agent:agent /home/agent

# Fail the BUILD if a harness did not install, rather than failing the first test
# run with a skip that looks like "not available on this machine".
#
# It must RUN each binary, not just find it. The first version of this check
# only did `command -v` and printed `$(omp --version)` inside an echo -- so it
# passed while omp was broken, because a command substitution's failure does not
# fail the echo. `set -e` plus a bare invocation is what actually asserts.
RUN set -e; \
    for b in claude codex pi omp grok; do \
        command -v "$b" >/dev/null || { echo "MISSING BINARY: $b" >&2; exit 1; }; \
        "$b" --version >/dev/null 2>&1 || { echo "BROKEN BINARY: $b" >&2; "$b" --version; exit 1; }; \
        printf '%-8s %s\n' "$b" "$($b --version 2>&1 | head -1)"; \
    done


# ---------------------------------------------------------------- agents target

FROM harnesses AS agents

# Separate COPY so editing the entrypoint does not invalidate the source layer,
# and so it exists even when the source is bind-mounted over /workspace.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

COPY . /workspace/agent-bus
RUN uv sync --group dev

# Hand the venv, the checkout and a writable uv cache to the unprivileged user.
ENV UV_CACHE_DIR=/home/agent/.cache/uv
RUN mkdir -p "$UV_CACHE_DIR" \
    && chown -R agent:agent /opt/venv /workspace/agent-bus /home/agent

ENV HOME=/home/agent
USER agent

RUN uv run python -c "import agent_bus; print('agent-bus', agent_bus.__version__)"

# Re-run the harness check AS THE UNPRIVILEGED USER. The copy in the harnesses
# stage runs as root and is not enough: grok installs into a home directory and
# is reached through a symlink, so it can work perfectly for root while being
# invisible or unreadable to the user the tests actually run as. That happened,
# and the only symptom was `SKIPPED grok not on PATH` inside a green suite --
# a skip is how a broken harness hides.
RUN set -e; \
    for b in claude codex pi omp grok; do \
        command -v "$b" >/dev/null || { echo "NOT ON PATH FOR $(whoami): $b" >&2; exit 1; }; \
        "$b" --version >/dev/null 2>&1 || { echo "BROKEN FOR $(whoami): $b" >&2; "$b" --version; exit 1; }; \
        printf '%-8s %s\n' "$b" "$($b --version 2>&1 | head -1)"; \
    done

CMD ["bash"]
