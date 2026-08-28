"""The default channel: a JSONL inbox under the bus home.

Not a vendor adapter -- it is what an agent gets when its harness has no
native way in. grok and omp both read it (via `agent-bus inbox`, or `watch`
wired into a monitor), and so does any harness we have never heard of, which
is why it has no KIND and is never registered in the transport table.
"""

from __future__ import annotations

from typing import Any

NAME = "filebus"


def send(
    entry: dict[str, Any],
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    # Imported here, not at module scope: store now consults adapters.addressing
    # on every listing, so a top-level import would make store -> adapters ->
    # transport -> filebus -> store a cycle. A transport needing the store is
    # the odd edge in that loop, so it is the one that gives way.
    from ... import store

    mid = store.send_message(
        to=entry["id"], text=text, summary=summary, from_name=from_name, home=home,
        message_id=message_id,
    )
    return {"transport": NAME, "id": mid, "to": entry["name"]}
