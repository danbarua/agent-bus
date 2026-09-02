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
    """The four frozen operations, and nothing else.

    Named as a protocol rather than a base class so the bridge can be tested
    without a network, and so the public contract stays something we state
    explicitly rather than something that emerges from an implementation.
    """

    def push(self, address: str, message: dict[str, Any]) -> str: ...

    def pull(self, address: str) -> list[dict[str, Any]]: ...

    def ack(self, address: str, ids: list[str]) -> None: ...

    def publish_roster(self, address: str, agents: list[dict[str, Any]]) -> None: ...


def bridge_name(address: str) -> str:
    """The name this bridge registers under: `desktop:claude` -> `desktop-claude`.

    A mechanical transform of the address rather than a lookup, so a kind nobody
    anticipated gets a sensible name without being added anywhere.
    """
    return address.replace(":", "-", 1)


def receipt_for(address: str) -> str:
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


def _join(address: str, home: str | None) -> dict[str, Any]:
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


def _drain_previous(client: CloudClient, address: str, home: str | None,
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
    if any(role in (a.get("aliases") or []) for a in agents.list_agents(home=home)):
        return 0
    try:
        pending = messages.inbox(name=role, unread_only=True, home=home)
    except ValueError:
        return 0  # no previous incarnation; nothing to recover
    recovered = 0
    for msg in pending:
        try:
            client.push(address, _wire(msg))
            messages.ack(msg["id"], name=role, home=home)
            recovered += 1
        except Exception as e:  # noqa: BLE001  # client.push is a Protocol implementation
            # Left unread: it stays recoverable on the next start, and the TTL
            # is the backstop. Carrying on is right -- one unforwardable message
            # must not stop a bridge coming back.
            log(f"[bridge] could not recover {msg.get('id')}: {e}")
            bus_log.warn("could not recover", message_id=msg.get("id"), error=str(e))
    return recovered


def _forward_one(client: CloudClient, address: str, entry: Any, msg: dict[str, Any],
                 home: str | None, log: Any, auto_reply: bool) -> None:
    """Push one message, then acknowledge it locally.

    Push-then-ack, deliberately. A crash between the two redelivers rather than
    loses, which is the right direction to fail for a courier: the cloud `write`
    carries the local message id as a dedupe key, so the duplicate is absorbed
    there instead of surfacing twice in someone's chat.
    """
    client.push(address, _wire(msg))
    messages.ack(msg["id"], name=entry["name"], home=home)
    if auto_reply:
        _send_receipt(address, entry, msg, home, log)


def _send_receipt(address: str, entry: Any, msg: dict[str, Any],
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
        bus_log.warn("dropped a reply with no addressee", reply_id=reply.get("id"))
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


def _roster_snapshot(address: str, me: dict[str, Any],
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


def _me(address: str, home: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
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
    # Shape, not membership. `:` is the address separator, so a name carrying
    # one would silently make a different address than the caller asked for --
    # which is worth refusing. What kinds exist is not our list to keep.
    for label, value in (("kind", kind), ("name", name)):
        if not value or ":" in value:
            raise ValueError(f"{label} must be non-empty and contain no ':' (got {value!r})")
    address = f"{kind}:{name}"
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
    log(f"[bridge] {entry['name']} standing in for {address}"
        f"{'; auto-reply on' if auto_reply else ''}")
    bus_log.info("standing in", name=entry["name"], auto_reply=auto_reply)

    try:
        return _serve(client, address, entry, home, log, auto_reply, once,
                      outbound_poll, inbound_poll, expires_at)
    finally:
        # Not on the `once` path. That is a single pass of the duties driven by
        # a caller that did its own `_join` and is still using the listener
        # afterwards; leaving there would tear down something we did not put
        # up. A bridge that stops *serving* is the one that has to let go.
        if not once and agents.leave(entry["name"], home=home):
            log(f"[bridge] {entry['name']} left the bus")
            bus_log.info("left the bus", name=entry["name"])


def _serve(client, address, entry, home, log, auto_reply, once,
           outbound_poll, inbound_poll, expires_at) -> int:
    """The loop itself, so `bridge` can own the leaving."""
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
        for msg in messages.inbox(name=me["name"], unread_only=True, home=home):
            last_traffic = time.monotonic()
            try:
                _forward_one(client, address, me, msg, home, log, auto_reply)
            except Exception as e:  # noqa: BLE001  # client.push is a Protocol implementation
                # Left unread on purpose: the next pass retries it.
                log(f"[bridge] could not forward {msg.get('id')}: {e}")
                bus_log.warn("could not forward", message_id=msg.get("id"), error=str(e))

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
            for r in replies:
                if not _deliver_reply(me, r, home, log):
                    continue
                rid = r.get("id")
                if not rid:
                    continue
                try:
                    client.ack(address, [rid])
                except Exception as e:  # noqa: BLE001  # client.ack is a Protocol implementation
                    # Delivered but unacked: the next poll hands it back and we
                    # deliver twice. At-least-once, which is the right direction.
                    log(f"[bridge] delivered {rid} but could not ack it: {e}")
                    bus_log.warn("delivered but could not ack", message_id=rid, error=str(e))

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
            detail = ""
            with contextlib.suppress(Exception):
                detail = (json.loads(e.read() or b"{}") or {}).get("detail", "")
            raise RuntimeError(f"cloud refused {op}: HTTP {e.code} {detail}".strip()) from e

    def push(self, address: str, message: dict[str, Any]) -> str:
        return self._call("push", message=message).get("id", "")

    def pull(self, address: str) -> list[dict[str, Any]]:
        return self._call("pull").get("messages") or []

    def ack(self, address: str, ids: list[str]) -> None:
        self._call("ack", ids=list(ids))

    def publish_roster(self, address: str, agents: list[dict[str, Any]]) -> None:
        self._call("roster", agents=list(agents))


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


def token_source(home: str | None = None) -> str:
    """`keychain`, `file` or `none` -- which one a bridge starting now would use.

    Worth saying out loud at startup. Two places can hold a token, one of them
    is invisible in a directory listing, and "which of these is live" is the
    first question anyone debugging a 401 has.
    """
    from agent_bus.paths import get_home

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
    """`(url, token)` from the Keychain, else `<home>/cloud-token`, else None.

    Absent is the ordinary case, not an error: a bridge with no token spools to
    disk instead, which is visible rather than silently dropped.

    **The Keychain wins.** It is where the credential is meant to live, and a
    stale file left behind after moving it there would otherwise keep being
    used -- silently, and for as long as it stayed valid. The file remains the
    fallback because not every machine that runs this is a Mac, and a service
    that starts before the Keychain unlocks still has to start.

    **The URL comes out of the token's own `iss` claim.** One artifact to
    install, and it cannot drift from a URL configured beside it. The claim is
    read without verifying the signature -- deliberately: this is the user's own
    0600 config file, not network input, and anyone who can rewrite it has
    already won. The server still verifies; a token naming the wrong issuer
    fails at connect, loudly, rather than being quietly trusted.
    """
    from agent_bus.paths import get_home

    path = os.path.join(home or get_home(), "cloud-token")
    token = _keychain_token()
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

    def _dir(self, address: str, leaf: str) -> str:
        d = os.path.join(self.root, address, leaf)
        os.makedirs(d, exist_ok=True)
        return d

    def push(self, address: str, message: dict[str, Any]) -> str:
        path = os.path.join(self._dir(address, "outbound"), f"{message['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(message, f, indent=2)
        return message["id"]

    def pull(self, address: str) -> list[dict[str, Any]]:
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

    def ack(self, address: str, ids: list[str]) -> None:
        d = self._dir(address, "inbound")
        for i in ids:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(d, f"{i}.json"))

    def publish_roster(self, address: str, agents: list[dict[str, Any]]) -> None:
        with open(os.path.join(self._dir(address, ""), "roster.json"), "w", encoding="utf-8") as f:
            json.dump(agents, f, indent=2)
