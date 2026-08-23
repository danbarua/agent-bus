"""Sending, reading and acknowledging file-bus messages."""

from __future__ import annotations

from typing import Any

from .. import store
from ..protocol import message_to_json


def send(
    to: str,
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    return {"id": store.send_message(to=to, text=text, summary=summary,
                                     from_name=from_name, home=home)}


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
