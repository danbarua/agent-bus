# codex

What to know when codex is the harness that is misbehaving. Its app-server API
is in `codex-messaging-reference.md`.

**An API key alone does not authenticate it.** It defaults to ChatGPT OAuth and
returns `401 Missing bearer or basic authentication` with `OPENAI_API_KEY` set
and ignored. It wants `codex login --with-api-key`, which writes
`~/.codex/auth.json`. The container's entrypoint does that at start-up into its
own disposable HOME.

**Its MCP server is a `-c` override, and the key must be a TOML *bare* key.**
`mcp_servers.agent-bus=…` works. Quoting it (`mcp_servers."agent-bus"`) parses
fine and then registers a server literally named `"agent-bus"`, quotes
included — its tools are unreachable, and the model improvises by shelling out
and reporting a success it did not have. Assert on the bus, never on stdout.

**It hands its MCP child a fixed environment.** Anything not named there does
not arrive. Probed: the child gets `HOME LANG LOGNAME PATH SHELL TERM TMPDIR
USER __CF_USER_TEXT_ENCODING` and nothing else — no thread id, no session id,
no socket path.

That has bitten twice, both times silently:

- `AGENT_BUS_LOG_*` missing, so its MCP calls were logged nowhere — in the runs
  whose purpose is observing them. It stayed hidden because codex reads nothing
  from the environment at call time, so a truncated one cost it nothing visible.
- `UV_PROJECT_ENVIRONMENT` missing, so `uv run --project` fell back to
  `<project>/.venv` — which in the container is the bind mount. A run replaced
  the developer's macOS venv with a Linux one, and the next `uv run` on the host
  rebuilt it without saying why.

`tests/agent_bus/integration/harnesses.py::_server_env` passes `AGENT_BUS_*`
and `UV_*` by prefix for that reason, and deliberately not the whole
environment: these configs are written to disk and onto a command line, so a
blanket merge would put API keys in both.
