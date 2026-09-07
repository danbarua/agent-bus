# Running a Bridge as a Service

A bridge stands in for one remote peer, for example `desktop:claude`,
`webhook:github`, or `remote:labkit-omp-claude`. The peer is reachable only
while its bridge runs.

A bridge started by hand can stop without any error message. It simply
stops appearing on the roster.

macOS is the only platform covered here. `systemd --user` follows the same
shape on Linux. No Linux implementation exists yet.

## Purpose of a bridge

A bridge gives a human and their AI agents a shared, cross-platform group
chat. The best code reviews come from outside a coding harness.

Without a bridge, getting an opinion from a desktop AI into a coding agent
takes manual copy-paste, in a repeating cycle:

1. A large context dump goes into a long-running desktop chat, typically
   asking it to review a branch on GitHub.
2. The desktop chat gives its opinions.
3. The coding agents act on the opinions.
4. Another context dump follows. This cycle repeats six or more times,
   until every point raised is acted on.

A bridge automates only the carrying of messages between peers. It does not
read, summarize, filter, or reorder anything it moves. Persistent team
messaging and tagging `@claude` on a pull request are separate, existing
mechanisms.

## Desktop peers

Claude Desktop and ChatGPT have no loop. No automatic process inserts a
message into their context when a turn ends. The user typing "you've got
mail" is the only mechanism that does.

This produces three effects.

- **The failure is one-sided.** Coding peers send messages and continue
  their own work. Every machine-local view looks fine while mail piles up
  against an unchecked desktop peer. This asymmetry is why a bridge runs as
  a background service, not in a terminal someone has to keep watching.
- **One conversation per provider.** `desktop:claude` and `desktop:chatgpt`
  are the whole address. No app lets an outside process enumerate or target
  a single conversation. No `desktop:claude:<conversation>` address exists.
  A second chat window is unreachable through the bus. A desktop peer is
  valuable precisely because its one conversation accumulates the whole
  review.
- **Mail expires after one hour.** This rule applies the same way in the
  cloud and locally. A bridge down for an afternoon does not deliver the
  morning's mail when it restarts. By restart time, the branch has moved
  and the question is already answered. A message delivered late can look
  current when it is not.

Because of this, traffic between two apps on one laptop goes out to the
public internet and back. This is the only route those two apps expose.

## Remote peers

The `remote` kind connects two machines under one account, for example a
coding agent on a MacBook reaching one on a Mac Studio. Both ends are
automated peers.

Unlike a desktop peer, a `remote` peer wakes normally. The far end is
another live `agent-bridge` process. It stands in for an ordinary bus peer,
waking the same way any other peer does.

Each direction uses its own address, named after the peer on the far
machine that will receive the message:

```sh
# on the Mac Studio, reaching the MacBook's labkit-omp-claude:
agent-bridge start --kind remote --name labkit-omp-claude --peer studio-claude

# on the MacBook, reaching the Studio's claude-mac-studio (say):
agent-bridge start --kind remote --name studio-claude --peer labkit-omp-claude
```

`--peer` declares a relay partner. The declaration stays inert until the
far side declares it back. A `desktop` address has a live occupant on its
connector. A `remote` address has no occupant on its connector.

The cloud relays a push into the peer's outbox only once both sides have
named each other. A one-sided declaration accumulates unread mail. That
mail still expires after the normal one-hour TTL.

This mutual-declaration rule keeps one bridge from writing into another
peer's inbox without consent. Every bridge in one environment shares one
credential with full trust. This pairing rule is what provides the safety.

`--peer` is a startup-time declaration. The cloud remembers it for as long
as the pairing stands. A restarted bridge does not need to redeclare it.

Running a `remote` peer as a service takes the same `--peer` flag, always
last:

```sh
packaging/launchd/bridge-service.sh install remote:labkit-omp-claude --peer studio-claude
```

## Installing the binary

```sh
uv tool install agent-bus-team
```

Do not run the bridge with `uv run` from a checkout. That ties the service
to one directory on one machine. Its behavior would change on every
`git checkout`. It would stop existing while the tree is mid-refactor.

## Storing the credential in the Keychain

One credential serves one environment, for its whole life. `desktop:claude`,
`webhook:github`, and any address added later all use the same Keychain
item unchanged. No new address needs a new credential minted for it. See
"Make a bridge credential" in `infra/cloud/README.md` for how to build one
from the deployed signing key.

```sh
security add-generic-password -U \
    -a "$USER" -s agent-bus-cloud-token -w '<the credential>'
```

Put the credential value directly on the command line with `-w`. `-w` with
no value prompts for input instead. That prompt reads through a 128-byte
buffer. A credential longer than 128 bytes gets silently truncated. The
command then exits 0 and reports no error. A credential that looks stored
but is not is worse than a `ps`-visible value that lasts a few
milliseconds.

Check the stored credential's length after adding it:

```sh
security find-generic-password -s agent-bus-cloud-token -w | tr -d '\n' | wc -c
```

`bridge-service.sh install` refuses to start a service whose credential is
too short to be real. This check uses the same length test.

The Keychain item takes priority over the file at `~/.agent-bus/cloud-token`.
This stops a leftover file from being used silently after the credential
moves. Without this rule, a leftover file could stay valid and in use
indefinitely. Delete the file once the Keychain item is added.

The file remains the fallback for machines that are not Macs. It is also
the fallback for a service that starts before the Keychain unlocks.
`agent-bridge` logs which credential source it used at startup. Check that
log first when a request returns 401.

### Pointing a bridge at a different deployment

