"""The default channel: a JSONL inbox under the bus home.

Not a vendor adapter -- it is what an agent gets when its harness has no
native way in. grok and omp both read it (via `agent-bus inbox`, or `watch`
wired into a monitor), and so does any harness we have never heard of, which
is why it has no KIND and is never registered in the transport table.
"""

from __future__ import annotations

from typing import Any

from ... import store

NAME = "filebus"


def send(
    entry: dict[str, Any],
    text: str,
    summary: str = "",
    from_name: str | None = None,
    home: str | None = None,
) -> dict[str, Any]:
    mid = store.send_message(
        to=entry["id"], text=text, summary=summary, from_name=from_name, home=home
    )
    return {"transport": NAME, "id": mid, "to": entry["name"]}
