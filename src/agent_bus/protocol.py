"""Protocol types for agent-bus.

All messages are plain text only. No structured beyond the envelope.
"""
from __future__ import annotations

import dataclasses
import datetime
import uuid
from typing import Any, Literal, NewType, TypedDict

# A harness name. Deliberately an open string, not a closed enum: the point of
# this bus is that a harness we have never heard of can join it, and a closed
# Literal meant an unknown one could not even name itself -- `register --kind`
# rejected it outright. KNOWN_KINDS is a hint for help text and completion, not
# a validation list. "other" remains the conventional fallback for a harness
# that cannot identify itself.
Kind = str

# Anything find_entry() resolves: a roster id, a name, an alias, or a former
# name still inside its grace window (#148) -- what a caller types to reach
# an agent, before resolution. A NewType, not a bare alias like Kind above:
# every construction site is then a real "I am asserting this string names
# an agent" moment, not an incidental str that happens to type-check.
#
# Not called AgentRef -- protocol.AgentRef already exists below, for the
# message envelope's from/to shape (id+name+kind, post-resolution). This is
# the pre-resolution query string; find_entry() is what turns one into the
# other, and "target" already names this concept on the CLI (send's
# positional) and throughout commands/messages.py.
AgentTarget = NewType("AgentTarget", str)

# A resolved RosterEntry.id -- what an inbox file is actually keyed by
# (_inbox_path_for, _roster_path). Outlives the agent: a dead entry with
# unread mail is still addressable by this id long after its AgentTarget
# (a name, say) may have been reused by someone else, which is why this is
# a distinct type rather than the same string before and after find_entry().
MailboxRef = NewType("MailboxRef", str)

# A message id. The same string on both sides of the bridge: `_wire` sends
# `msg["id"]` and the cloud store uses it as the Firestore document id, so one
# value addresses a message locally and remotely. That is the whole reason
# `trace_id` can join two log stores (docs/structured-logging.md), and a bare
# `str` said none of it.
MessageId = NewType("MessageId", str)

# `<kind>:<name>` -- the address a bridge holds, e.g. `desktop:claude`.
#
# Distinct from AgentTarget because they are different strings for the same
# peer and mixing them silently routes nowhere: `desktop:claude` is the
# address, `desktop-claude` is the name the bridge registers on the bus, and
# `bridge_name()` is the one-way transform between them. Both were `str`, so
# passing the wrong one type-checked -- which is exactly what a reader of
# `send_message(to="desktop-claude", ...)` could not tell.
#
# Named for the holder, not the peer, because the invariant belongs to the
# holder: one bridge per address, ever. A second one registers cleanly as
# `desktop-claude-2`, competes for the address, and delivery then depends on
# which entry sorts first -- so "who may hold this" is the fact worth carrying
# in the name.
BridgeAddress = NewType("BridgeAddress", str)

KNOWN_KINDS: tuple[str, ...] = ("claude", "grok", "omp", "codex", "desktop", "other")
FALLBACK_KIND = "other"

# `other` and `pending` are two different facts that shared one word.
#
#   other        there IS an agent here, it is addressable, and we have no
#                discovery adapter that can name what it is. pi is the standing
#                example. This is a settled, positive answer -- an agent need
#                never identify its kind to work -- so nothing may treat it as
#                a gap to be filled in later.
#
#   pending  nobody has connected and identified themselves YET. The MCP
#                server registers before any client says hello, and it has
#                nothing to go on: a harness passes its MCP child no
#                identifying environment at all (probed -- codex hands over
#                nine generic vars). This one IS expected to be replaced.
#
# Telling them apart is not cosmetic. The initialize handshake upgrades a peer
# only from the unclaimed state, and while that state was spelled `other` the
# guard could overwrite a settled `other` -- a pi peer that ran the MCP server
# would have had its correct kind taken off it.
#
# Not in KNOWN_KINDS: it is not a kind an agent may claim, it is what the bus
# says about an agent that has not spoken yet.
PENDING_KIND = "pending"

# `desktop` is the one kind added by decision rather than discovered: Claude
# Desktop and ChatGPT, reachable only over public HTTPS via a bridge process.
# Adding to KNOWN_KINDS is a product decision, not a defect repair, which is why
# it is recorded rather than inferred -- see docs/running-the-bridge.md.

# When a message to this kind can be expected to be read.
NOW = "now"
QUEUED = "queued"

# Kinds with no loop of their own. A desktop peer never wakes: no `watch`, no
# native delivery, nothing that inserts a message into its context when it
# finishes a turn. The user types "you've got mail". That is the mechanism and
# it is not going to improve.
HUMAN_PRODDED_KINDS: tuple[str, ...] = ("desktop",)


