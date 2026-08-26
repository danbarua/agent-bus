# Integration / smoke tests

These spawn **real coding agents** and talk to a **live Claude Code session**.
They cost money and minutes, so they never run in a normal sweep.

**Run them in the container.** It needs nothing from you but API keys in
`.env`:

```sh
docker compose run --rm e2e
```

Installing five harnesses, logging codex in, granting grok folder trust — all
of it is in the image. Removing those steps is what the container was built
for, which is why there is no list of things to do first.

## Running in Docker

Developing agent-bus on the machine that *runs* agent-bus is self-interfering.
Tiers 3, 4 and 5 deliberately switch **off** the `AGENT_BUS_*_DIR` overrides,
because they have to discover a real Claude peer — so they cannot be isolated by
environment variable. Only by kernel.

```sh
export ANTHROPIC_API_KEY=... OPENAI_API_KEY=... XAI_API_KEY=...
docker compose run --rm e2e        # every test, logs kept in .e2e/
docker compose run --rm test       # unit suite only, no keys needed
docker compose run --rm shell      # poke around with all five agents on PATH
```

`AGENT_BUS_RUN_SPENDY_E2E_TESTS` is already set by the service; prepending it
to `docker compose run` sets it for the compose CLI on your machine, which is
not where it is read.

### Where the logs go

`--rm` throws the container away, so anything written inside it goes too. The
`e2e` service passes `--basetemp=/workspace/agent-bus/.e2e`, which puts every
test's `tmp_path` inside the bind mount instead:

```
.e2e/
  test_tier4_round_trip_peer_to_0/
    agent-bus.jsonl    # every verb call, driver and peer, in order
    peer/stdout.jsonl  # the Claude session's own stream
    evidence/          # marker files the shell wrote
```

**It does not accumulate**, and not because anything cleans up: pytest empties
an explicit `--basetemp` at the start of every run, so `.e2e/` always holds
exactly the last one. Gitignored.

The service runs at `AGENT_BUS_LOG_LEVEL=INFO`, since the default WARNING is
right for an agent in someone's terminal and useless for a run you are
reviewing. Override it from your shell like any other.

*(The `…current` symlinks pytest writes point at the container's path and will
not resolve on the host. The directories beside them are the real thing.)*

The bridge is not here. It has its own tests in `tests/agent_bridge/` and its
own stack in `docker-compose.cloud.yml`, which documents itself — work on
agent-bus should not wait on a cloud service, and the dependency runs the other
way.

Keys come from `.env` (or the shell, which wins). They are injected at run time
only — `.dockerignore` keeps `.env` out of the build context, because an API key
baked into an image layer survives in the history even if a later layer deletes
it.

**codex is the one harness an API key alone does not satisfy.** It defaults to
ChatGPT OAuth and returns `401 Missing bearer or basic authentication` with
`OPENAI_API_KEY` set and ignored; it wants an explicit
`codex login --with-api-key`, which writes `~/.codex/auth.json`. The container's
entrypoint does that at start-up, into its own disposable HOME. The other four
read their key straight from the environment — including omp, whose
`xai-oauth/grok-4.6` default looks like it needs a browser login and does not.

The container has its own `HOME`, `~/.agent-bus`, `/tmp/cc-socks` and PID
namespace, so nothing it does reaches the live bus. Worth checking once yourself:
run `agent-bus list` on the host before and after an `e2e` run — it does not
change.

**grok's trust step is already done in the image.** On the host, granting folder
trust is a manual ceremony this README refuses to automate. In the image it is a
Dockerfile layer, because the two are not the same act: the container is a
disposable sandbox you built by typing `docker build`, holding a checkout at a
path that exists nowhere else, and trusting it grants nothing on your machine.

**Do not bind-mount `/tmp/cc-socks` or `~/.claude/sessions` from the host.** Peers
are identified by pid; a host/container split puts the pid in
`sessions/<pid>.json`, the pid in the socket filename, and the pid `getpeereid()`
reports in three different namespaces.

### Pinning a harness version

Every agent is a build arg, so reproducing a suspected regression is one rebuild
rather than a bisect against whatever the installer serves today:

```sh
docker build --target agents --build-arg GROK_VERSION=1.0.4 -t agent-bus:agents .
```

Defaults match the maintainer's machine. Two install paths are not npm and are
worth knowing about: grok takes the version positionally
(`install.sh | bash -s 1.0.5`), and **omp is fetched as a prebuilt release
binary** rather than from npm — its npm bin is a `bun` script that loads a native
module `npm install -g` never fetches, so it lands on `PATH` and dies on first
run. The Dockerfile's build-time check runs every binary, not just locates it,
for exactly that reason.

## Running them outside a container

Supported, but nothing here needs it. You need the five harness binaries on
`PATH`, codex logged in, and the repo already trusted by grok -- which is the
whole of what the image does for you, and the reason it exists.

```sh
AGENT_BUS_RUN_SPENDY_E2E_TESTS=1 uv run python -m pytest tests/integration -q -s
```

Without that variable every test in here skips, and each tier skips
individually if its binary is missing.

## The tiers

| tier | needs | what it proves |
|---|---|---|
| 1 | nothing | the bus comes up in an empty directory |
| 2 | a harness binary | **each of the four harnesses joins the bus and gets a message through** |
| 3 | a harness + `claude` on `PATH` | a peer reaches Claude over UDS |
| 4 | a harness + `claude` on `PATH` | …and Claude's reply reaches the peer; and both views of the roster show those two and nobody else |

**Every tier runs unattended.** Tier 2 is the cheapest — it needs no Claude at
all — and is parametrised over every harness, so `-k omp`, `-k grok`,
`-k codex`, `-k pi` each run one.

Claude is not among them, and that is the point rather than an omission. Every
other harness has to *join*: it runs `register`, or starts our MCP server, or
is wired up by a hook. A Claude session is found by existing — it publishes a
session file for its own reasons and discovery reads it — so there is no
joining step to test. It appears in the tiers below only as the thing being
messaged.

Tier 4's second test **counts** rather than confirms. The rest assert a named
thing happened, which stays true with bystanders around; "these two and no
third" does not, and the container is what makes it true — its own PID
namespace, `HOME`, `~/.agent-bus` and `/tmp/cc-socks`. Run outside one it skips,
naming whoever it found, rather than failing on a laptop.

It asks the same question from both sides on purpose: `agent-bus list` is our
answer, Claude's `ListAgents` is the harness's own, read from the session file
we publish. Either can be right while the product is wrong, and a disagreement
means one of them is lying about who is on the team without a sender being able
to tell which.

**There is no tier 5.** The bridge's e2e test was one; see above.

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
- An **MCP-only peer of any kind** is registered as `pending-<pid>` before
  it claims a name, because the MCP child does not inherit the harness's
  session variables — grok's are hook-scoped. The `initialize` handshake
  settles it: to the harness's kind if the client identifies itself, otherwise
  to `other`, which is a settled answer and not a missing one.

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
