# agent-bus for omp

An [omp](https://github.com/can1357/oh-my-pi) extension that makes agent-bus's
MCP push notifications do the mechanical work themselves, and keeps an omp
session off the bus entirely until you say otherwise.

## What it does

- **Reacts to `notifications/resources/updated` on `agentbus://inbox`
  directly** (omp's `mcp_notification` event), instead of leaving it to the
  model to notice the notification and spend a turn on `get_inbox` /
  `read_message` / `ack_message`. The extension fetches the message,
  injects it into the session as a steer, and acks it — zero tool calls,
  zero tokens, on the mechanical part.
- **Never registers on connect.** Connecting to agent-bus's MCP server used
  to create a roster entry and start a UDS listener the moment the
  connection happened, whether or not anyone asked for it. This extension
  pairs with `AGENT_BUS_NO_AUTO_REGISTER=1` (agent-bus side) so that never
  happens — an omp session with agent-bus configured is invisible on the
  bus until `/agent-bus-join` is run.
- **One-time join, per project.** `/agent-bus-join [name]` registers once
  and remembers the name in `.omp/agent-bus.json`. Every later
  `session_start` in that project re-registers silently — the same name,
  no user action — so joining is a one-time decision, not a per-session one.

## Install

Copy or symlink `agent-bus.ts` into an extension directory omp scans:

```sh
# every project on this machine
ln -s "$(pwd)/agent-bus.ts" ~/.omp/agent/extensions/agent-bus.ts

# or just this one
mkdir -p .omp/extensions
ln -s "$(pwd)/../../integrations/omp/agent-bus.ts" .omp/extensions/agent-bus.ts
```

Add agent-bus as an MCP server with the passive flag set, in whichever
`mcp.json` your extension install matches (user-level
`~/.omp/agent/mcp.json`, or project-level `.omp/config.yml` /
`.omp/settings.json` — see omp's own `extension-loading.md`):

```json
{
  "mcpServers": {
    "agent-bus": {
      "command": "agent-bus",
      "args": ["mcp"],
      "env": { "AGENT_BUS_NO_AUTO_REGISTER": "1" }
    }
  }
}
```

Without the `env` entry, agent-bus registers on connect as it always has —
the extension's inbox handling still works, but the passive-until-joined
behavior does not.

## Use

```
/agent-bus-join                 # registers as the project directory's name
/agent-bus-join overlap-bench   # registers under a chosen name
```

Nothing else to do. A message that arrives afterward shows up as a steer in
the current turn, already acked; the underlying CLI calls (`agent-bus
inbox`, `read`, `ack`, `register`) are logged wherever `agent-bus`'s own
structured logging already writes.

## Verified against

Type-checked against the real `@oh-my-pi/pi-coding-agent` source
(`ExtensionAPI`, `ExtensionContext`, `McpNotificationEvent`, `ExecResult`),
not just `docs/extensions.md`'s prose — `pi.exec`'s result field is `code`,
not `exitCode`; `notify`'s level is `"warning"`, not `"warn"`; the CLI's
`register` command requires `--kind` explicitly, unlike the MCP tool path
(which infers it from the `initialize` handshake). Not yet run against a
live omp session — the inbox-fetch/inject/ack loop and the `session_start`
re-registration are the two things worth watching on first real use.
