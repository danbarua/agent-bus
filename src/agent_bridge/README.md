# agent_bridge

Stands in on the bus for a peer that is only reachable remotely.

Claude Desktop and ChatGPT talk to a server over public HTTPS. They cannot see
a unix socket on a laptop, and nothing can push to them — a connector polls, or
a person opens the app. The bus is machine-local by design. This package is the
piece that makes a peer on one side addressable from the other.

**Operating it is `docs/running-the-bridge.md`** — installing the service, the
token, expiry, pointing one at a second deployment. This file is the map of the
code, so the two do not become two copies of the same thing.

## Two modules

| | |
|---|---|
| `bridge.py` | the loop, the cloud clients, the token. Everything with behaviour |
| `cli.py` | argument parsing, SIGTERM, and the startup lines a person reads |

`agent_bridge` imports `agent_bus`; `agent_bus` never imports `agent_bridge`.
The bus does not know the cloud exists.

## Verbs

```sh
agent-bridge start --kind desktop --name claude
agent-bridge read <id> --kind desktop --name claude
```

`read` answers where a message got to, inside its lifetime. It searches **both**
of the address's cloud queues and says which one holds it, because that is the
diagnostic:

| where | what it means |
|---|---|
| `inbox`, unread | the bridge pushed it; the peer has not looked |
| `inbox`, read | the peer consumed it |
| `outbox`, unread | the peer wrote it; this bridge has not pulled yet |
| not found (exit 1) | delivered and expired, or it never arrived |

No special case for a send-only peer — a webhook's inbox is simply empty. It
does not ack: an operator asking where a message went must not be the reason it
stops being redelivered.

The id is the one `agent-bus inbox` printed. It needs no translation: a local
message travels as the cloud's document id, so the same string addresses it on
both sides.

`start` runs the daemon; other verbs are ordinary commands. `agent-bus mcp` is
the shape this follows. Before it, `agent-bridge` was flags only, so there was
nowhere to put a query — which is why the verb exists at all.

The bare-flag form was dropped rather than shimmed. Nothing outside this
machine runs it, and a shim outlives the thing it shims.

## One bridge, one address

`--kind desktop --name claude` is the address `desktop:claude`, and it is the
*whole* address of the peer being stood in for. There is no conversation
dimension and there will not be one, so two holders is not an ambiguity to
resolve at delivery — it is a thing that must not exist.

`_join` refuses to start when something already holds the address. The bus will
not stop it: `register()` de-collides names and not aliases, so a second bridge
would register cleanly as `desktop-claude-2`, appear in `list`, and compete for
the same queue. Keeping the refusal here leaves the bus dumb.

Locally the bridge registers as `desktop-claude` — a mechanical transform, so a
kind nobody anticipated gets a sensible name without being added anywhere.

## The two directions are not symmetric

**Outbound.** A local agent sends to `desktop-claude`; that lands in the
bridge's own file-bus inbox. The loop forwards it and acks locally *only after*
the forward really happened — a client that reported success on a refusal would
lose mail while looking like it worked.

**Inbound.** The connector writes; the bridge polls, delivers onto the local
bus, and acks the cloud *only after* delivery. Both acks gate on the hop
actually completing, in both directions, for the same reason.

Polling is adaptive: fast inside a busy window, idle otherwise. A conversation
is bursty and the reply is the leg someone is waiting for; the idle rate is
what stops that costing ~5,600 requests a day for a handful of messages.

## Two clients behind one protocol

`CloudClient` is a Protocol with four operations — `push`, `pull`, `ack`,
`publish_roster`.

- **`HttpCloudClient`** talks to `/bridge` on the cloud server. Deliberately
  *not* the connector's MCP tool names: these are transport ops between two
  pieces of our own code, and the connector surface answers to the bus's
  vocabulary. One set moving must not drag the other.
- **`SpoolClient`** writes to a directory instead. `--spool-dir` is how you work
  offline on a machine that has a token, and it is what a bridge with no token
  falls back to — mail visible on disk rather than silently dropped.

Stdlib only, no dependencies: `agent-bus-team` declares `dependencies = []` and
means it.

## Where the server comes from

Not a flag, and not an environment variable naming a URL. **The token carries
its own issuer** in its `iss` claim, so installing a token is the whole of
"connect this bridge to the cloud" and the URL cannot drift from a value
configured beside it.

Resolution order is `AGENT_BUS_CLOUD_TOKEN`, then the Keychain, then
`<home>/cloud-token`. Explicitly set for this process beats machine-wide setup —
and it is the only one of the three that can differ between two bridges on one
machine, which is what makes pointing one at a second deployment possible at
all.

The claim is read without verifying the signature, deliberately: this is the
user's own 0600 config file, not network input, and anyone who can rewrite it
has already won. The server still verifies, so a token naming the wrong issuer
fails at connect, loudly.

## Reading what it did

`agent-bridge.jsonl`, beside `agent-bus.jsonl` — one file per binary, because a
launchd service restarts on its own schedule and someone tailing it wants only
its own traffic.

The startup lines go to *both* stderr and that file. A launchd service's stderr
is not where anyone looks, and a person watching a terminal does not tail a
jsonl, so neither substitutes for the other.
