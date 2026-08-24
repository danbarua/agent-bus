# Durable messaging… or not

> Replaces the working note committed at `e0010b1`, which is still in history if
> you want the original phrasing.

## What this is for

**Cross-platform group chat for a human and their AI agents.** Not a messaging
product, not persistent team messaging — other people are building that. This
exists because the best code reviews come from *outside* a coding harness, and
getting an opinion out of a desktop AI and back into a coding agent currently
costs the user two sore thumbs and a lot of copy-paste.

The pattern it automates, stated plainly:

1. Big context dump goes into a long-running desktop chat — usually "review this
   branch/PR on GitHub".
2. The desktop chat reviews, and has opinions.
3. Coding agents do a flurry of work.
4. Another context dump. Repeat, six-plus rounds, until the desktop AI has no
   further opinions that need actioning.
5. The coding team picks up the next large piece of work.

It is a clunky version of Claude Code's `/advisor`, and **the clunkiness is worth
automating** precisely because the value is in the outside opinion. The second
use case is design review, where the desktop chats drive strategy and the
long-context coding agents are the ones with the outside opinion.

What it is *not*: tagging `@claude` on a GitHub PR, or hand-writing a paragraph
of context to carry from one chat to another. Those exist and are not what the
user wants.

## Two classes of peer, and one hard boundary

The harness taxonomy lives in `harness-compatibility.md` and is not repeated
here. What matters for this design is a boundary that cuts across it:

| | reachable | wakes | agency |
|---|---|---|---|
| **coding peer** — Claude Code, Codex, Grok, omp, pi | on the machine, always | natively or via `watch` | runs a loop, will crack on |
| **desktop peer** — Claude Desktop, ChatGPT | only over public HTTPS | **never** — a human prods it | none between prods |

**Coding→coding is near-real-time. Coding→desktop is hard asynchronous.** That
asymmetry is the whole design constraint, and it is not a quality-of-service
gradient — it is a categorical difference in whether a reply can be waited for.

A desktop peer has no wake. There is no loop, no `watch`, no way to insert a
message into its context when it finishes a turn. The user types "you've got
mail". That is the mechanism, and it is not going to improve.

**The failure is one-sided.** Coding peers carry on regardless: they send, they
get on with work, everything is fine locally. It only breaks on the desktop
side, where messages pile up against a peer that is not looking.

## Persistence is an anti-feature

The obvious reading — "desktop peers can't be reached now, so we need durable
inboxes" — is half right and dangerous.

Persistence is the **transport mechanism**. It is not a feature, and it must not
become one. A six-hour-old message delivered because the bridge came back up is
worse than a message never delivered:

- the branch moved, the question was answered, the decision was taken
- the agent acts on a world that no longer exists
- and it arrives looking current

That is a **correctness** problem, not housekeeping. It is also remedial work
appearing hours later with no obvious cause.

So: messages expire, uniformly and briefly. Two consequences follow, and both
are wanted:

- no cleanup job for unread and dated mail
- cloud components stay pay-for-what-you-use — you are not billed to queue up
  chaos for later

### The TTL is uniform

Tempting to vary it: a desktop peer prodded once a day could use a longer
lifetime than a coding peer woken in seconds. Rejected, deliberately.

A per-class TTL optimises for the desktop↔desktop conversation — human plus
Claude Desktop plus ChatGPT, talking amongst themselves. That is *supported*,
but it is not the problem space. The problem space is **live group chat**: ideas
flowing, the user in the loop, the user specifically breaking flow to get a
second pair of eyes. In that mode a stale message is exactly the thing to avoid,
and the longer TTL would apply to the peer most likely to read late.

One brief uniform TTL. `agent-bus` is bringing the Claude Code experience
everywhere else, and Claude Code's messaging is "deliver now, or not now".

### Open question, worth reverse-engineering

Claude Code's UDS protocol has a busy path — "receiver is not available to read
this now". **Does the sender wait until the receiver finishes its turn, or is the
send simply refused?** We have only ever seen the unhappy path while e2e-testing
our own listener, never against a real Claude receiver in normal use. Whatever
Claude does here is the reference behaviour worth copying, because the whole
design is "be Claude Code's model, elsewhere". See `UDS-protocol.md`.

## Addressing is the up-front constraint

This is baked in from the start rather than retrofitted, because the address is
where the asynchrony has to live.

`address.py` already spells an address `<kind>:<space>:<value>`, and each space
owns its own liveness rule — that abstraction exists because `is_pid_alive` is
right for a Claude session and wrong for a Codex thread. Desktop peers extend it
by one step: **a space owns its delivery expectation too.**

| space | liveness | mailbox | delivery |
|---|---|---|---|
| `bus` | the registering process | yes | now |
| `session` | the harness's process | yes, except `claude` | now |
| `pid` | that process | yes | now |
| `thread` | existence only | no | queued, wakes natively |
| **`desktop`** | **existence only — never a process** | **yes, cloud, TTL'd** | **whenever a human prods it** |

A `desktop` address is *known-asynchronous by its space*. A sender can tell from
the address alone that it must not block, and the UX can say so honestly:
"queued to Claude Desktop; you will be notified when they reply" — which
requires the user to go and ask.

### The desktop space has no conversation dimension

`desktop:claude` and `desktop:chatgpt`. That is the whole address. There is
**no** `desktop:claude:<conversation-id>`, and there will not be.

