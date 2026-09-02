"""The MCP surface: one JSON-RPC message in, one response out.

Everything here that looks arbitrary was found by watching a real connector
fail, in the predecessor (`c2c-mcp`). Three of them cost a bring-up each:

**Discovery is anonymous; only `tools/call` is gated.** ChatGPT's connector
pings `initialize`, `tools/list` and friends *before it ever attaches a token*,
and attaches `Authorization` only once a tool is actually invoked. Gating every
method uniformly made discovery itself 401, so **no tool was visible at all**,
whether or not OAuth had worked. Safe, because discovery exposes schemas and
never mailbox contents -- the reads and writes are all `tools/call`.

**`resources/list` and `prompts/list` must answer.** Some clients call them
unconditionally during discovery, not gated on the advertised capabilities. A
`Method not found` there did not mean "no resources"; it killed tool discovery
entirely. So both capabilities are declared and both methods return valid
empties, along with `resources/templates/list`.

Split out of `app.py` because it is the half with no sockets in it: `dispatch`
is pure, the store is injected, and almost everything worth testing about the
protocol is testable without binding a port.
"""

from __future__ import annotations

import logging
from typing import Any

import logs
from config import version
from contract import TOOLS
from store import INBOX, OUTBOX, Rejected, queue

log = logging.getLogger(logs.LOGGER_NAME)


PROTOCOL_VERSION = "2025-06-18"

# Exempt from the bearer check. Read-only schema and capability methods only;
# `tools/call` is deliberately absent, being the only one that touches a
# mailbox. Taken from the predecessor, where the set was arrived at by watching
# discovery 401 in its own logs.
DISCOVERY_METHODS = frozenset({
    "initialize",
    "notifications/initialized",
    "ping",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "prompts/list",
})


def _ok(mid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(body: str, **structured: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": body}]}
    if structured:
        out["structuredContent"] = structured
    return out


def call_tool(name: str, args: dict[str, Any], store: Any, kind: str,
              peer: str) -> dict[str, Any]:
    """The tools. `kind:peer` is who is calling, from the token, never args."""
    inbox, outbox = queue(kind, peer, INBOX), queue(kind, peer, OUTBOX)

    # Every tool call, not just the one that writes. Until this line the read
    # path -- get_inbox, read_message, ack_message, list_agents -- emitted
    # nothing, so "did the connector actually fetch that message?" could only
    # be answered by asking the person looking at the client. The request log
    # says a POST reached /mcp; it cannot say which tool ran.
    #
    # The tool name and the caller, never the arguments: `send_message` carries
    # the message body, and these logs exist to be read during a connector
    # mystery, which is exactly when they get pasted somewhere public. Same
    # reasoning as `LOGGED_HEADERS` in handler_base.py, which redacts the
    # request log the same way.
    log.info("tools/call", extra={"verb": "tools/call", "tool": name,
                                  "peer": f"{kind}:{peer}"})

    if name == "list_agents":
        agents = store.roster(f"{kind}:{peer}")
        if not agents:
            return _text(
                "Nobody is on the bus, or the bridge is not running. Its roster "
                "expires on its own, so an empty list means it stopped "
                "publishing rather than that the team went home.",
                agents=[],
            )
        lines = [f"- **{a['name']}** ({a.get('kind', '?')})" for a in agents]
        return _text(f"{len(agents)} on the bus:\n\n" + "\n".join(lines), agents=agents)

    if name == "get_inbox":
        msgs = store.read(inbox, unread_only=args.get("unread_only", True))
        if not msgs:
            return _text("Nothing waiting.", messages=[])
        lines = [f"- `{m['id']}` from **{m.get('from', '?')}**: {m.get('summary') or ''}"
                 for m in msgs]
        return _text(f"{len(msgs)} waiting:\n\n" + "\n".join(lines), messages=msgs)

    if name == "read_message":
        mid = args.get("message_id")
        if not isinstance(mid, str) or not mid:
            return _text("read_message needs the message_id get_inbox gave you.")
        msg = store.read_one(inbox, mid)
        if msg is None:
            return _text(f"No message `{mid}`. Ids expire with the message, and "
                         f"only ids from your own inbox resolve.", message=None)
        # The listing carries summaries; this is the one place the body is
        # rendered, so it goes in the text block rather than structured content
        # alone -- a connector that reads only the prose still gets the message.
        return _text(f"From **{msg.get('from', '?')}**"
                     + (f" -- {msg['summary']}" if msg.get("summary") else "")
                     + f"\n\n{msg.get('text', '')}", message=msg)

    if name == "ack_message":
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            return _text("ack_message needs a list of ids. There is no 'everything' mode.")
        return _text(f"Acked {store.ack(inbox, ids)} of {len(ids)}.")

    if name == "send_message":
        try:
            mid = store.write(outbox, {
                "to": args.get("to"),
                "text": args.get("text"),
                "summary": args.get("summary") or "",
                "from": args.get("from"),
            })
        except Rejected as e:
            return _text(f"Refused: {e}")
        # The id this mints is the one the bridge carries down to the local
        # bus, so it is where a connector's journey starts.
        log.info("connector write", extra={"trace_id": mid, "to": args.get("to")})
        return _text(f"Queued as `{mid}`. It reaches the team when the bridge "
                     f"next polls; nobody has read it yet.")

    return _text(f"No such tool: {name}")


def dispatch(msg: dict[str, Any], store: Any, kind: str, peer: str,
             authed: bool) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out. None means "notification".

    Pure: no sockets, no globals. The HTTP layer above is thin enough that
    almost everything worth testing is testable here, and the store is injected
    so the dispatch tests need no emulator.
    """
    method = msg.get("method")
    mid = msg.get("id")

    if method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _ok(mid, {
            "protocolVersion": PROTOCOL_VERSION,
            # resources and prompts are declared despite having none of either.
            # See the module docstring: not declaring them is what killed tool
            # discovery in the predecessor.
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "agent-bus-cloud", "version": version()},
        })
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": list(TOOLS)})
    if method == "resources/list":
        return _ok(mid, {"resources": []})
    if method == "resources/templates/list":
        return _ok(mid, {"resourceTemplates": []})
    if method == "prompts/list":
        return _ok(mid, {"prompts": []})

    if method == "tools/call":
        if not authed:
            return err(mid, -32001, "unauthenticated")
        params = msg.get("params") or {}
        return _ok(mid, call_tool(params.get("name") or "",
                                  params.get("arguments") or {},
                                  store, kind, peer))

    return err(mid, -32601, f"method not found: {method}")
