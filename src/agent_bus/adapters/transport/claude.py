"""Deliver to a Claude peer over its own UDS socket.

A Claude session never *polls* an inbox -- its harness delivers peer messages
into the conversation directly. It does have one: since #26 every peer does,
and commands.messages.send writes the durable copy already acked once this
transport returns without raising. What a Claude peer never has is a reason to
look in it.

So this transport raises rather than falling back to the file bus. A fallback
would put the message in a channel this peer does not read and then report
success for a delivery that never happened. The pre-acked copy is a record of
a delivery that did -- which is why an *unread* in a Claude inbox now means
something precise: the transport failed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ...paths import claude_sessions_dir
from ...process import is_pid_alive

KIND = "claude"
NAME = "claude-uds"


def socket_for(entry: dict[str, Any]) -> str | None:
    """Where to reach this peer.

    A discovered entry carries the socket in `native`; a roster entry has
    `native={}` and only a pid, so the session file is the fallback rather
    than the exception. Resolving from `native` alone silently failed for
    every registered peer.
    """
    sock = (entry.get("native") or {}).get("messagingSocketPath")
    if sock and os.path.exists(sock):
        return sock
    pid = entry.get("pid")
    if not pid or not is_pid_alive(int(pid)):
        return None
    path = os.path.join(claude_sessions_dir(), f"{int(pid)}.json")
    try:
        with open(path, encoding="utf-8") as f:
            sock = json.load(f).get("messagingSocketPath")
    except (OSError, json.JSONDecodeError):
        return None
    return sock if sock and os.path.exists(sock) else None


def resolve(target: str) -> dict[str, Any] | None:
    """Claude peers are always visible to discovery, so there is nothing to
    resolve here that the bus has not already found."""
    return None


def send(
    entry: dict[str, Any],
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    from ...uds import send_peer_message

    sock = socket_for(entry)
    if sock is None:
        raise ValueError(
            f"{entry.get('name')} is a claude peer with no reachable socket "
            "(session gone, or it never published one)"
        )
    if not send_peer_message(sock, text):
        raise ValueError(f"claude peer {entry.get('name')} refused the message")
    return {"transport": NAME, "to": entry.get("name"), "socket": sock}
