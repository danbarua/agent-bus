"""The bridge: a team secretary for peers that cannot be reached directly.

Claude Desktop and ChatGPT are reachable only over public HTTPS, never wake on
their own, and are prodded by a human. A bridge process stands in for one of
them locally, one process per address, and does what a secretary does:

    takes the message, says "got it -- they haven't read it yet",
    knows who is in the office, and passes replies back.

**Not an AI secretary.** It never reads, summarises, filters, re-orders or acts
on the content of anything it carries. It is deterministic plumbing, and the
moment it starts interpreting messages it becomes a participant in the
conversation rather than the thing that moves it. If you are tempted to make it
clever, that is the line you are crossing.

Being an ordinary registered peer is what makes it cheap. It has an address, a
mailbox, liveness and pruning because `register()` supplies all four, so nothing
in the routing table, the address spaces or the transports needed a new branch
for `desktop`. Its liveness is genuinely useful rather than nominal: mail cannot
be sent to a bridge that is not running (see commands.messages), so a dead
secretary fails at the sender instead of accumulating post nobody collects.

The receipt is the part worth being precise about. Delivery to the bridge is
*instant* and really did succeed -- it is a live local process. Delivery to the
desktop peer is *ultimate*, and happens whenever the user prods it. Those are
different facts, and a sender told only the first one would reasonably assume
the second. So the receipt states both, in one line.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from agent_bus import __version__
from agent_bus import log as bus_log
from agent_bus.commands import agents, messages
from agent_bus.protocol import AgentTarget, BridgeAddress, MessageId

from .subscriptions import Subscriptions

# The kind that changes what a bridge is. Not a flag: #59 is explicit that
# the kind is the whole statement and a flag repeating it is two things
# that must agree.
WEBHOOK_KIND = "webhook"

# A bridge is identified by `<kind>:<name>` -- the address of the peer it stands
# in for, and the whole of it. One long-running chat per name talks to the
# coding team: there is deliberately no conversation dimension, so there is
# deliberately no more than one bridge per address.
#
# There is no list of permitted kinds or names here, and there must not be. A
# third job -- `webhook:github` -- would otherwise mean editing an enum, a
# display map and an argparse `choices=` before it could start, which is three
# registries to keep in step for something the caller already told us.

# Outbound is a local file inbox and wants to feel immediate; inbound is a
# billed network call against a peer that is hard-asynchronous by definition.
# Polling the cloud at the outbound cadence would spend money to learn nothing.
OUTBOUND_POLL_SECONDS = 1.0

# The inbound poll is adaptive: a fixed 30s carried ~5,600 requests a day for a
# handful of messages, and the traffic is bursty in the way a conversation is.
# So poll fast for a window after anything moves, and slowly when nothing has.
#
# **This does not improve first-message latency, and cannot.** Nothing local
# knows a message was written until it asks, so the first one after a quiet
# spell waits up to the idle interval; what the busy window buys is the reply
# loop after it, which is where the waiting is actually felt.
INBOUND_POLL_IDLE_SECONDS = 120.0
INBOUND_POLL_BUSY_SECONDS = 5.0
BUSY_WINDOW_SECONDS = 60.0
INBOUND_POLL_SECONDS = INBOUND_POLL_IDLE_SECONDS
ROSTER_PUBLISH_SECONDS = 30.0

# A 30-day credential in a service that runs for months has one interesting
# day, and it is not the day it was minted. The bridge already reads the claim
# to find its own server, so it can say this rather than discover it as a 401.
TOKEN_WARNING_DAYS = 7.0
EXPIRY_CHECK_SECONDS = 86400.0


class CloudClient(Protocol):
    """The transport operations, and nothing else.

    Named as a protocol rather than a base class so the bridge can be tested
    without a network, and so the public contract stays something we state
    explicitly rather than something that emerges from an implementation.

    Four of these move mail. `read` does not: it answers where a message got
    to, and is the one operation nothing in the loop calls.
    """

    def push(self, address: BridgeAddress, message: dict[str, Any]) -> str: ...

    def pull(self, address: BridgeAddress) -> list[dict[str, Any]]: ...

    def ack(self, address: BridgeAddress, ids: list[str]) -> None: ...

    def publish_roster(self, address: BridgeAddress, agents: list[dict[str, Any]]) -> None: ...

    def read(self, address: BridgeAddress, message_id: MessageId) -> dict[str, Any]: ...


def bridge_address(kind: str, name: str) -> BridgeAddress:
    """The only place a `<kind>:<name>` is made, so the shape check and the
    type assertion cannot drift apart.

    Shape, not membership. `:` is the address separator, so a name carrying one
    would silently make a different address than the caller asked for -- worth
    refusing. What kinds exist is not our list to keep.

    `agent-bridge read` built this string by hand and skipped the check
    entirely, which is the kind of gap a constructor closes by existing.
    """
    for label, value in (("kind", kind), ("name", name)):
        if not value or ":" in value:
            raise ValueError(f"{label} must be non-empty and contain no ':' (got {value!r})")
    return BridgeAddress(f"{kind}:{name}")


def bridge_name(address: BridgeAddress) -> AgentTarget:
    """The name this bridge registers under: `desktop:claude` -> `desktop-claude`.

    A mechanical transform of the address rather than a lookup, so a kind nobody
    anticipated gets a sensible name without being added anywhere.
    """
    return AgentTarget(address.replace(":", "-", 1))


def receipt_for(address: BridgeAddress) -> str:
    """The one-line receipt sent back to whoever wrote in.

    Terse on purpose: it is an FYI, not a conversation, and it is marked
    automated so the sender does not reply to it. It says the two things a
    uniform "delivered" would conflate -- that the hand-off succeeded, and that
    the actual reader has not seen it yet.
    """
    # The address, not a prettified display name. There was a map of those; it
    # was a second list to keep in step, and `desktop:claude` is what a sender
    # would have to type anyway.
    who = address
    # The wording below states the queued expectation in prose. Pinned by
    # test_a_desktop_peer_is_queued_and_everything_else_is_now rather than by an
    # assert here: this runs per message, and `python -O` strips asserts anyway.
    return (
        f"[auto] Got it -- queued for {who}. Not read yet: {who} has no way to "
        "wake, so a human has to prod it. No reply needed."
    )


def sender_name(msg: dict[str, Any]) -> str | None:
    """Who wrote in.

    The public shape (protocol.message_to_json, which is what commands.messages
    hands out) serializes the sender under `from`. The storage struct underneath
    spells it `from_`. Reading the wrong one fails silently in two directions at
    once -- no receipt is ever sent, and every forwarded message is attributed
    to "unknown" -- so it is worth a named function and a test rather than an
    inline `.get`.
    """
    ref = msg.get("from") or {}
    if isinstance(ref, dict):
        return ref.get("name") or None
    return None


def _join(address: BridgeAddress, home: str | None) -> dict[str, Any]:
    """Join the bus and wait until peers can actually reach us.

    agents.join is register plus a published listener, and it does not return
    until the listener has bound. That last part is not tidiness: the listener
    is a detached process, so a bridge that started serving as soon as
    start_uds_listen returned had no socket to send *from* yet. A reply already
    queued when it started was dropped, reported as the recipient refusing it.

    The listener is what puts the bridge in Claude's *native* ListAgents, so
    "send this to Claude Desktop" is a plain SendMessage rather than something
    Claude has to be taught to do through a CLI. pi proves the shape: no MCP
    server at all, and it still messages Claude, because `listen` publishes the
    Claude-shaped session and socket.

    It is also what gives the bridge a socket of its own to reply *from*. An
    outbound frame carries the sender's socket as its reply address, so without
    one the receipt could not go back to a Claude peer at all.
    """
    role = address
    # One bridge per address, and it is the bridge's job not to mess this up.
    # `<kind>:<name>` is the *whole* address of the peer we stand in for --
    # no conversation dimension and there will not be one, so two holders is not
    # an ambiguity to resolve at delivery, it is a thing that must not exist.
    #
    # The bus will not stop us. register() de-collides names but not aliases, so
    # a second bridge registers cleanly as `desktop-claude-2`, appears in `list`
    # and competes for the address; find_entry then returns whichever sorts
    # first. Refusing here keeps that knowledge where it belongs and leaves the
    # bus dumb.
    #
    # Check-then-act, so two bridges started in the same instant can still both
    # pass. That is not the case this guards: the case is a second one started
    # later, by hand, which is the one that happens.
    # Held by *us* is not a collision: a bridge that re-joins in the same
    # process is the same bridge. Ten tests caught this on the guard's first
    # run by calling _join and then bridge(), which joins again.
    held = next((a for a in agents.list_agents(home=home)
                 if role in (a.get("aliases") or []) and a["pid"] != os.getpid()),
                None)
    if held is not None:
        raise RuntimeError(
            f"{role} is already held by {held['name']} (pid {held['pid']}). "
            f"There is one bridge per address: stop that one before starting "
            f"another, or run a different one."
        )

    # The kind is the caller's, not a constant. Hardcoding "desktop" here made
    # `--kind` change the address and not the registration, so a webhook bridge
    # registered as a desktop peer -- and `delivery_expectation` keys on kind,
    # so it would have told senders to expect a human to prod a GitHub webhook.
    #
    # Not added to KNOWN_KINDS: kinds are open strings (`normalize_kind` accepts
    # anything non-empty), and what belongs in the hint list is a product
    # decision, not something a bridge grants itself by starting.
    kind = address.split(":", 1)[0]
    entry = agents.join(
        bridge_name(address),
        kind,
        pid=os.getpid(),
        home=home,
        aliases=[role],
    )
    if not entry.get("reachable"):
        raise RuntimeError(
            f"{entry['name']} registered but no peer can reach it: the listener "
            "did not come up. Anything it was asked to carry would be dropped."
        )
    return entry


def _wire(msg: dict[str, Any]) -> dict[str, Any]:
    """The cloud payload for one local message.

    The local id travels as `id` and is the dedupe key: a forward that is
    retried -- by the next pass, or by recovery after a crash -- is absorbed
    cloud-side rather than surfacing twice in someone's chat.
    """
    return {
        "id": msg["id"],
        "from": sender_name(msg) or "unknown",
        "summary": msg.get("summary") or "",
        "text": msg.get("text") or "",
        "ts": msg.get("ts"),
    }


def _drain_previous(client: CloudClient, address: BridgeAddress, home: str | None,
                    log: Any) -> int:
    """Forward what the previous incarnation accepted and never sent on.

    **Before `_join`, never after.** A restarted bridge reclaims its name and
    not its inbox: `register` mints a new id when the old row is dead, and the
    mailbox is keyed by id. So the previous mailbox is reachable only through
    the role -- `find_entry` prefers a live match and returns the stale one when
    there is none -- and only until we re-register. Measured: visible before
    `_join`, empty after.

    Without this the push-then-ack ordering promises something it does not
    deliver. A crash between push and ack is supposed to retry; instead the
    message sits in an inbox nobody reads and expires. That is losing mail
    silently, which is the failure direction the design rejected.

    Bounded by construction: nothing new can arrive for a dead bridge, because
    the router refuses to deliver to a receiver that is not live. What is here
    is only what arrived while the last process was alive and unforwarded.

    No receipt for these. We are not registered yet, so there is no identity to
    send one from -- and a receipt for mail recovered from a crash is a stranger
    thing to receive than silence.

    **Only from a dead holder.** A live one owns its own mail: it is either this
    process having already joined, or a second bridge that `_join` is about to
    refuse. Draining a live inbox here would swallow ordinary traffic before the
    loop could forward it *and send its receipt* -- which is exactly what the
    receipt tests caught when this guard was missing.
    """
    role = address
    if agents.dead_holder(role, home=home) is None:
        return 0  # nothing has ever held this role, or something live does
    pending = messages.inbox(target=role, unread_only=True, home=home)
    recovered = 0
    for msg in pending:
        try:
            client.push(address, _wire(msg))
            messages.ack(msg["id"], target=role, home=home)
            recovered += 1
        except Exception as e:  # noqa: BLE001  # client.push is a Protocol implementation
            # Left unread: it stays recoverable on the next start, and the TTL
            # is the backstop. Carrying on is right -- one unforwardable message
            # must not stop a bridge coming back.
            log(f"[bridge] could not recover {msg.get('id')}: {e}")
            bus_log.warn("could not recover", trace_id=msg.get("id"), error=str(e))
    return recovered


def _forward_one(client: CloudClient, address: BridgeAddress, entry: Any, msg: dict[str, Any],
                 home: str | None, log: Any, auto_reply: bool) -> None:
    """Push one message, then acknowledge it locally.

    Push-then-ack, deliberately. A crash between the two redelivers rather than
    loses, which is the right direction to fail for a courier: the cloud `write`
    carries the local message id as a dedupe key, so the duplicate is absorbed
    there instead of surfacing twice in someone's chat.

    Both halves are recorded, at INFO, carrying the message id. This is louder
    than a courier would normally log a success, and that is the point: what is
    being delivered is a context update to an agent that needs it in time to
    matter, so a delivery that worked has to be visible as a chain rather than
    inferred from the absence of a failure. Until this existed the bridge
    logged only when it failed -- every message that went wrong was visible and
    every message that went right was not.
    """
    client.push(address, _wire(msg))
    # Between the two, so the record survives a crash in the ack below: the
    # cloud has it, and that is the fact a redelivery has to be read against.
    bus_log.info("forwarded", trace_id=msg["id"], to=address,
                 sender=sender_name(msg))
    messages.ack(msg["id"], target=entry["name"], home=home)
    bus_log.info("acked locally", trace_id=msg["id"], name=entry["name"])
    if auto_reply:
        _send_receipt(address, entry, msg, home, log)


def _send_receipt(address: BridgeAddress, entry: Any, msg: dict[str, Any],
                  home: str | None, log: Any) -> None:
    """Reply to the sender the way any peer would -- through the router.

    Best-effort by design. The message *has* been accepted for forwarding, so a
    receipt that cannot be delivered must not undo that or report a failure that
    did not happen. The commonest reason it fails is the honest one: the sender
    has since exited, and commands.messages refuses to write to a peer that is
    no longer live.
    """
    sender = sender_name(msg)
    if not sender:
        return
    try:
        messages.send(
            to=sender,
            text=receipt_for(address),
            summary="auto-receipt",
            from_name=entry["name"],
            home=home,
        )
    except Exception as e:  # noqa: BLE001  # the router can raise anything; a receipt must never fail a delivery
        log(f"[bridge] receipt to {sender} not delivered: {e}")
        bus_log.warn("receipt not delivered", to=sender, error=str(e))


def _handle_control(entry: Any, msg: dict[str, Any], subs: Any,
                    home: str | None, log: Any) -> None:
    """A message addressed *to* a webhook bridge, which is the only kind it
    gets: there is no cloud inbox for one to be forwarded to.

    A peer reading its own mail, not a courier inspecting cargo -- the
    distinction #59 draws so that "not an AI secretary" still holds.
    """
    from . import control

    sender = sender_name(msg)
    if not sender:
        # No addressee, so no reply is possible. Acked rather than left: the
        # next poll would hand back the same unanswerable message forever.
        log(f"[bridge] a control message named no sender: {msg.get('id')}")
        bus_log.warn("control message with no sender", trace_id=msg.get("id"))
        messages.ack(msg["id"], target=entry["name"], home=home)
        return
    reply = control.handle(msg.get("text") or "", sender, subs)
    if reply is None:
        # Not a verb, and nowhere upward to send it. Answered rather than
        # dropped: a message into a bridge that silently vanishes is the
        # failure an agent cannot see.
        reply = ("I only take SUBSCRIBE <topic>, UNSUBSCRIBE <topic> and "
                 "SUBSCRIPTIONS. Nothing here is forwarded anywhere.")
    messages.send(to=AgentTarget(sender), text=reply, summary="subscriptions",
                  from_name=AgentTarget(entry["name"]), home=home)
    messages.ack(msg["id"], target=entry["name"], home=home)
    bus_log.info("control", verb=(msg.get("text") or "").split(" ")[:1],
                 trace_id=msg["id"], to=sender)


def _fan_out_batch(entry: Any, events: list[dict[str, Any]], subs: Any,
                   home: str | None, log: Any) -> None:
    """A whole poll's worth, so several events on one topic arrive as one.

    #106: the poll already *is* the batch, so collapsing what a single cycle
    drained needs no new transport and no debounce. Four merges while an agent
    is mid-task should be one message, not four interruptions into a live
    conversation.

    Grouped per subscriber and per topic, which is #106's collapse key --
    merges into different branches are different facts, and the branch is
    already part of the topic (`pr.merge.main`), so grouping by topic groups by
    branch for free.
    """
    from . import notify, topics

    # subscriber -> topic -> the events that matched it
    grouped: dict[str, dict[str, list[tuple[str, dict[str, Any]]]]] = {}
    for msg in events:
        event = msg.get("summary") or ""
        try:
            payload = json.loads(msg.get("text") or "{}")
        except ValueError:
            log(f"[bridge] dropped an event that was not JSON: {msg.get('id')}")
            bus_log.warn("event was not JSON", trace_id=msg.get("id"))
            continue
        matched = topics.topics_for(event, payload)
        if not matched:
            # TRACE, not INFO: #59 accepts that most of the firehose is
            # discarded here, so a line per discarded event would be logging
            # the design rather than an event.
            bus_log.trace("event matched nobody", trace_id=msg.get("id"), event=event)
            continue
        for topic in matched:
            for who in subs.subscribers_for({topic}):
                grouped.setdefault(who, {}).setdefault(topic, []).append((event, payload))

    for who, by_topic in sorted(grouped.items()):
        for topic, matched_events in sorted(by_topic.items()):
            if len(matched_events) == 1:
                event, payload = matched_events[0]
                summary, text = notify.notification({topic}, event, payload)
            else:
                summary, text = notify.digest(topic, matched_events)
            try:
                messages.send(to=AgentTarget(who), text=text, summary=summary,
                              from_name=AgentTarget(entry["name"]), home=home)
                bus_log.info("delivered event", to=who, topic=topic,
                             count=len(matched_events))
            except Exception as e:  # noqa: BLE001  # messages.send refuses a dead peer
                # A dead subscriber fails loudly rather than accumulating
                # silently (#68), and one failure must not hold back the rest.
                log(f"[bridge] could not deliver to {who}: {e}")
                bus_log.warn("could not deliver event", to=who, error=str(e))


def _fan_out(entry: Any, event_msg: dict[str, Any], subs: Any,
             home: str | None, log: Any) -> bool:
    """One event, one addressed copy per subscriber. Never a broadcast (#59).

    Returns whether the cloud copy may be acked. An event nobody subscribes to
    is acked: #59 accepts that most of the firehose is discarded here, and
    keeping it would re-pull the same unwanted event every poll until it
    expired.
    """
    from . import notify, topics

    event = event_msg.get("summary") or ""
    try:
        payload = json.loads(event_msg.get("text") or "{}")
    except ValueError:
        log(f"[bridge] dropped an event that was not JSON: {event_msg.get('id')}")
        bus_log.warn("event was not JSON", trace_id=event_msg.get("id"))
        return True
    matched = topics.topics_for(event, payload)
    wanted = subs.subscribers_for(matched)
    if not wanted:
        # TRACE, not INFO: #59 accepts that most of the firehose is
        # discarded here, so a line per discarded event would be logging the
        # design rather than an event.
        bus_log.trace("event matched nobody", trace_id=event_msg.get("id"),
                      event=event, topics=sorted(matched))
        return True

    summary, text = notify.notification(matched, event, payload)
    for who in sorted(wanted):
        try:
            messages.send(to=AgentTarget(who), text=text, summary=summary,
                          from_name=AgentTarget(entry["name"]), home=home,
                          message_id=MessageId(f"{event_msg['id']}-{who}"))
            bus_log.info("delivered event", trace_id=event_msg.get("id"), to=who)
        except Exception as e:  # noqa: BLE001  # messages.send refuses a dead peer
            # A dead subscriber fails loudly rather than accumulating silently
            # (#68). One failure must not hold the others' copies back.
            log(f"[bridge] could not deliver to {who}: {e}")
            bus_log.warn("could not deliver event", trace_id=event_msg.get("id"),
                         to=who, error=str(e))
    return True


def _deliver_reply(entry: Any, reply: dict[str, Any], home: str | None, log: Any) -> bool:
    """Hand an inbound reply to the router, not to the store.

    Returns whether the cloud copy may be acked, which is **not** the same as
    "we tried". Every branch here used to return True, so the caller acked
    whatever it had attempted and the return value decided nothing.

    This distinction is load-bearing. A reply from Claude Desktop addressed to a
    *Claude Code* session, written straight into a file inbox, would sit unread
    forever -- Claude never polls one. Through the router it goes out over UDS,
    and the durable copy is written already-acked, which is the arrangement that
    dissolved the orphaned inboxes in the first place.
    """
    to = reply.get("to")
    if not to:
        # Nothing to retry toward. Keeping it would re-pull the same
        # unaddressable message every poll until it expired.
        log("[bridge] dropped a reply with no addressee")
        bus_log.warn("dropped a reply with no addressee", trace_id=reply.get("id"))
        return True
    try:
        messages.send(
            to=to,
            text=reply.get("text") or "",
            summary=reply.get("summary") or "",
            from_name=entry["name"],
            home=home,
            # The id the cloud gave it. Without this the message changes
            # identity here and nothing joins the two halves of its journey --
            # and a redelivery, which the retry below exists to cause, arrives
            # as a second message rather than the same one again.
            message_id=reply.get("id"),
        )
        bus_log.info("delivered", trace_id=reply.get("id"), to=to,
                     sender=entry["name"])
        return True
    except Exception as e:  # noqa: BLE001  # the router can raise anything
        # **Leave it unacked and let the next poll retry.** This used to drop,
        # on the reasoning that a stale reply delivered late is worse than none
        # -- but that is a second staleness policy layered on the one the design
        # already has. Messages expire uniformly and briefly, and `expireAt` is
        # what ends a delivery nobody can take.
        #
        # Dropping was also wrong about the common case. A receiver is
        # routinely gone for seconds: a user stops a Claude session to rename
        # its worktree and starts it again. Refusing once and discarding turns
        # a self-healing absence into lost mail.
        log(f"[bridge] holding a reply for {to}, will retry: {e}")
        bus_log.warn("holding a reply for retry", to=to, error=str(e))
        return False


def _roster_snapshot(address: BridgeAddress, me: dict[str, Any],
                     home: str | None) -> list[dict[str, Any]]:
    """Who is in the office, for the desktop peer to check before writing.

    Published rather than queried, because nothing can reach into this machine.
    It carries the ordinary TTL, so a bridge that stops running stops refreshing
    it and the listing empties by itself -- bridge liveness needs no separate
    heartbeat.

    The bridge itself is excluded, by **pid**: a secretary listed among the
    people it is describing invites the desktop peer to write to it, which
    routes mail back to the thing that just delivered it.

    It used to exclude by comparing the row's id against the id `join` returned,
    and that is what put `desktop-claude` into its own broadcast in CI.

    One id is not enough, and neither is one pid. A joined bridge occupies the
    roster twice over: the row it registered, and the Claude-shaped session its
    **detached listener** publishes -- which carries the listener's pid and its
    own address. Those normally reconcile, because the listener records the
    session address as an alias on our row, but a row that is re-created loses
    the alias and the session stands alone under our name.

    So the test is "is this us" asked three ways that cannot all drift at once:
    our process, our current name, or the role only we may hold.
    """
    mine = os.getpid()
    role = address
    return [
        {"name": a["name"], "kind": a["kind"], "id": str(a["id"])}
        for a in agents.list_agents(home=home)
        if not (a["pid"] == mine
                or a["name"] == me["name"]
                or role in (a.get("aliases") or []))
    ]


def _me(address: BridgeAddress, home: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    """Our own roster row, now -- not the copy `join` handed back at startup.

    A name is renameable (register de-collides on collision) and an id is not
    stable across a re-registration, so a value read once is a guess by the
    second pass. Two CI failures were exactly that: a stale id put the bridge
    into its own roster broadcast, and a stale name asked for an inbox the bus
    said it had never heard of.

    Resolved by the two things that cannot drift: our pid, and the role alias
    `<kind>:<name>`, which _join has just guaranteed only we hold.

    Falls back to the joined entry rather than raising. A bridge that cannot
    find itself for one pass should carry on with the last thing it knew and
    say so through the failure it hits next, not die holding queued mail.
    """
    role = address
    mine = os.getpid()
    for a in agents.list_agents(home=home):
        if a["pid"] == mine or role in (a.get("aliases") or []):
            return a
    return fallback


def expiry_warning(expires_at: float | None, now: float) -> str | None:
    """The line to log about a token running out, or None if there is nothing
    to say.

    Pure, and separate from the loop that schedules it, because the branch that
    matters fires once a month at most. A warning that is only exercised on the
    day it is needed is a warning that has never been run.
    """
    if expires_at is None:
        return None
    days = (expires_at - now) / 86400.0
    if days <= 0:
        return ("[bridge] the cloud token EXPIRED "
                f"{abs(days):.1f} days ago; every call to the cloud is failing")
    if days <= TOKEN_WARNING_DAYS:
        return (f"[bridge] the cloud token expires in {days:.1f} days. "
                "Mint a new one and replace it before it does -- see "
                "docs/running-the-bridge.md")
    return None


def inbound_interval(since_traffic: float, idle: float,
                     busy: float = INBOUND_POLL_BUSY_SECONDS,
                     window: float = BUSY_WINDOW_SECONDS) -> float:
    """How long to wait before asking the cloud again.

    A pure function of one number, because the alternative -- reading the
    schedule off a running loop -- is a thing you can only check by waiting,
    and a poll interval nobody can check is a poll interval nobody will change.

    `busy` is clamped to `idle`, so an idle interval shorter than the busy one
    simply turns the adaptation off. That is what a test asking for every-pass
    polling wants, and it means `inbound_poll=0` still means 0.
    """
    return min(busy, idle) if since_traffic < window else idle


def bridge(
    kind: str,
    name: str,
    client: CloudClient,
    home: str | None = None,
    once: bool = False,
    log: Any = None,
    auto_reply: bool = False,
    outbound_poll: float = OUTBOUND_POLL_SECONDS,
    inbound_poll: float = INBOUND_POLL_IDLE_SECONDS,
    expires_at: float | None = None,
) -> int:
    """Run the secretary until interrupted.

    `once` runs a single pass of each duty, which is what the tests drive: a
    loop that can only be observed by waiting is a loop nobody checks.
    """
    address = bridge_address(kind, name)
    # A webhook bridge is a different animal: it answers its own mail instead
    # of forwarding it, and authors messages from an event stream instead of
    # couriering them. `subs` being non-None is what says which one this is --
    # the kind decides, the same way it decides there is no cloud inbox.
    subs = Subscriptions() if kind == WEBHOOK_KIND else None
    log = log or (lambda line: print(line, flush=True))
    # Which of possibly several agent-bridge processes this record is from
    # (#197) -- desktop:claude and desktop:chatgpt share agent-bridge.jsonl,
    # and this is the field that tells them apart. Set here rather than
    # left to the CLI entry point: `bridge()` is also called directly, by
    # tests and by anything that does its own `_join`.
    bus_log.identify(address=address)

    recovered = _drain_previous(client, address, home, log)
    if recovered:
        log(f"[bridge] forwarded {recovered} message(s) left by the previous run")
        bus_log.info("forwarded backlog", count=recovered)

    entry = _join(address, home)
    if subs is not None:
        # Stated, not discovered (#68). Subscriptions are in memory until #249
        # decides the Firestore op, so a restart drops them -- and an agent
        # that is silently deaf has no way to find that out.
        log(f"[bridge] {entry['name']} holds no subscriptions after a restart; "
            "subscribers must SUBSCRIBE again")
        bus_log.info("subscriptions are not durable yet", name=entry["name"])
    log(f"[bridge] {entry['name']} standing in for {address}"
        f"{'; auto-reply on' if auto_reply else ''}")
    bus_log.info("standing in", name=entry["name"], auto_reply=auto_reply)

    try:
        return _serve(client, address, entry, home, log, auto_reply, once,
                      outbound_poll, inbound_poll, expires_at, subs)
    finally:
        # Not on the `once` path. That is a single pass of the duties driven by
        # a caller that did its own `_join` and is still using the listener
        # afterwards; leaving there would tear down something we did not put
        # up. A bridge that stops *serving* is the one that has to let go.
        if not once and agents.leave(entry["name"], home=home):
            log(f"[bridge] {entry['name']} left the bus")
            bus_log.info("left the bus", name=entry["name"])


def _serve(client, address, entry, home, log, auto_reply, once,
           outbound_poll, inbound_poll, expires_at, subs=None) -> int:
    """The loop itself, so `bridge` can own the leaving.

    `subs` non-None means this is a webhook bridge: it answers its own mail
    rather than forwarding it, and fans events out rather than delivering
    replies. The two paths are the same loop because the *shape* is the same --
    drain the local inbox, poll the cloud -- and only what happens to each
    message differs.
    """
    last_inbound = 0.0
    # Busy at startup, not idle. A bridge that has just come up is the one most
    # likely to have mail waiting -- it is either the first run or the one after
    # a crash, and both leave something in the queue.
    last_traffic = time.monotonic()
    # Checked immediately, not in 24 hours: a service restarted every day would
    # otherwise never reach the branch that warns.
    last_expiry_check = 0.0
    while True:
        if time.monotonic() - last_expiry_check >= EXPIRY_CHECK_SECONDS:
            last_expiry_check = time.monotonic()
            warning = expiry_warning(expires_at, time.time())
            if warning:
                log(warning)
                # expires_at is not None here -- expiry_warning() only
                # returns a string when it isn't. Recomputed rather than
                # returned from expiry_warning(), which stays a pure
                # string-or-None function its own tests already cover.
                bus_log.warn("token expiry",
                            days_remaining=round((expires_at - time.time()) / 86400.0, 1))
        me = _me(address, home, entry)
        for msg in messages.inbox(target=me["name"], unread_only=True, home=home):
            last_traffic = time.monotonic()
            try:
                if subs is not None:
                    _handle_control(me, msg, subs, home, log)
                    continue
                _forward_one(client, address, me, msg, home, log, auto_reply)
            except Exception as e:  # noqa: BLE001  # client.push is a Protocol implementation
                # Left unread on purpose: the next pass retries it.
                log(f"[bridge] could not forward {msg.get('id')}: {e}")
                bus_log.warn("could not forward", trace_id=msg.get("id"), error=str(e))

        now = time.monotonic()
        if once or now - last_inbound >= inbound_interval(now - last_traffic, inbound_poll):
            last_inbound = now
            try:
                client.publish_roster(address, _roster_snapshot(address, me, home))
            except Exception as e:  # noqa: BLE001  # client.publish_roster is a Protocol implementation
                log(f"[bridge] roster not published: {e}")
                bus_log.warn("roster not published", error=str(e))
            try:
                replies = client.pull(address)
            except Exception as e:  # noqa: BLE001  # client.pull is a Protocol implementation
                log(f"[bridge] could not pull: {e}")
                bus_log.warn("could not pull", error=str(e))
                replies = []
            # One ack per message, not one per batch. Acking at the end means a
            # crash part-way through redelivers everything already delivered in
            # that pass; acking as we go bounds that at the single message in
            # flight. Replies arrive a handful at a time, so the extra calls
            # cost less than the duplicates would.
            if replies:
                last_traffic = now
            if subs is not None:
                # The whole batch at once, so several events on one topic
                # arrive as one message (#106). Every one is acked either way:
                # #59 accepts that most of the firehose is discarded, and
                # keeping an unwanted event would re-pull it every poll until
                # it expired.
                _fan_out_batch(me, replies, subs, home, log)
                for r in replies:
                    if rid := r.get("id"):
                        with contextlib.suppress(Exception):
                            client.ack(address, [rid])
                replies = []
            for r in replies:
                delivered = _deliver_reply(me, r, home, log)
                if not delivered:
                    continue
                rid = r.get("id")
                if not rid:
                    continue
                try:
                    client.ack(address, [rid])
                    # The last hop, and the one that decides whether the next
                    # poll hands this message back. Without a record, "why did
                    # that arrive twice" has no evidence either way.
                    bus_log.info("acked in the cloud", trace_id=rid, to=address)
                except Exception as e:  # noqa: BLE001  # client.ack is a Protocol implementation
                    # Delivered but unacked: the next poll hands it back and we
                    # deliver twice. At-least-once, which is the right direction.
                    log(f"[bridge] delivered {rid} but could not ack it: {e}")
                    bus_log.warn("delivered but could not ack", trace_id=rid, error=str(e))

        if once:
            return 0
        time.sleep(outbound_poll)


#: Who we are, in the server's request log. `user-agent` is in the cloud's
#: LOGGED_HEADERS, so this is legible the moment it is sent -- no server change.
#: Without it urllib says `Python-urllib/3.x`, which is indistinguishable from
#: anything else written in Python, while Claude Desktop announces itself as
#: `Claude-User`. RFC 9110 product/version form: `+` already means something
#: inside a version string (`0.2.11.dev46+gccc48a1`), so a `+` separator would
#: put two different meanings of it in one token.
USER_AGENT = f"agent-bus/{__version__}"


class HttpCloudClient:
    """The cloud, over HTTPS, with a bearer and nothing else.

    stdlib `urllib` on purpose: `dependencies = []` is the package's promise and
    this is the only component that speaks to a network at all. Firestore is
    never spoken to from a user's machine -- only the server does that.

    The bridge is not a third-party client and does not do the OAuth dance. The
    token is long-lived, minted out of band, and lives at
    `~/.agent-bus/cloud-token` (0600). One header.

    Addresses are passed for symmetry with `SpoolClient`, and the server ignores
    them: it takes the address from the token's claims, so a bridge cannot ask
    to be someone else.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _call(self, op: str, **body: Any) -> dict[str, Any]:
        payload = json.dumps({"op": op, **body}).encode()
        req = urllib.request.Request(  # noqa: S310 -- base_url is our own config, not input
            f"{self.base_url}/bridge", data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": USER_AGENT,
                     "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            # Raised, never swallowed. The bridge acks a local message only when
            # the forward really happened, so a client that reported success on
            # a refusal would lose mail while looking like it worked.
            #
            # RFC 7807 only: `detail` says what went wrong this time, `title`
            # what class of thing it was. No fallback to the old `{"error":
            # ...}` shape -- we own both ends and they ship together, so a
            # reader for a format nothing sends any more is cruft that outlives
            # the thing it was reading.
            detail = ""
            with contextlib.suppress(Exception):
                problem = json.loads(e.read() or b"{}") or {}
                detail = problem.get("detail") or problem.get("title") or ""
            raise RuntimeError(f"cloud refused {op}: HTTP {e.code} {detail}".strip()) from e

    def push(self, address: BridgeAddress, message: dict[str, Any]) -> str:
        return self._call("push", message=message).get("id", "")

    def pull(self, address: BridgeAddress) -> list[dict[str, Any]]:
        return self._call("pull").get("messages") or []

    def ack(self, address: BridgeAddress, ids: list[str]) -> None:
        self._call("ack", ids=list(ids))

    def publish_roster(self, address: BridgeAddress, agents: list[dict[str, Any]]) -> None:
        self._call("roster", agents=list(agents))

    def read(self, address: BridgeAddress, message_id: MessageId) -> dict[str, Any]:
        """`{"queue": "inbox"|"outbox"|None, "message": {...}|None}`.

        A query, not a hop: nothing in the loop calls it, and it does not ack.
        An operator asking where a message went must not be the reason it
        stops being redelivered.
        """
        return self._call("read", message_id=message_id)


KEYCHAIN_SERVICE = "agent-bus-cloud-token"


def _keychain_token() -> str | None:
    """The token out of the login Keychain, or None if it is not there.

    Shelling out to `security` rather than binding a framework: the package
    promises `dependencies = []`, and this is one subprocess at startup.

    Every failure is None rather than an exception, and they are all ordinary.
    No `security` binary means not macOS. Exit 44 means no such item. A locked
    Keychain means a service started before login -- and in each case the file
    below is the answer, so a bridge that could have run must not refuse to.
    """
    import subprocess

    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


#: A token in the environment, for a bridge pointed somewhere other than the
#: one the machine is set up for. The Keychain holds exactly one item, so
#: without this a second bridge -- against staging, say -- could not exist:
#: the Keychain shadows the file unconditionally, and every bridge on the
#: machine resolved the same credential and therefore the same environment.
TOKEN_ENV = "AGENT_BUS_CLOUD_TOKEN"  # noqa: S105 -- the variable name, not a token


def token_source(home: str | None = None) -> str:
    """`environment`, `keychain`, `file` or `none` -- what a bridge starting
    now would use.

    Worth saying out loud at startup. Three places can hold a token, two of
    them are invisible in a directory listing, and "which of these is live" is
    the first question anyone debugging a 401 has.

    The environment wins because it is the explicit one: a Keychain item is
    machine-wide setup and a file is left lying around, while an env var was
    typed by whoever started this process, for this process. It is also the
    only one that can differ between two bridges on the same machine.

    **Not where a credential should live day to day.** An environment variable
    is inherited by every child, and the ordinary place for the real one is the
    Keychain. This is for pointing a bridge at a second deployment, and for
    machines that are not Macs.
    """
    from agent_bus.paths import get_home

    if (os.environ.get(TOKEN_ENV) or "").strip():
        return "environment"
    if _keychain_token() is not None:
        return "keychain"
    if os.path.exists(os.path.join(home or get_home(), "cloud-token")):
        return "file"
    return "none"


def token_expiry(token: str) -> float | None:
    """The `exp` claim, or None if the token does not carry one.

    Unverified, like the issuer beside it and for the same reason: this is the
    user's own credential, and the server is what decides whether it is good.
    Read here only so the bridge can say *when* rather than discover it as a
    401 on day thirty.
    """
    payload = token.split(".", maxsplit=1)[0]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (ValueError, KeyError, TypeError):
        return None


def read_cloud_token(home: str | None = None) -> tuple[str, str] | None:
    """`(url, token)` from `AGENT_BUS_CLOUD_TOKEN`, else the Keychain, else
    `<home>/cloud-token`, else None.

    Absent is the ordinary case, not an error: a bridge with no token spools to
    disk instead, which is visible rather than silently dropped.

    **The environment wins, then the Keychain.** The Keychain is where the
    credential is meant to live, and a stale file left behind after moving it
    there would otherwise keep being used -- silently, and for as long as it
    stayed valid. The file remains the fallback because not every machine that
    runs this is a Mac, and a service that starts before the Keychain unlocks
    still has to start.

    `AGENT_BUS_CLOUD_TOKEN` sits above both because the Keychain holds exactly
    one item: without it, every bridge on a machine resolves the same
    credential and therefore the same deployment, and a second bridge pointed
    at staging is not expressible. An env var is per-process, which is exactly
    the granularity that problem needs. It is not where the day-to-day
    credential belongs -- every child process inherits it.

    **The URL comes out of the token's own `iss` claim.** One artifact to
    install, and it cannot drift from a URL configured beside it. The claim is
    read without verifying the signature -- deliberately: this is the user's own
    0600 config file, not network input, and anyone who can rewrite it has
    already won. The server still verifies; a token naming the wrong issuer
    fails at connect, loudly, rather than being quietly trusted.
    """
    from agent_bus.paths import get_home

    path = os.path.join(home or get_home(), "cloud-token")
    # Explicitly set for this process beats machine-wide setup -- and it is the
    # only lever that can differ between two bridges on one machine, which is
    # what makes pointing one at staging possible at all.
    token = (os.environ.get(TOKEN_ENV) or "").strip() or _keychain_token()
    if not token:
        try:
            with open(path, encoding="utf-8") as f:
                token = f.read().strip()
        except OSError:
            return None
    if not token:
        return None
    payload = token.split(".")[0]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        issuer = claims["iss"]
    except (ValueError, KeyError, TypeError):
        raise RuntimeError(
            f"{path} does not name a server. A bridge token carries the issuer "
            "it was minted for; this one has no `iss`, so there is nowhere to "
            "connect to. Mint a new one."
        ) from None
    return issuer, token


class SpoolClient:
    """A cloud that is a directory. Not a mock -- a real, inspectable stand-in.

    The server does not exist yet, and a bridge that cannot be run until it does
    is a bridge nobody has watched work. This writes what it would have sent and
    reads replies a human (or a test) drops in, so the secretary behaviour --
    the receipt especially -- can be exercised against real harnesses now.

    It is also the honest failure mode for a misconfigured install: mail spools
    visibly on disk instead of vanishing.
    """

    def __init__(self, root: str) -> None:
        self.root = root

    def _dir(self, address: BridgeAddress, leaf: str) -> str:
        d = os.path.join(self.root, address, leaf)
        os.makedirs(d, exist_ok=True)
        return d

    def push(self, address: BridgeAddress, message: dict[str, Any]) -> str:
        path = os.path.join(self._dir(address, "outbound"), f"{message['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(message, f, indent=2)
        return message["id"]

    def pull(self, address: BridgeAddress) -> list[dict[str, Any]]:
        d = self._dir(address, "inbound")
        out = []
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            rec.setdefault("id", fn[:-5])
            out.append(rec)
        return out

    def ack(self, address: BridgeAddress, ids: list[str]) -> None:
        d = self._dir(address, "inbound")
        for i in ids:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(d, f"{i}.json"))

    def publish_roster(self, address: BridgeAddress, agents: list[dict[str, Any]]) -> None:
        with open(os.path.join(self._dir(address, ""), "roster.json"), "w", encoding="utf-8") as f:
            json.dump(agents, f, indent=2)

    def read(self, address: BridgeAddress, message_id: MessageId) -> dict[str, Any]:
        """The same answer the cloud gives, from the directory.

        `outbound` is what this bridge pushed and `inbound` is what it would
        pull, so they map onto the cloud's `inbox` and `outbox` -- named from
        the *peer's* side there, from ours here. The reported queue uses the
        cloud's names, because the point of this verb is that one answer means
        the same thing wherever it came from.
        """
        for queue, leaf in (("inbox", "outbound"), ("outbox", "inbound")):
            path = os.path.join(self._dir(address, leaf), f"{message_id}.json")
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            rec.setdefault("id", message_id)
            return {"queue": queue, "message": rec}
        return {"queue": None, "message": None}
