# agent-bus for omp

An [omp](https://github.com/can1357/oh-my-pi) extension that makes agent-bus's
MCP push notifications do the mechanical work themselves.

## What it does

- **Reacts to `notifications/resources/updated` on `agentbus://inbox`
  directly** (omp's `mcp_notification` event), instead of leaving it to the
  model to notice the notification and spend a turn on `get_inbox` /
  `read_message` / `ack_message`. The extension fetches the message,
  injects it into the session as a steer, and acks it — zero tool calls,
  zero tokens, on the mechanical part.
- **Registers only when this project's own MCP config says to.** Whether
  this omp session is on the bus at all is decided entirely by
  `AGENT_BUS_NAME` in the `agent-bus` server's own `env` — set, this session
  registers under that exact name at startup; unset, nothing registers when
  omp connects, and the agent joins only if it calls the `register` tool.
  Nothing in this extension writes to the bus or decides a name; naming is
  the config file you wrote, not a runtime join step.

## Install

Copy or symlink `agent-bus.ts` into an extension directory omp scans:

```sh
# every project on this machine
ln -s "$(pwd)/agent-bus.ts" ~/.omp/agent/extensions/agent-bus.ts

# or just this one
mkdir -p .omp/extensions
ln -s "$(pwd)/../../integrations/omp/agent-bus.ts" .omp/extensions/agent-bus.ts
```

Add agent-bus as an MCP server with a name for this project, in whichever
`mcp.json` your extension install matches (user-level
`~/.omp/agent/mcp.json`, or project-level `.omp/config.yml` /
`.omp/settings.json` — see omp's own `extension-loading.md`):

```json
{
  "mcpServers": {
    "agent-bus": {
      "command": "agent-bus",
      "args": ["mcp"],
      "env": { "AGENT_BUS_NAME": "labkit-dev" }
    }
  }
}
```

Without the `env` entry, this session never registers — the extension's
inbox handling still works for anything registered by hand (an explicit
`register` tool call), but nothing joins the bus on its own.

## Use

Nothing to do beyond the config above. Every session start in this project
registers as `labkit-dev` (or whatever name you chose) automatically; a
message that arrives afterward shows up as a steer in the current turn,
already acked. The underlying CLI calls (`agent-bus inbox`, `read`, `ack`)
are logged wherever `agent-bus`'s own structured logging already writes.

**Checking that the extension itself loaded** needs its own step: it only
reacts to a push nobody controls the timing of, so a session that never
receives mail writes nothing and gives no other visible sign. It logs
`"agent-bus extension loaded"` through `pi.logger`, omp's own logger, into
`~/.omp/logs/omp.<date>.<pid>.log`:

```sh
grep '"agent-bus extension loaded"' ~/.omp/logs/omp.*.log
```

A missing line, once a session has actually started, means the extension did
not load — check the install path and the `mcp.json` above, not the bus.

## Verified against

Type-checked against the real `@oh-my-pi/pi-coding-agent` source
(`ExtensionAPI`, `McpNotificationEvent`, `ExecResult`), not just
`docs/extensions.md`'s prose — `pi.exec`'s result field is `code`, not
`exitCode`; `notify`'s level is `"warning"`, not `"warn"`.

Run live end to end against a real installed omp (18.2.1) and `agent-bus`
(0.7.0): registers under `AGENT_BUS_NAME`, appears in another Claude session's
`ListAgents`, receives a message sent with `agent-bus send`, the resource
watch notices and pushes `notifications/resources/updated`, the extension's
handler fetches the unread message, acks it (confirmed `"read": true` in the
real inbox file), and injects it as a steer, which the model saw and acted on
correctly. The pid resolution agrees between the MCP server's `AGENT_BUS_NAME`
registration and this extension's `pi.exec` calls, provided both resolve the
same installed `agent-bus` — see the note below.

**The MCP server command and `pi.exec` must resolve the same `agent-bus`.**
`pi.exec("agent-bus", ...)` resolves through `PATH`, independently of whatever
the `mcp.json` `command` names. Pointing only the MCP server at a different
build (a source checkout via `uv run --project`, for example) while `PATH`
still finds an older installed one produces two processes with incompatible
self-identity resolution: the extension's own `agent-bus inbox --unread`
call finds nothing, even though the server-side inbox genuinely holds an
unread message. Keep them in step — `uv tool update --reinstall
agent-bus-team` for the installed one, or point both at the same checkout.
