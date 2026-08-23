# Integration / smoke tests

These spawn **real coding agents** and talk to a **live Claude Code session**.
They cost money and minutes, so they never run in a normal sweep:

```sh
AGENT_BUS_INTEGRATION=1 uv run python -m pytest tests/integration -q -s
```

Without that variable every test in here skips.

## What a human has to do first

Most of this cannot be automated, because it is exactly the trust and auth
ceremony that protects you from a test doing it silently.

### Always

| | |
|---|---|
| `AGENT_BUS_INTEGRATION=1` | opt in; without it everything skips |
| the harness binary on `PATH` | each tier skips individually if its agent is missing |

### For the UDS tiers (peer → Claude, and the round trip)

```sh
AGENT_BUS_E2E_PEER="<name of a live Claude session>"
```

Get the name from `/list-agents` inside Claude Code — it is the line that says
"the name other sessions use to message it". **That session must stay open**;
the round-trip tier needs it to answer.

Nothing is installed on the Claude side and nothing is asserted about it. Its
harness delivers the peer's message and it replies with native `SendMessage`.
That absence of Claude-side code is the feature, so a test that needs Claude to
poll an inbox or look up a socket is testing the wrong thing.

### Per harness

**omp** — nothing. It reads a project-local `.mcp.json` that the test writes
into a tmpdir, so no global config is touched.

**codex** — nothing beyond being logged in.

**pi** — nothing. pi has no hooks *and* no MCP; it is a minimal agent whose tool
surface is the shell. It joins the bus because the prompt tells it to run
`agent-bus register`, which is the honest shape for a harness with no
integration points at all — and a useful test that the bus works for one.

**grok** — needs one manual step, once.

grok *discovers* a project-scoped MCP server in an untrusted folder but will not
**start** it, so a throwaway tmpdir is useless: it is untrusted by definition.
Verified — `grok inspect` in a tmpdir says `Project trusted: no` while still
listing `agent-bus (stdio) config`, and a headless run there reads the config
file and never calls the tool.

Trust lives in `~/.grok/trusted_folders.toml` and is granted interactively:

```sh
cd /path/to/agent-bus && grok      # answer the trust prompt, then quit
```

A test must not write that file for you — granting trust on your behalf is the
one thing the prompt exists to prevent.

Because the trusted folder has to be the repo, the grok tier writes
`<repo>/.grok/config.toml` at run time and removes it afterwards (`.grok/` is
gitignored). One consequence worth knowing: while it exists, *any* grok session
started in this repo will also launch the bus MCP server.

Verified working end to end on grok 1.0.5:

```
$ grok -p 'Call the agent-bus MCP tool `register` ...' --always-approve
REGISTERED=grok-probe
```

with the bus showing `name=grok-probe kind=grok`. Note what that proves beyond
grok itself: the MCP server registered *itself* as `other-<pid>` on startup —
`GROK_SESSION_ID` and friends are **hook-scoped and not set for MCP children**,
so an MCP-only peer cannot be detected — and the `register` tool then renamed
that same entry rather than creating a second one. An MCP-only peer has no name
until it asks for one.

## Why the tiers are shaped this way

Tier 1 is CLI only and needs no agent. Tiers 2 and 3 test **UDS**, because that
is the product: a peer that appears in Claude's native `ListAgents` and can be
messaged like any Claude session. They assert nothing about inbox files — to the
calling agent there is only the MCP facade, and to Claude there is only the
socket.

## If a tier fails

- **`SEND_EXIT` is non-zero** — the peer never published its own listener.
  Reaching a Claude peer needs one, because the outbound frame carries the
  sender's socket as the reply address. Check the MCP server actually started.
- **`REPLY=NONE`** — the Claude session did not answer within the wait. Check it
  is still open and not blocked on an approval prompt.
- **an MCP server fails to start with `ENOENT`** — a stale config pointing at
  something that no longer exists. `hooks/`, `scripts/agent-bus` and
  `plugin.json` were deleted; the MCP command is now `agent-bus mcp` (or
  `uv run --project <repo> agent-bus mcp`).
