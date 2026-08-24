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

## The tiers

| tier | needs | what it proves |
|---|---|---|
| 1 | nothing | the bus comes up in an empty directory |
| 2 | a harness binary | **each of the four harnesses joins the bus and gets a message through** |
| 3 | a harness + a live Claude session | a peer reaches Claude over UDS |
| 4 | a harness + a live Claude session | …and Claude's reply reaches the peer |

**Tier 2 is the one you can run on demand** — no Claude session, no
`AGENT_BUS_E2E_PEER`. It is parametrised over every harness, so
`-k omp`, `-k grok`, `-k codex`, `-k pi` each run one.

Tiers 3 and 4 test **UDS**, because that is the product: a peer that appears in
Claude's native `ListAgents` and can be messaged like any Claude session. They
assert nothing about the bus's file layout — the reply is read back through the
driver's own `inbox --json`, which is the public surface. To the calling agent
there is only that CLI, and to Claude there is only the socket.

### Why tier 2 asserts on a delivered message, not on `list`

A headless agent is a one-shot: it registers, it exits, and its roster entry is
pruned as dead. That is correct — presence *is* liveness — so asserting that it
appears in `list` would be asserting it is still running, which it deliberately
is not. Mail is the thing that outlives its sender, so the assertion is the
delivered message, and the **sender recorded on it** proves the agent claimed
its name and kind. One assertion, both halves.

### What each harness taught us

- **pi** must register with `--pid $PPID`. Inside its own shell tool that is
  *pi's* pid; without it the entry belongs to the CLI process, which exits
  immediately and is pruned before anything can address it.
- **codex** takes its server as a `-c` override, and the key must be a TOML
  **bare** key: `mcp_servers.agent-bus=…`. Quoting it
  (`mcp_servers."agent-bus"`) parses fine and then registers a server literally
  named `"agent-bus"`, quotes included — its tools are unreachable, and the
  model improvises by shelling out and reporting a success it did not have.
  That is exactly why the assertion is on the bus and not on stdout.
- **grok** needs the trusted folder above. Discovery is not start: an untrusted
  directory still *lists* the server.
- An **MCP-only peer of any kind** is registered as `other-<pid>` before it
  claims a name, because the MCP child does not inherit the harness's session
  variables — grok's are hook-scoped. It has no identity until it calls
  `register`.

### Isolation

`_bus_env(..., isolate_native=True)` points every harness registry
(`AGENT_BUS_GROK_DIR`, `_OMP_DIR`, `_CODEX_DIR`, plus sessions and sockets) at
empty directories. Without that, `list` unions the roster with whatever
discovery finds, so an assertion sees your own live sessions — and a test that
sends to a name could reach a real agent. Tiers 3 and 4 deliberately turn it
off, because they must find a live Claude peer.

## Why pi drives the UDS tiers

Tiers 3 and 4 are driven by pi. It is worth saying why, because it is not
obvious: pi is the *least* capable harness here -- no MCP, no hooks, only a
shell -- and that is exactly the point.

Measured: a pi-driven tier 3 completes in **15s** against omp's minutes, and
three of four omp round-trip runs failed on omp's own side (MCP tools missing
from its list, the send step silently skipped). Every one of those failure
modes is MCP-shaped. pi has no MCP to fail.

It costs one extra step. A peer reaching a Claude session needs its **own**
listener, because the outbound frame carries that socket as the reply address.
omp gets one free from `session_start()` when its MCP server starts; pi has to
ask:

```sh
agent-bus listen --name pi-peer --pid $PPID &   # $PPID inside pi's shell is pi
sleep 5
agent-bus send <claude-peer> -m "..."
```

`--pid $PPID` matters twice over -- it is also what makes `register` outlive the
CLI process, per the tier-2 note above.

This is how the listener bug was found: `run_listen` published a working socket
but never registered under its host pid, so `send` could not locate it. omp
never noticed because its listener came from the other code path. **The harness
with the least machinery finds the gaps, because nothing else is papering over
them.**

## How tiers 3 and 4 record what happened

Every shell step writes a marker into an `evidence/` directory beside the bus
home, and the assertions read those files. The driver's stdout is diagnostic
only.

This is not fussiness. A run that completed the entire round trip once failed
anyway, because pi wrote "The inbox contains a message." where the test grepped
its stdout for `SEND_EXIT=0`. Asking a language model to relay shell output
verbatim is asking it to do the one thing it will not do reliably, and a test
built that way grades the driver's prose rather than the product. The shell
records the fact; the model only has to run the command.

Each marker is joined by `;` to the command it describes, so both land in one
shell invocation -- split across two tool calls, `$?` is somebody else's exit
status.

## If a tier fails

- **`send.txt was not written`** — the driver never ran the send step at all.
  This is the one genuinely model-dependent failure left; the message names the
  step, and pi's stdout tail is included.
- **`send.txt` holds a non-zero `SEND_EXIT`** — the peer never published its own
  listener. Reaching a Claude peer needs one, because the outbound frame carries
  the sender's socket as the reply address.
- **`inbox.json` is empty** — the Claude session did not answer within the wait.
  Check it is still open and not blocked on an approval prompt.
- **a message arrived but not the briefed reply** — something answered that was
  not the headless peer. The expected wording is `ACK_TEXT` in `claude_peer.py`,
  which is also what briefs the peer, so the two cannot drift.
- **an MCP server fails to start with `ENOENT`** — a stale config pointing at
  something that no longer exists. `hooks/`, `scripts/agent-bus` and
  `plugin.json` were deleted; the MCP command is now `agent-bus mcp` (or
  `uv run --project <repo> agent-bus mcp`).