def delivery_expectation(kind: str | None) -> str:
    """When a message to this kind can be expected to be read.

    A property of the *kind*, not of the address space. The design doc put it on
    the space, but a desktop bridge registers in the `bus` space like any other
    process, so a space-keyed rule would answer "now" for the one peer class
    that means the opposite. Kind is also the truer predicate: `desktop` means a
    human is in the loop however the peer happens to be addressed.

    Read by the inbound auto-reply, which must state the real expectation rather
    than a uniform hedge -- "MAY respond, not guaranteed" is wrong for a peer
    that answers in three seconds, and being wrong in the reassuring direction
    is how a notice gets trained out of being read.

    "now" is when the recipient *can* read it, not proof that it did. For a
    claude-kind target there is no wire-level delivery receipt to confirm
    with -- measured directly against real Claude Code 2.1.239 and 2.1.251,
    a session never sends one back for an inbound cross-session-message (#157).
    "now" only means the message was handed to a transport with a live loop,
    the same thing it has always meant; it was never a confirmation and still
    is not one.
    """
    return QUEUED if normalize_kind(kind) in HUMAN_PRODDED_KINDS else NOW


def normalize_kind(value: str | None) -> str:
    """Lowercase and trim a kind. Anything non-empty is accepted."""
    if not value:
        return FALLBACK_KIND
    cleaned = value.strip().lower()
    return cleaned or FALLBACK_KIND


def resolve_kind_filter(value: str | None) -> str | None:
    """Resolve a *filter* over kinds. None means "every kind".

    Not the same function as normalize_kind, which resolves a kind an agent is
    claiming: there, an empty string has to become something, and "other" is
    the conventional answer. Here an absent filter means no filtering, so empty
    and "all" both mean None. Unknown kinds pass through rather than being
    dropped -- filtering by a harness we have not heard of should return
    nothing, not everything.
    """
    if value is None:
        return None
    cleaned = value.strip().lower()
    if not cleaned or cleaned == "all":
        return None
    return cleaned


@dataclasses.dataclass
class AgentRef:
    id: str
    name: str
    kind: Kind


@dataclasses.dataclass
class RosterEntry:
    id: MailboxRef  # uuid for registered, or stable "kind:xxx" for discovered
    name: str
    kind: Kind
    pid: int | None
    cwd: str | None
    status: Literal["idle", "busy", "waiting", "unknown"]
    inbox: str  # e.g. "file:~/.agent-bus/inboxes/<id>.jsonl"
    native: dict[str, Any]  # adapter specific, e.g. sessionId, messagingSocketPath etc.
    registeredAt: str
    updatedAt: str
    # Process start time, so a recycled pid cannot impersonate a dead agent.
    # Claude Code checks exactly this alongside the pid; optional because
    # entries written before this field existed have no value for it.
    procStart: str | None = None
    # Other addresses that denote this same agent. An agent registers under a
    # bus uuid and is separately *discovered* under its harness's own session
    # address; with nothing linking the two it appeared on the bus twice, under
    # two names. Aliases are what say they are one thing.
    aliases: list[str] = dataclasses.field(default_factory=list)
    # Names this entry answered to before a `register`-driven rename, most
    # recent first: [{"name": ..., "until": <iso, when it stopped being
    # current>}]. Matches Claude Code's own `formerNames` field and its
    # semantics (docs/harnesses/claude-code-presence.md#2) -- `until` is a
    # timestamp of the rename, not a stored deadline. Whether a name in here
    # still resolves is a read-time policy (store.FORMER_NAME_GRACE_SECONDS),
    # not something persisted per entry.
    formerNames: list[dict[str, Any]] = dataclasses.field(default_factory=list)


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
        # Without this the pid-reuse guard is inert: every disk round-trip
        # returned None and is_process_alive() degraded to a bare pid check.
        "procStart": r.procStart,
        "aliases": list(r.aliases),
        "formerNames": list(r.formerNames),
    }


def roster_to_public(r: RosterEntry) -> dict[str, Any]:
    """What an agent is told about another agent.

    Deliberately NOT roster_to_dict. That one is the storage round-trip --
    store.py writes it and dict_to_roster reads it back -- and it was also
    being returned straight to callers, so a caller asking who was on the bus
    got handed `inbox` (a path to a file on disk), `native` (harness internals,
    including another process's socket path) and `procStart` (the internal
    pid-reuse guard). None of that is theirs, and one of it is a filesystem
    path to somebody else's mailbox.

    What is left is what you need in order to write to them: an id and a name
    that address them, aliases that also do, and enough context -- kind, cwd,
    status -- to know which one you mean. `formerNames` also address this
    entry (#148), and are deliberately left out here rather than forgotten:
    advertising a decaying address invites a new sender to adopt a name that
    is about to stop working, the opposite of what the grace window is for.
    """
    return {
        "id": r.id,
        "name": r.name,
        "kind": r.kind,
        "pid": r.pid,
        "cwd": r.cwd,
        "status": r.status,
        "aliases": list(r.aliases),
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
        procStart=d.get("procStart"),
        aliases=list(d.get("aliases") or []),
        formerNames=list(d.get("formerNames") or []),
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
