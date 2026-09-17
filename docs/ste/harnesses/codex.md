# codex

The `codex-messaging-reference.md` file documents codex's app-server API.

## Authentication

Codex authenticates through ChatGPT OAuth by default. A bare
`OPENAI_API_KEY` returns `401 Missing bearer or basic authentication`.
Codex ignores the variable. `codex login --with-api-key` authenticates
codex and writes `~/.codex/auth.json`. The container's entrypoint runs this
command at start-up, into its own disposable HOME.

## Model selection

`codex exec -m <model>` selects a model by id. Codex has no command to list
model ids during a headless run. `codex models` requires a terminal and
exits with `Error: stdin is not a terminal`. Check a model id
interactively, or take one from a harness that can list models, such as
`omp models`. `-c model="<id>"` sets the same model through the override
channel the MCP config uses.

Codex warns when the configured service tier does not advertise a model:
`service tier 'priority' is not advertised ... will be omitted from
requests`. Codex runs the model anyway. The warning is noise.

## MCP server registration

Codex registers its MCP server through a `-c` override. The key must be a
TOML bare key: `mcp_servers.agent-bus=…` works. A quoted key,
`mcp_servers."agent-bus"`, also parses. It registers a server named
`"agent-bus"` with the quotes included.

Its tools become unreachable. When this happens, the model shells out and
reports a success it did not have. Verify delivery against the bus, not
against stdout.

## MCP child environment

Codex hands its MCP child a fixed environment. The child receives only
`HOME LANG LOGNAME PATH SHELL TERM TMPDIR USER __CF_USER_TEXT_ENCODING`.
The child receives no thread id, no session id, and no socket path.

Two missing variables have caused silent failures.

`AGENT_BUS_LOG_*` was missing, so codex's MCP calls were not logged. Codex
does not read the environment at call time, so the missing variable
produced no visible error.

`UV_PROJECT_ENVIRONMENT` was missing, so `uv run --project` fell back to
`<project>/.venv`. In the container, that path is the bind mount. A run
replaced the developer's macOS venv with a Linux one. The next `uv run` on
the host rebuilt the venv without explanation.

`tests/agent_bus/integration/harnesses.py::_server_env` passes
`AGENT_BUS_*` and `UV_*` variables by prefix, not the full environment. A
blanket merge would write API keys to disk and onto a command line.

## Mail delivery to `codex exec`

`codex exec` exposes `exec_command` and `write_stdin`, a long-lived shell
it can write to. Both tools are poll-shaped: codex receives output only
when it asks for it. Whether `codex exec` can park `exec_command` on a
read of `agent-bus watch`, as `omp` parks on `hub wait`, is untested.

## Interactive app-server wake

This poll-shaped behavior applies to headless `codex exec` only. The
interactive app-server path has a native wake. A `thread/queue/add` item
auto-wakes an idle thread. The wake arrives as a plain user turn. Codex
runs in these two distinct modes. The tests drive `codex exec`.

## `turn/steer` versus `thread/queue/add`

`turn/resume` followed by `turn/steer` can interject into a busy thread
directly. This was verified live on codex-cli 0.149.0. `turn/steer`
requires `expectedTurnId`. This id comes from `thread.turns[]`, from an
entry with `status: "inProgress"`. The wire protocol has no top-level
`activeTurnId` field to read it from.

`turn/steer` works only from the process already holding that turn.
Thread state is per-app-server and lives in memory. A freshly-spawned
app-server's `turn/start` or `turn/steer` either starts a competing turn,
or ends when that process closes. `send_to_codex` spawns and closes an
app-server for each call. This is the shape that breaks `turn/steer`.
`send_to_codex` uses `thread/queue/add` only (#292).
