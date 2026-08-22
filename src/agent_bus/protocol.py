"""Protocol types for agent-bus.

All messages are plain text only. No structured beyond the envelope.
"""
from __future__ import annotations

import dataclasses
import datetime
import uuid
from typing import Any, Literal, TypedDict

Kind = Literal["claude", "grok", "omp", "codex", "other"]


@dataclasses.dataclass
class AgentRef:
    id: str
    name: str
    kind: Kind


@dataclasses.dataclass
class RosterEntry:
    id: str  # uuid for registered, or stable "kind:xxx" for discovered
    name: str
    kind: Kind
    pid: int | None
    cwd: str | None
    status: Literal["idle", "busy", "waiting", "unknown"]
    inbox: str  # e.g. "file:~/.agent-bus/inboxes/<id>.jsonl"
    native: dict[str, Any]  # adapter specific, e.g. sessionId, messagingSocketPath etc.
    registeredAt: str
    updatedAt: str


class Message(TypedDict):
    id: str
    ts: str
    from_: AgentRef  # "from" is keyword, use from_ in py, serialize as "from"
    to: dict[str, str]  # {"id": , "name": }
    summary: str
    text: str
    replyTo: str | None
    read: bool


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def make_agent_ref(id: str, name: str, kind: Kind) -> AgentRef:
    return AgentRef(id=id, name=name, kind=kind)


def roster_to_dict(r: RosterEntry) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "pid": r.pid,
        "cwd": r.cwd,
        "status": r.status,
        "inbox": r.inbox,
        "native": r.native,
        "registeredAt": r.registeredAt,
        "updatedAt": r.updatedAt,
    }


def dict_to_roster(d: dict[str, Any]) -> RosterEntry:
    return RosterEntry(
        id=d["id"],
        name=d["name"],
        kind=d["kind"],
        pid=d.get("pid"),
        cwd=d.get("cwd"),
        status=d.get("status", "unknown"),
        inbox=d["inbox"],
        native=d.get("native", {}),
        registeredAt=d["registeredAt"],
        updatedAt=d["updatedAt"],
    )


def message_to_json(m: Message) -> dict[str, Any]:
    return {
        "id": m["id"],
        "ts": m["ts"],
        "from": {  # serialized key
            "id": m["from_"].id,
            "name": m["from_"].name,
            "kind": m["from_"].kind,
        },
        "to": m["to"],
        "summary": m["summary"],
        "text": m["text"],
        "replyTo": m["replyTo"],
        "read": m["read"],
    }


def json_to_message(d: dict[str, Any]) -> Message:
    from_ref = d.get("from", {})
    return {
        "id": d["id"],
        "ts": d["ts"],
        "from_": make_agent_ref(
            from_ref.get("id", ""), from_ref.get("name", ""), from_ref.get("kind", "other")
        ),
        "to": d.get("to", {}),
        "summary": d.get("summary", ""),
        "text": d["text"],
        "replyTo": d.get("replyTo"),
        "read": d.get("read", False),
    }