**One long-running chat per provider talks to the coding team.** Anything else is
guaranteed to get messy: several chats holding partial views of the same review,
replies arriving from whichever one the user last had open, and no way for a
sender to know which it reached.

It is also not addressable from outside even if it were wanted — nothing in
Claude Desktop or ChatGPT lets an external process enumerate conversations or
target one. But the constraint stands on its own merits: the value of a desktop
peer is that it is a *long-context* advisor, and that value comes precisely from
one conversation accumulating the whole thread of a review. Splitting it across
addressable conversations would destroy the thing being addressed.

Worth contrasting with `thread`, which looks similar and is not:

| | cardinality | why |
|---|---|---|
| `thread` — `codex:thread:<uuid>` | **many** — every Codex thread is addressable | Codex exposes thread ids, and a thread is the unit of work |
| `desktop` — `desktop:claude` | **one per provider** | the conversation is not addressable, and should not be |

Both have existence-only liveness. They differ in whether the space has an
interior, and `desktop` deliberately does not.

`desktop:claude` and `desktop:chatgpt` are **not IPC peers** even when their apps
are running on the same machine as the bus. Traffic goes out to the public
internet and back for IPC between two processes on one laptop. That is absurd
and unavoidable: it is the only route those apps expose.

**`desktop` becomes a real kind.** `KNOWN_KINDS` in `protocol.py` is deliberately
closed and adding to it is a product decision rather than a defect repair — this
is that decision, made explicitly.

## Shape of the implementation

### The seam already exists

Two classes of inbox, file-based and cloud-based, is already the shape of
`adapters/transport/`: `claude.py`, `codex.py`, `filebus.py`. A cloud inbox is
one more adapter, routed by kind in `commands/messages.send` like every other.
Nothing about the bus needs rearranging — if the design is right, this is a
drop-in on machinery that is already there.

### Store: Firestore

Chosen over Pub/Sub, GCS and Cloud SQL:

- **native TTL policies** — an `expireAt` field, and the document deletes itself.
  The expiry requirement above is a property of the store, not a cron job.
- **scale to zero** — effectively free at this volume. Cloud SQL bills
  continuously and was ruled out on that alone.
- **a mailbox, not a pipe** — `inbox --unread` already reads without acking.
  Pub/Sub redelivers on ack-deadline expiry, which is different behaviour, and
  its retention is a property of the subscription rather than the message.
- **an emulator** — the cloud transport is unit-testable with no GCP at all,
  which is what makes the whole thing disposable and repeatable.
- room to build user-facing features on later, which a queue does not give.

### The public surface is frozen

ChatGPT and Claude Desktop reach a **stable, narrow** MCP server over public
HTTPS: read, ack, write. Nothing else, and it does not change.

This is what removes the OpenAI problem rather than mitigating it. Their
verification and WAF-like filtering is opaque and undebuggable, and it is
provoked by shipping changes. Freeze the contract and there is nothing to
provoke: the bus iterates daily behind it, the public surface does not move.

Worth being precise about why this works — **the decoupling comes from the
frozen contract, not from the storage technology.** A queue behind the API would
also decouple, but so does anything, once the API stops changing.

It also retires the localhost machinery: no SSH tunnel, no reverse proxy, no
running the thing you are simultaneously working on. Accept the cloud
constraint and build it all in-cloud. The previous incarnation — localhost MCP
server, private VM, SSH tunnel, HTTPS reverse proxy — worked; it is being
replaced because it is easier without the tunnel, not because it failed.

## Message size: adopt the narrowest constraint

Rule: **agent-bus adopts the narrowest constraint of any supported harness that
already implements cross-session messaging.** A message the bus accepts must be
one every peer can receive, or the bus is lying to the sender.

What is actually measured today:

| | limit | source |
|---|---|---|
| agent-bus | 1,000,000 chars; 50 unread per inbox | `store.py:38-39` |
| Codex | **none found** on `thread/queue/add` | source read; the 2MB cap at `rmcp-client/src/event_notification_transport.rs:23` is queued MCP event notifications, a different path |
| Claude Code | **not established** | binary strings show only libc errno tables; it is compiled, so absence of a string is not absence of a limit |
| Grok | generous, per prior testing | not pinned to a number |

So the rule currently has one number in it — ours — and 1MB is probably already
the narrowest. That is a thin evidence base for a rule stated this confidently.
Claude's limit is the one worth establishing, and the honest way is empirical:
send increasing sizes to a live peer and find where it refuses. The integration
tiers are the place for that, and it is not urgent.

Two related facts, both Claude-shaped and both worth copying:

- **Claude Code's message and its notification are the same thing.** There is no
  separate "you have mail" event.
- `agent-bus listen` is the same idea: "you have a message, call the tool to read
  it" — delivering that notification *is* delivering the message.

## Non-goals

- **Persistent team messaging.** Other people are building it. Messages here
  expire on purpose.
- **`oh-my-opencode`.** Installed on the maintainer's machine, not on the list
  until someone asks. If omo suits you, omo is the only harness you need;
  agent-bus is for a chimera of agents across several.
- **Writing into another product's private store** to fake a peer — the Codex
  `state_5.sqlite` argument in `harness-compatibility.md` applies to anything
  schema-versioned by someone else.
- **Making desktop peers wake.** They cannot. The human is the wake mechanism
  and the design assumes it rather than working around it.
