"""The bridge: a team secretary for peers that cannot be reached directly.

Claude Desktop and ChatGPT are reachable only over public HTTPS, never wake on
their own, and are prodded by a human. A bridge process stands in for one of
them locally, one process per provider, and does what a secretary does:

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

import contextlib
import json
import os
import time
from typing import Any, Protocol

from .commands import agents, messages
from .listener import start_uds_listen

# Providers a bridge can stand in for. One long-running chat per provider talks
# to the coding team -- there is deliberately no conversation dimension, so
# there is deliberately no more than one bridge per provider.
PROVIDERS: tuple[str, ...] = ("claude", "chatgpt")

DISPLAY = {"claude": "Claude Desktop", "chatgpt": "ChatGPT"}

# Outbound is a local file inbox and wants to feel immediate; inbound is a
# billed network call against a peer that is hard-asynchronous by definition.
# Polling the cloud at the outbound cadence would spend money to learn nothing.
OUTBOUND_POLL_SECONDS = 1.0
INBOUND_POLL_SECONDS = 30.0
ROSTER_PUBLISH_SECONDS = 30.0


class CloudClient(Protocol):
    """The four frozen operations, and nothing else.

    Named as a protocol rather than a base class so the bridge can be tested
    without a network, and so the public contract stays something we state
    explicitly rather than something that emerges from an implementation.
    """

    def push(self, provider: str, message: dict[str, Any]) -> str: ...

    def pull(self, provider: str) -> list[dict[str, Any]]: ...

    def ack(self, provider: str, ids: list[str]) -> None: ...

    def publish_roster(self, provider: str, agents: list[dict[str, Any]]) -> None: ...


def bridge_name(provider: str) -> str:
    return f"desktop-{provider}"


def receipt_for(provider: str) -> str:
    """The one-line receipt sent back to whoever wrote in.

    Terse on purpose: it is an FYI, not a conversation, and it is marked
    automated so the sender does not reply to it. It says the two things a
    uniform "delivered" would conflate -- that the hand-off succeeded, and that
    the actual reader has not seen it yet.
    """
    who = DISPLAY.get(provider, provider)
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


def _join(provider: str, home: str | None) -> dict[str, Any]:
    """Join the bus the way a coding harness session does.

    Two steps, because that is what lifecycle.session_start does for every
    non-Claude kind: claim a name, then publish a listener. The second is not
    optional decoration -- it is what puts the bridge in Claude's *native*
    ListAgents, so "send this to Claude Desktop" is a plain SendMessage rather
    than something Claude has to be taught to do through a CLI. pi proves the
    shape: no MCP server at all, and it still messages Claude, because `listen`
    publishes the Claude-shaped session and socket.

    It is also what gives the bridge a socket of its own to reply *from*. An
    outbound frame carries the sender's socket as its reply address, so without
    one the receipt could not go back to a Claude peer at all.
    """
    entry = agents.register(
        bridge_name(provider),
        "desktop",
        pid=os.getpid(),
        home=home,
        aliases=[f"desktop:{provider}"],
    )
    start_uds_listen(entry["name"], os.getpid(), home=home)
    return entry


def _forward_one(client: CloudClient, provider: str, entry: Any, msg: dict[str, Any],
                 home: str | None, log: Any, auto_reply: bool) -> None:
    """Push one message, then acknowledge it locally.

    Push-then-ack, deliberately. A crash between the two redelivers rather than
    loses, which is the right direction to fail for a courier: the cloud `write`
    carries the local message id as a dedupe key, so the duplicate is absorbed
    there instead of surfacing twice in someone's chat.
    """
    client.push(provider, {
        "id": msg["id"],
        "from": sender_name(msg) or "unknown",
        "summary": msg.get("summary") or "",
        "text": msg.get("text") or "",
        "ts": msg.get("ts"),
    })
    messages.ack(msg["id"], name=entry["name"], home=home)
    if auto_reply:
        _send_receipt(provider, entry, msg, home, log)


def _send_receipt(provider: str, entry: Any, msg: dict[str, Any],
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
            text=receipt_for(provider),
            summary="auto-receipt",
            from_name=entry["name"],
            home=home,
        )
    except Exception as e:  # noqa: BLE001  # the router can raise anything; a receipt must never fail a delivery
        log(f"[bridge] receipt to {sender} not delivered: {e}")


def _deliver_reply(entry: Any, reply: dict[str, Any], home: str | None, log: Any) -> bool:
    """Hand an inbound reply to the router, not to the store.

    This distinction is load-bearing. A reply from Claude Desktop addressed to a
    *Claude Code* session, written straight into a file inbox, would sit unread
    forever -- Claude never polls one. Through the router it goes out over UDS,
    and the durable copy is written already-acked, which is the arrangement that
    dissolved the orphaned inboxes in the first place.
    """
    to = reply.get("to")
    if not to:
        log("[bridge] dropped a reply with no addressee")
        return True
    try:
        messages.send(
            to=to,
            text=reply.get("text") or "",
            summary=reply.get("summary") or "",
            from_name=entry["name"],
            home=home,
        )
        return True
    except Exception as e:  # noqa: BLE001  # the router can raise anything; a stale reply is worse than none
        # Log and drop. The recipient is gone or unroutable, and the message
        # would expire at TTL anyway -- a stale reply delivered late is the
        # thing the whole design exists to prevent.
        log(f"[bridge] dropped a reply for {to}: {e}")
        return True


def _roster_snapshot(entry: Any, home: str | None) -> list[dict[str, Any]]:
    """Who is in the office, for the desktop peer to check before writing.

    Published rather than queried, because nothing can reach into this machine.
    It carries the ordinary TTL, so a bridge that stops running stops refreshing
    it and the listing empties by itself -- bridge liveness needs no separate
    heartbeat.
    """
    return [
        {"name": a["name"], "kind": a["kind"], "id": str(a["id"])}
        for a in agents.list_agents(home=home)
        if a["id"] != entry["id"]
    ]


def bridge(
    provider: str,
    client: CloudClient,
    home: str | None = None,
    once: bool = False,
    log: Any = None,
    auto_reply: bool = False,
    outbound_poll: float = OUTBOUND_POLL_SECONDS,
    inbound_poll: float = INBOUND_POLL_SECONDS,
) -> int:
    """Run the secretary until interrupted.

    `once` runs a single pass of each duty, which is what the tests drive: a
    loop that can only be observed by waiting is a loop nobody checks.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider} (expected one of {', '.join(PROVIDERS)})")
    log = log or (lambda line: print(line, flush=True))

    entry = _join(provider, home)
    log(f"[bridge] {entry['name']} standing in for {DISPLAY.get(provider, provider)}"
        f"{'; auto-reply on' if auto_reply else ''}")

    last_inbound = 0.0
    while True:
        for msg in messages.inbox(name=entry["name"], unread_only=True, home=home):
            try:
                _forward_one(client, provider, entry, msg, home, log, auto_reply)
            except Exception as e:  # noqa: BLE001  # client.push is a Protocol implementation
                # Left unread on purpose: the next pass retries it.
                log(f"[bridge] could not forward {msg.get('id')}: {e}")

        now = time.monotonic()
        if once or now - last_inbound >= inbound_poll:
            last_inbound = now
            try:
                client.publish_roster(provider, _roster_snapshot(entry, home))
            except Exception as e:  # noqa: BLE001  # client.publish_roster is a Protocol implementation
                log(f"[bridge] roster not published: {e}")
            try:
                replies = client.pull(provider)
            except Exception as e:  # noqa: BLE001  # client.pull is a Protocol implementation
                log(f"[bridge] could not pull: {e}")
                replies = []
            done = [r["id"] for r in replies if _deliver_reply(entry, r, home, log) and r.get("id")]
            if done:
                try:
                    client.ack(provider, done)
                except Exception as e:  # noqa: BLE001  # client.ack is a Protocol implementation
                    log(f"[bridge] could not ack {len(done)} replies: {e}")

        if once:
            return 0
        time.sleep(outbound_poll)


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

    def _dir(self, provider: str, leaf: str) -> str:
        d = os.path.join(self.root, provider, leaf)
        os.makedirs(d, exist_ok=True)
        return d

    def push(self, provider: str, message: dict[str, Any]) -> str:
        path = os.path.join(self._dir(provider, "outbound"), f"{message['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(message, f, indent=2)
        return message["id"]

    def pull(self, provider: str) -> list[dict[str, Any]]:
        d = self._dir(provider, "inbound")
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

    def ack(self, provider: str, ids: list[str]) -> None:
        d = self._dir(provider, "inbound")
        for i in ids:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(d, f"{i}.json"))

    def publish_roster(self, provider: str, agents: list[dict[str, Any]]) -> None:
        with open(os.path.join(self._dir(provider, ""), "roster.json"), "w", encoding="utf-8") as f:
            json.dump(agents, f, indent=2)
