"""Sending, reading and acknowledging messages.

Sending is where the bus earns its name: the caller names an agent, and
agent-bus works out what that agent is and how to reach it. It used to be the
caller's problem -- `send` wrote a file inbox, `send-peer` spoke UDS, and
`send-codex` spawned a codex app-server, so you had to know a target's harness
before you could pick the command. Three commands for one verb, and the
harness detail the bus exists to hide leaked into every caller.
"""

from __future__ import annotations

from typing import Any

from .. import store
from ..adapters import transport
from ..protocol import message_to_json, roster_to_dict


def send(
    to: str,
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    """Deliver to `to` over whatever channel that agent actually reads.

    The reply says which transport carried it, because "sent" means something
    different per channel: the file bus persists a message the recipient will
    read when it next looks, codex persists to its own queue, and a Claude
    peer is handed the message live by its harness.
    """
    entry = store.resolve_target(to, home)
    if entry is not None:
        adapter = transport.for_kind(entry.kind)
        payload = roster_to_dict(entry)
        if adapter is not None:
            result = adapter.send(payload, text, summary, from_name=from_name, home=home)
            _keep_a_delivered_copy(entry, text, summary, from_name, home)
            return result
        return transport.filebus.send(payload, text, summary, from_name=from_name, home=home)

    # Nothing on the bus answers to that name. Before calling it unknown, ask
    # the transports that can address their own namespace -- a codex thread is
    # reachable but is never a roster entry, because codex records no pid or
    # socket to build one from.
    native = transport.resolve_unknown(to)
    if native is None:
        raise ValueError(f"no such agent: {to}")
    adapter, payload = native
    return adapter.send(payload, text, summary, from_name=from_name, home=home)


def _keep_a_delivered_copy(
    entry: Any,
    text: str,
    summary: str,
    from_name: str | None,
    home: str | None,
) -> None:
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
    try:
        store.send_message(
            to=entry.id,
            text=text,
            summary=summary,
            from_name=from_name,
            home=home,
            read=True,
        )
    except Exception:
        pass


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


def ack(message_id: str, name: str | None = None, home: str | None = None) -> dict[str, Any]:
    return {"acked": bool(store.ack_message(message_id, name_or_id=name, home=home))}
