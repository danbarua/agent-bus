# Harness notes

What each coding agent needs before it can join the bus, and what it cost to
find out. Moved here from the integration README, which is for running the
tests rather than for reading about them.

## codex

The one harness an API key alone does not satisfy. It defaults to ChatGPT OAuth
and returns `401 Missing bearer or basic authentication` with `OPENAI_API_KEY`
set and ignored. It wants `codex login --with-api-key`, which writes
`~/.codex/auth.json`; the container's entrypoint does that at start-up into its
own disposable HOME.

Its MCP server is a `-c` override, and the key must be a TOML **bare** key:
`mcp_servers.agent-bus=…`. Quoting it (`mcp_servers."agent-bus"`) parses fine
and then registers a server literally named `"agent-bus"`, quotes included —
its tools are unreachable, and the model improvises by shelling out and
reporting a success it did not have. That is exactly why the assertion is on
the bus and not on stdout.

It also passes its MCP child a **fixed** environment, so anything not named
there does not arrive. That is how its calls went unlogged, and how a container
run replaced a developer's macOS `.venv` with a Linux one:
`UV_PROJECT_ENVIRONMENT` was missing, so `uv run --project` fell back to
`<project>/.venv`, which in the container is the bind mount.

## grok

Needs the repo trusted as a folder. Discovery is not start: an untrusted
directory still *lists* the MCP server and then never launches it. In the image
that is a Dockerfile layer; on a host it is a manual ceremony, and the two are
not the same act — a disposable sandbox holding a checkout at a path that
exists nowhere else grants nothing on your machine.

## omp

Fetched as a prebuilt release binary rather than from npm. Its npm bin is a
`bun` script that loads a native module `npm install -g` never fetches, so it
lands on `PATH` and dies on first run. The Dockerfile's build-time check runs
every binary rather than just locating it, for that reason.

Its `xai-oauth/grok-4.6` default looks like it needs a browser login. It does
not; it reads `XAI_API_KEY` from the environment.

## pi

No MCP and no hooks — a shell, and nothing else. It joins by running the CLI,
and must pass `--pid $PPID`: inside its own shell tool that is *pi's* pid, and
without it the entry belongs to the CLI process, which exits immediately and is
pruned before anything can address it.

It drives the Claude-messaging tests deliberately. Measured: a pi-driven run
completes in **15s** against omp's minutes, and three of four omp round-trip
runs failed on omp's own side — MCP tools missing from its list, the send step
silently skipped. Every one of those failure modes is MCP-shaped, and pi has no
MCP to fail.

The harness with the least machinery finds the gaps, because nothing else is
papering over them. That is how `run_listen` publishing a working socket
without registering under its host pid was found: `send` could not locate it,
and every other harness got its listener from the other code path.

## Identity, for any MCP harness

Registered as `pending-<pid>` before it claims a name, because the MCP child
does not inherit the harness's session variables — grok's are hook-scoped. The
`initialize` handshake settles it: to the harness's kind if the client
identifies itself, otherwise to `other`, which is a settled answer and not a
missing one.

## Pinning a version

Every agent is a build arg, so reproducing a suspected regression is one
rebuild rather than a bisect against whatever the installer serves today:

```sh
docker build --target agents --build-arg GROK_VERSION=1.0.4 -t agent-bus:agents .
```

grok takes its version positionally (`install.sh | bash -s 1.0.5`).

## Do not bind-mount the peer directories

`/tmp/cc-socks` and `~/.claude/sessions` from the host, into the container.
Peers are identified by pid, and a host/container split puts the pid in
`sessions/<pid>.json`, the pid in the socket filename, and the pid
`getpeereid()` reports in three different namespaces.
