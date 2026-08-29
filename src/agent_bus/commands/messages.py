"""Sending, reading and acknowledging messages.

Sending is where the bus earns its name: the caller names an agent, and
agent-bus works out what that agent is and how to reach it. It used to be the
caller's problem -- `send` wrote a file inbox, `send-peer` spoke UDS, and
`send-codex` spawned a codex app-server, so you had to know a target's harness
before you could pick the command. Three commands for one verb, and the
harness detail the bus exists to hide leaked into every caller.
"""

from __future__ import annotations

import contextlib
from typing import Any

from .. import store
from ..adapters import addressing, transport
from ..log import logged
from ..protocol import delivery_expectation, message_to_json, roster_to_dict


@logged
def send(
    to: str,
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Deliver to `to` over whatever channel that agent actually reads.

    The reply says which transport carried it, because "sent" means something
    different per channel: the file bus persists a message the recipient will
    read when it next looks, codex persists to its own queue, and a Claude
    peer is handed the message live by its harness.

    `message_id` carries an identity the message already has -- the bridge
    passes the id the cloud gave it, so one identifier spans the whole journey
    instead of one per hop. It reaches the durable copy only: an adapter hands
    a peer text, and there is nowhere in a conversation to put an id.
    """
    entry = store.resolve_target(to, home)
    if entry is not None:
        _refuse_if_not_live(to, entry)
        adapter = transport.for_kind(entry.kind)
        # roster_to_dict, not roster_to_public: an adapter is inside the bus and
        # needs the parts a caller must never see -- native, pid, inbox path.
        payload = roster_to_dict(entry)
        if adapter is not None:
            adapter.send(payload, text, summary, from_name=from_name, home=home)
            mid = _keep_a_delivered_copy(entry, text, summary, from_name, home, message_id)
        else:
            mid = transport.filebus.send(
                payload, text, summary, from_name=from_name, home=home,
                message_id=message_id).get("id")
        return _sent(entry.name, entry.kind, mid)

    # Nothing on the bus answers to that name. Before calling it unknown, ask
    # the transports that can address their own namespace -- a codex thread is
    # reachable but is never a roster entry, because codex records no pid or
    # socket to build one from.
    native = transport.resolve_unknown(to)
    if native is None:
        raise ValueError(f"no such agent: {to}")
    adapter, payload = native
    adapter.send(payload, text, summary, from_name=from_name, home=home)
    return _sent(payload.get("name") or to, payload.get("kind"))


def _sent(name: str, kind: str | None, message_id: str | None = None) -> dict[str, Any]:
    """What the sender is told.

    The adapters return which channel carried it, and the Claude one returns
    the socket path it used. That went straight back to the caller, so an agent
    asking to send a message was handed a filesystem path into another
    process's plumbing -- and the name of a transport it can do nothing with.

    A sender has one real question beyond "did it go": can I wait for an
    answer. `delivery` says so. "now" is a peer with a loop of its own; "queued"
    is one where a human has to prod it before anything happens.

    **`id` came back, and the other two did not.** Removing all three was one
    change with two reasons, and neither of them covers the id: it is not a
    path into another process, and it is not a name for a mechanism the caller
    cannot use. It is the identifier `ack_message` already takes and `get_inbox`
    already returns -- public on the receiving side, so withholding it from the
    sender was asymmetric rather than principled. Without it a sender cannot
    quote, follow up, or match an ack, and the logs on either side of a bridge
    have nothing to join on (#108).
    """
    out = {"to": name, "delivery": delivery_expectation(kind)}
    if message_id:
        # The id was in hand from the transport and discarded, so a sender
        # could not reference the message it had just sent -- could not quote
        # it, follow it up, or match it against an ack. It is also the
        # correlation id the logs join on; see docs/structured-logging.md.
        out["id"] = message_id
    return out


def _refuse_if_not_live(to: str, entry: Any) -> None:
    """Refuse to deliver to a receiver that is not there.

    The store is deliberately more permissive: an entry is retained after its
    process exits so that queued mail stays *readable*, because deleting it took
    the mailbox with it and a reply to an agent that had just exited failed with
    "no such agent" (tests/agent_bus/test_presence_vs_mailbox.py). That retention is about
    reading, and it is untouched -- has_mailbox is never consulted on read, and
    mail already on disk stays available.

    Writing is a different question, and the old answer was wrong. Sending to a
    dead peer *succeeded*, filing a message into an inbox nothing would ever
    drain: the sender was told it worked, and with a 1h TTL the message then
    expired unread with no error anywhere. "Receiver unavailable" is both true
    and more useful than a silent success -- the send did not happen.

    Each space answers for itself, so this needs no special-casing. A Codex
    thread stays addressable while nothing runs (`thread.is_live` is True, and
    that is deliberate -- it is addressable *because* nothing is running), and a
    desktop bridge is live exactly while its process is up.
    """
    if addressing.is_live(entry):
        return
    raise ValueError(
        f"receiver unavailable: {to} is registered as a {entry.kind} peer but "
        "its process is not running, so nothing would read this. Not sent. "
        "(Mail already in its inbox stays readable.)"
    )


def _keep_a_delivered_copy(
    entry: Any,
    text: str,
    summary: str,
    from_name: str | None,
    home: str | None,
    message_id: str | None = None,
) -> str | None:
    """Record a natively-delivered message in the peer's inbox, already read.

    Reaching here means the adapter returned without raising, and that is the
    only success signal there is -- transport/claude.py turns a refusal into a
    ValueError, so nothing above the adapter boundary carries a boolean.

    Written pre-acked because the peer does not poll this inbox: its own harness
    has taken delivery -- into the conversation for Claude, into its own queue
    for Codex. The copy exists so every peer is on one code path and so a failed
    delivery is distinguishable -- a message stays *unread* only when the
    transport raised and we never got here.

    Only kinds with a native transport reach this. A `desktop` peer has none:
    its bridge is an ordinary bus peer that reads a file inbox, so mail for it
    takes the filebus path above and stays *unread* until the bridge drains it.
    "Delivered elsewhere" and "waiting here" are different states, and keeping
    them different is why this function must not grow a branch for desktop.

    Failure is swallowed on purpose. The message has been delivered; turning a
    bookkeeping problem into a reported send failure would be a lie in the
    direction that costs most.
    """
    with contextlib.suppress(Exception):
        return store.send_message(
            to=entry.id,
            text=text,
            summary=summary,
            from_name=from_name,
            home=home,
            read=True,
            message_id=message_id,
        )
    return None


@logged
def inbox(
    name: str | None = None,
    unread_only: bool = False,
    home: str | None = None,
) -> list[dict[str, Any]]:
    """Messages addressed to `name`, or to whoever we are.

    Serialized by protocol.message_to_json -- the same function that wrote them
    to disk. Both edges used to reimplement it by hand, identically, three
    copies of one wire format away from the definition of it.
    """
    msgs = store.get_inbox(name_or_id=name, unread_only=unread_only, home=home)
    return [message_to_json(m) for m in msgs]


@logged
def read_one(message_id: str, name: str | None = None,
             home: str | None = None) -> dict[str, Any] | None:
    """One message, whole. None if nothing matches that reference.

    The summary is the envelope's short form and the notice carries it; this is
    for the rest. Nothing here truncates -- a body is capped at MAX_TEXT when
    it is sent, which is what makes that safe.
    """
    msgs = inbox(name=name, unread_only=False, home=home)
    full = store.resolve_message_id(msgs, message_id)
    if full is None:
        return None
    return next((m for m in msgs if m["id"] == full), None)


def ack(message_id: str, name: str | None = None, home: str | None = None) -> dict[str, Any]:
    return {"acked": bool(store.ack_message(message_id, name_or_id=name, home=home))}
