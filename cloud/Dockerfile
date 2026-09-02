# The cloud server, for Cloud Run.
#
# Deliberately not a target in the repo-root Dockerfile. That one builds the bus
# and the five coding agents; this builds a thing that must never contain
# either. The separation is the one cloud/pyproject.toml already makes:
# `agent-bus-team` declares `dependencies = []` and means it, and Firestore is a
# dependency of the server alone.
#
# Build context is `cloud/`, so there is no path by which the bus could be
# copied in even by accident.

# --- build: resolve from the lockfile, and stay in this stage ---------------
#
# uv is here and not in the runtime image. What ships is the resolved venv; the
# installer, the resolver and the lockfile do not. An internet-facing OAuth
# server should not carry the tools that could change what it is running.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# `--frozen`: install exactly what uv.lock says or fail. A build that quietly
# re-resolved would put a version in production that no lockfile records, which
# is the one thing a lockfile exists to prevent.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- runtime ----------------------------------------------------------------
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=build /app/.venv /app/.venv

# Flat modules, not a package -- `cloud/` is never installed, only run here and
# imported by its tests, so a package boundary would buy nothing and cost an
# import prefix everywhere.
COPY app.py contract.py logs.py oauth.py store.py ./

# Not root. Cloud Run does not require it; a container that never needs to write
# anywhere should not be able to.
RUN useradd --system --uid 10001 --no-create-home bus
USER 10001

# Cloud Run sets PORT and terminates TLS itself. The server must never try to
# serve TLS -- see cloud/README.md.
ENV PORT=8080
EXPOSE 8080

# The running build, baked in rather than configured.
#
# `/health` and the MCP `serverInfo` report this, and until it existed both
# said `0+unknown` on every deploy the service has ever had -- so the endpoint
# whose whole job is telling an operator whether what they promoted is what is
# running could not answer.
#
# **A property of the image, not of the deployment.** Terraform could set it
# for production, but `infra/staging` has `ignore_changes` on the image because
# CI owns it there -- so terraform would be stating a version it does not know,
# and staging would report one build while running another. Baking it in is the
# only place the two cannot drift: the answer travels with the thing being
# identified.
#
# The default is the same string the code falls back to, so a local
# `docker build` with no `--build-arg` is honest about not being a release.
ARG VERSION=0+unknown
ENV AGENT_BUS_CLOUD_VERSION=${VERSION}

# `main`, not `serve`: it reads the config before constructing the Firestore
# client, so a revision with a missing signing key fails with the line naming
# the variable rather than with a Google credentials error that reads as an IAM
# problem. It then fails its startup probe and never takes traffic.
CMD ["python", "-c", "import app, store; app.main(store.Firestore)"]
