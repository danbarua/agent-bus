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
right for a Claude session and wrong for a Codex thread.

| space | liveness | mailbox | delivery |
|---|---|---|---|
| `bus` | the registering process | yes | now |
| `session` | the harness's process | yes | now |
| `pid` | that process | yes | now |
| `thread` | existence only | no | queued, wakes natively |

> **Superseded, and worth reading as a correction.** This section originally
> added a fifth row — a `desktop` space with existence-only liveness and a
> cloud mailbox — on the reasoning that "a space owns its delivery expectation
> too". That was right about *where the asynchrony lives* and wrong about the
> mechanism.
>
> A desktop peer is reached by **a bridge process, one per provider**, which
> registers on the bus as an ordinary peer. So it lands in the `bus` space with
> a real pid, and needs no new space, no new addressing adapter and no change to
> `address.py`. `desktop:claude` survives as an **alias** on that entry, which
> `find_entry` already matches.
>
> Two things improve rather than merely simplify. Liveness becomes *true*
> instead of assumed: a desktop peer is reachable exactly when its bridge is
> running, which is the fact a sender actually needs, where "existence only"
> would have claimed a dead bridge was fine. And **delivery expectation keys on
> `kind`, not space** (`protocol.delivery_expectation`) — a space-keyed rule
> would answer "now" for a bridge sitting in the `bus` space, which is the one
> peer class where that is exactly backwards.

A `desktop` peer is *known-asynchronous by its kind*. A sender can tell without
probing that it must not block, and the UX can say so honestly: "queued to
Claude Desktop; you will be notified when they reply" — which requires the user
to go and ask.

### There is no conversation dimension

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

They differ in whether the address has an interior, and `desktop` deliberately
does not — enforced by there being exactly one bridge process per provider,
rather than by the parser.

`desktop:claude` and `desktop:chatgpt` are **not IPC peers** even when their apps
are running on the same machine as the bus. Traffic goes out to the public
internet and back for IPC between two processes on one laptop. That is absurd
and unavoidable: it is the only route those apps expose.

**`desktop` becomes a real kind.** `KNOWN_KINDS` in `protocol.py` is deliberately
closed and adding to it is a product decision rather than a defect repair — this
is that decision, made explicitly.

## Closing the UDS / file-inbox dichotomy

The change that makes everything above cheap. Today "Claude is different" leaks
into addressing, mailbox policy, hook safety and what we tell users to install.
After this it is different in exactly one place — the UDS transport — which is
the layer nobody else sees.

Two symmetric moves, both a couple of lines in the right place.

### Inbound: auto-reply with the real delivery semantics

Claude sends us a message because we smell like a Claude peer. We dial back a
protocol ACK because that is what the wire needs (`UDS-protocol.md` §5).

But Claude's **semantic** ACK — the thing its user and its model actually read —
is a peer replying "got your message" through native tooling. When the recipient
is a bus peer, that may be seconds away or may be tomorrow, and Claude's mental
model is Claude↔Claude: send, and the peer sees it now.

So on inbound, auto-reply through the same native transport:

> *Auto-Reply: your message has been delivered to a short-lived durable inbox.
> The receiving agent MAY respond within {TTL}, but this is not guaranteed.*

In most cases the receiving agent will answer immediately afterwards anyway —
courteous acknowledgement before proceeding is what "you are a helpful AI agent"
training produces, and that is the real semantic ACK. The auto-reply manages
expectations regardless of who is receiving and how.

**Refinement worth taking:** the addressing table above already records delivery
expectation per space, so the auto-reply can state the *actual* expectation
rather than a uniform hedge — "delivered to a coding peer, typically replies in
seconds" versus "queued to a desktop peer; needs the user to prod it, and may
not be read within {TTL}". A uniform "MAY respond, not guaranteed" is wrong for
a peer that answers in three seconds, and being wrong in the reassuring
direction is how a notice gets trained out of being read.

Two constraints on it: keep it terse, because it doubles inbound traffic in
Claude's context, and mark it unmistakably automated so Claude does not converse
with it.

### Outbound: inbox-ACK on transport-ACK

When agent-bus sends to Claude over UDS and the transport ACK comes back, also
write the message into Claude's file inbox **and immediately ack it**.

This is what puts Claude on the same footing as every other harness:

- no reaping of unread mail Claude was never going to read — it is acked at
  write time
- Claude *can* still read it, in the extreme-timing case where the UDS delivery
  landed but was missed
- **the MCP server becomes safe to install into Claude Code.** Today we have to
  caution against it. After this, users can install it there and let every other
  harness copy the MCP config from Claude rather than treating Claude as the
  odd one out
- Claude is made aware of non-`SendMessage` delivery semantics even while using
  its native tool
- "you've got mail" hooks become safe to write, because the message only stays
  unread when UDS delivery **failed** — so for Claude, "you've got mail" is
  unusual, meaningful and correct rather than a bug
- it removes "if claude do this else do that" from everywhere except the UDS
  layer, which is the only-if-Claude layer by definition

### This dissolves NO_MAILBOX_KINDS rather than overruling it

`adapters/addressing/session.py` currently declares Claude sessions
mailbox-less, and the reason is empirical, not aesthetic: *"a file inbox for one
is write-only, and writing to it leaves an unread nobody can ever clear. That is
how four inboxes on this machine were orphaned."*

The objection is **unclearable unreads**. Acking at write time means the unread
never exists, so the failure mode that justified the exclusion cannot occur.
Recorded here so nobody re-adds the exclusion later citing the orphans — the
orphans were real, and this is what fixed them.