`AGENT_BUS_CLOUD_TOKEN` wins over both the Keychain and the file. The
Keychain holds a single item under `agent-bus-cloud-token`. That item holds
only one environment's credential. Without `AGENT_BUS_CLOUD_TOKEN`, every
bridge on a machine resolves the same deployment. Set
`AGENT_BUS_CLOUD_TOKEN` to point one bridge at a different deployment, for
example staging.

```sh
AGENT_BUS_CLOUD_TOKEN='<the credential built for the other deployment>' \
  agent-bridge start --kind desktop --name claude-staging
```

Use a distinct `--name` for the second bridge. Each address has one
bridge. Two bridges claiming `desktop:claude` would compete for one inbox.
Both bridges write to the same `agent-bridge.jsonl`. The `address` field
tells their records apart. The `cloud endpoint` record at startup names
which deployment each bridge came up against.

Every child process the bridge starts inherits an environment variable.
Use the Keychain for the credential used every day. Use
`AGENT_BUS_CLOUD_TOKEN` only to point a bridge at a different environment.
One credential covers every address in the same environment.

### Credential lifetime

The credential is a static shared secret. It carries no `exp` field and
needs no rotation schedule. It changes only when the signing key itself is
rotated, following the "rotate the signing key" recipe in
`infra/cloud/README.md`. After a rotation, every bridge's Keychain item
needs the new value.

## Installing the service

```sh
packaging/launchd/bridge-service.sh install desktop:claude
```

This command renders the template, checks that the plist parses, and
bootstraps the LaunchAgent. It refuses to start if the stored credential is
too short to be one. Running it again reinstalls the service.

Each service serves one address. Install a second connector with
`install webhook:github`. Do not add a flag to the first service for it.
One address belongs to a single service.

The address argument is always explicit. There is no default address. A
restart therefore never targets the wrong service. No second credential
needs provisioning. The same Keychain item this machine already holds
works for the new service. One credential covers every address on the
machine.

To see what the install command would write before it writes it:

```sh
packaging/launchd/bridge-service.sh render desktop:claude /tmp/out.plist
```

## Operating the service

```sh
packaging/launchd/bridge-service.sh status    desktop:claude   # loaded? running? last exit?
packaging/launchd/bridge-service.sh logs      desktop:claude   # follow it
packaging/launchd/bridge-service.sh restart   desktop:claude
packaging/launchd/bridge-service.sh uninstall desktop:claude
```

`status` reports two separate things: the launchd state, and whether the
address is on the roster. These can fail independently: a service that is
not running, or a service that is running but not registered.

Stopping the service takes the address off the roster. Senders then see
the peer as unreachable. A stopped bridge does not collect mail into an
undrained inbox.

The bridge stops on SIGTERM. It stops its listener and leaves the roster. A
restart takes about a second.

`KeepAlive` restarts the service automatically after a crash.
`ThrottleInterval` is 60 seconds, because the bridge calls a billed
endpoint. A crash loop against a billed endpoint is a different failure
than one crash.

The plain `launchctl` forms, if you want them:

```sh
launchctl print     "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
launchctl kickstart -k "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
launchctl bootout   "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
```

### Two logs, two audiences

`bridge-service.sh logs` tails `~/Library/Logs/agent-bus/<label>.log`. This
file holds launchd's own capture of stdout and stderr, as untimestamped
`[bridge] ...` lines, one file per address. Open this file to check a
problem happening right now.

Every bridge process also writes structured JSONL to
`$XDG_STATE_HOME/agent-bus/agent-bridge.jsonl` (`~/.local/state` when
`$XDG_STATE_HOME` is unset). `agent-bus` uses the same logging mechanism,
in its own separate file beside `agent-bus.jsonl`. Every bridge process on
the machine shares `agent-bridge.jsonl`. The `address` field in each record
identifies which bridge wrote it.

```sh
jq 'select(.address=="desktop:claude")' ~/.local/state/agent-bus/agent-bridge.jsonl
```

Open `agent-bridge.jsonl` for a timestamped record with the actual
exception attached. Use it to correlate a bridge's traffic with a `send` or
`inbox` call that `agent-bus` logged in `agent-bus.jsonl`.

Both logs stay silent by default, showing only failures, the same as
`agent-bus`. Set `AGENT_BUS_LOG_LEVEL=info` to also see routine lines such
as `standing in`, `left the bus`, and a drained backlog. For a running
service, set this in the plist's `EnvironmentVariables`.

Two environment variables redirect these logs. They answer different
questions. `AGENT_BRIDGE_LOG_FILE` redirects only the bridge's structured
log. `agent-bus.jsonl` stays where it is. `AGENT_BUS_LOG_FILE` redirects
both logs into whichever single file it names.

Set `AGENT_BRIDGE_LOG_FILE` to redirect the bridge log alone. Set
`AGENT_BUS_LOG_FILE` to combine both logs into one file. The bridge checks
`AGENT_BRIDGE_LOG_FILE` first and falls back to `AGENT_BUS_LOG_FILE`.

## Polling interval

The bridge polls adaptively.

| | |
|---|---|
| within a minute of any traffic | every 5s |
| otherwise | `--inbound-poll`, default 120s |

Faster polling during a busy window does not improve first-message
latency. The bridge does not know a message exists until it polls for one.
The first message after a quiet spell still waits up to the full idle
interval. The fast 5-second interval speeds up the reply loop that follows
that first message. That is where faster polling actually helps.

A fixed 30-second interval sent about 5,600 requests a day to deliver a
handful of messages. Set `--inbound-poll 30` in the plist to trade that
request volume for a faster first hop.