Consequences to keep in mind: `MAX_UNREAD` (50) can never trip for Claude, since
its inbox is pre-acked; and `inbox --unread` stays empty for a Claude peer in
the normal case, which is the correct signal.

### The residual gap, named

If UDS delivery **fails**, we do not transport-ACK, so we do not inbox-ACK, and
the message correctly stays unread. Good — that is the design working.

But how does Claude find out? It has no hooks installed (they were deleted
deliberately) and nothing polls on its behalf. A failed delivery leaves mail
that Claude sees only if it happens to call `get_inbox`.

Not a blocker, and not solved here. The candidates are surfacing pending unread
on the next successful UDS message, or at `session_start()` when the MCP server
is running in Claude. Worth deciding before relying on the fallback.

## Shape of the implementation

### The seam already exists — and it is not the one this doc first named

The original reading was that a cloud inbox is one more `adapters/transport/`
module alongside `claude.py`, `codex.py` and `filebus.py`, routed by kind in
`commands/messages.send`. The instinct was right — nothing about the bus needs
rearranging — but it pointed at the wrong seam.

**The seam is `register()`.** A desktop peer is reached by a bridge process
(`agent-bus bridge --provider claude|chatgpt`, one per provider) that registers
as an ordinary bus peer, watches its own file inbox for outbound mail, pushes it
to the cloud, polls for replies, and routes those back through
`commands.messages.send`.

So there is no cloud transport adapter at all: `transport.for_kind("desktop")`
returns `None`, and mail for a desktop peer takes the plain filebus path that
already exists. The routing table is untouched.

What that buys over the adapter reading:

- **`dependencies = []` survives.** The bridge is stdlib `urllib` plus a bearer
  token, and Firestore is never spoken to from a user's machine — only by the
  server, which is a separate deployable.
- **One code path for replies.** A reply from Claude Desktop addressed to a
  Claude Code session must go through the *router*, not `store.send_message`, or
  it lands as an unread in an inbox Claude never polls — recreating the exact
  orphan the pre-acked mailbox dissolved.
- **1:1 between process and cloud mailbox.** Two bridges, so a wedged ChatGPT
  bridge cannot affect the Claude Desktop one.
- **A dead bridge fails loudly at the sender** rather than silently filling an
  inbox nobody drains — see the liveness note in the addressing section.

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

## Message size: sized to how the predecessor was actually used

Rule: **agent-bus adopts the narrowest constraint of any supported harness that
already implements cross-session messaging** — a message the bus accepts must be
one every peer can receive, or the bus is lying to the sender.

But the harness limits turned out to be mostly unmeasurable, and there is a
better source of truth: 107 real messages from the predecessor, in
`bonsai-2026/.claude/claude2claude/archive` (69) and `claude2gpt/archive` (38).

### What the archives say (measured, body only, header stripped)

| | c2c (69) | c2gpt (38) | combined |
|---|---|---|---|
| median | 3,730 | 3,572 | **3,730** |
| p90 | 6,042 | 5,933 | 6,073 |
| p95 | 8,107 | 6,519 | 7,554 |
| p99 | 18,724 | 8,976 | 15,865 |
| max | 24,511 | 10,413 | **24,511** |
| min | 32 | 921 | 32 |

**The current cap of 1,000,000 chars (`store.py:38`) is 41× the largest message
ever sent.** It is not a constraint, it is an absence of one.

### The tail is prose, not payload

The decisive number: **1.9% of all message text sits inside code fences.** The
largest message, 24,511 chars, is 94% prose with six small snippets. Three of
the five largest contain no code block at all.

So the size tail is long-form *reasoning* — exactly the review-and-argue traffic
this exists to carry — and not agents pasting files. The discipline "if what
you are sending is a file, send a pointer to it" was already being followed
without being enforced.

That matters for choosing a cap: it does not need headroom for file-pasting,
because nobody was file-pasting. It needs headroom for one more paragraph than
the longest argument anyone has yet made.

### Proposed: 32,768 chars

| cap | rejects, of 107 | note |
|---|---|---|
| 4,096 | 40 (37%) | far too tight — below the median-plus-a-bit |
| 8,192 | 5 (4.7%) | cuts genuine review messages |
| 16,384 | 1 (0.9%) | cuts the one real outlier |
| **32,768** | **0** | 34% headroom over the largest ever sent |
| 65,536 | 0 | 2.7× headroom; safe, but wide enough to admit a pasted source file |
| 1,000,000 | 0 | today; 41× the observed max |

32,768 accepts every message in the archive with room to spare, and is small
enough that a pasted source file or a real diff fails — which is the behaviour
we want, since the failure teaches the pointer discipline at the moment it is
needed. 65,536 is the conservative alternative if the outlier feels too close.

`MAX_UNREAD` (50) is untouched by this data — 107 messages across two channels
over roughly three days is low volume, and with pre-acked inboxes and a brief
TTL the unread queue stays short by construction.

### Harness limits: what was and was not established

| | limit | source |
|---|---|---|
| agent-bus | 1,000,000 chars; 50 unread | `store.py:38-39` |
| Codex | **none found** on `thread/queue/add` | source read; the 2MB cap at `rmcp-client/src/event_notification_transport.rs:23` is queued MCP event notifications, a different path |
| Claude Code | **not established** | binary strings show only libc errno tables; it is compiled, so absence of a string is not absence of a limit |
| Grok | generous, per prior testing | not pinned to a number |

Claude's is the one worth establishing, and the honest way is empirical — send
increasing sizes at a live peer until it refuses. Not urgent: at 32,768 we are
two orders of magnitude below anything a messaging path is likely to refuse.

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
