"""Store and bus logic. Roster + inboxes under AGENT_BUS_HOME (default ~/.agent-bus).

All operations are best-effort. No network. Pid checks via os.kill.
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
from typing import Any

from .adapters import addressing
from .address import parse as parse_address

# Re-exported: get_home resolves a directory, so it lives in paths with its
# neighbours. Every caller in this module and beyond still reaches it as
# store.get_home, which is why moving it broke nothing.
from .paths import DEFAULT_HOME, get_home  # noqa: F401

# Re-exported: uds.py and the tests import these from store, which is still
# their vocabulary even though the implementation moved to a leaf in the
# adapters split. store itself no longer calls is_pid_alive -- liveness is the
# address space's rule now -- so the noqa is what stops a lint autofix deciding
# it is dead and breaking every importer.
from .process import is_pid_alive, is_process_alive, proc_start  # noqa: F401
from .protocol import (
    FALLBACK_KIND,
    Kind,
    Message,
    RosterEntry,
    dict_to_roster,
    json_to_message,
    make_agent_ref,
    message_to_json,
    new_id,
    now_iso,
    roster_to_dict,
)

# Sized to how the predecessor was actually used, not to a round number. Across
# 107 archived c2c/c2gpt messages the median was 3,730 chars and the largest
# 24,511 -- and only 1.9% of all that text sat inside code fences, so the tail
# is long-form reasoning rather than pasted files. 32,768 accepts every message
# ever observed with a third to spare, and is small enough that a real source
# file or diff fails, which is the moment the pointer discipline is worth
# teaching. See docs/durable-messaging-or-not.md.
MAX_TEXT = 32_768
MAX_UNREAD = 50

# Messages expire, and briefly. A six-hour-old message delivered because a
# bridge came back up is worse than one never delivered: the branch moved, the
# question was answered, and it arrives looking current. Fixed rather than
# configurable -- a knob invites someone to set it to a week and reintroduce
# exactly that. Expiry is derived from a message's `ts` rather than stored,
# because protocol.message_to_json is also the shape agents read back through
# MCP get_inbox, and a storage concern does not belong in it.
MESSAGE_TTL_SECONDS = 3600
REAP_AFTER_SECONDS = MESSAGE_TTL_SECONDS * 2


def _roster_dir(home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, "roster")


def _inbox_dir(home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, "inboxes")


def ensure_dirs(home: str | None = None) -> None:
    h = home or get_home()
    os.makedirs(_roster_dir(h), exist_ok=True)
    os.makedirs(_inbox_dir(h), exist_ok=True)


def _parent_pid(pid: int) -> int | None:
    if pid <= 1:
        return None
    if pid == os.getpid():
        pp = os.getppid()
        return pp if pp > 1 else None
    proc_stat = f"/proc/{pid}/stat"
    try:
        if os.path.exists(proc_stat):
            with open(proc_stat, encoding="utf-8") as f:
                body = f.read()
            close = body.rfind(")")
            if close != -1:
                fields = body[close + 2 :].split()
                if len(fields) >= 2:
                    return int(fields[1])
    except (OSError, ValueError):
        pass
    # Imported before the try, not inside it: the except clause below names
    # subprocess.SubprocessError, and a name bound inside the try is unbound if
    # the try is what failed.
    import subprocess

    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            pp = int(r.stdout.strip())
            return pp if pp > 1 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        # No ps, or output that is not a pid. Both mean "cannot tell", which is
        # what None says. Anything else is a bug and should surface.
        pass
    return None


def ancestor_pids(start: int | None = None) -> list[int]:
    pid = os.getpid() if start is None else start
    seen: set[int] = set()
    out: list[int] = []
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        out.append(pid)
        nxt = _parent_pid(pid)
        pid = nxt if nxt else 0
    return out


def _entry_for_current_process(home: str | None = None) -> RosterEntry | None:
    by_pid = {e.pid: e for e in get_live_roster(home) if e.pid}
    for pid in ancestor_pids():
        if pid in by_pid:
            return by_pid[pid]
    return None


def _safe_id_for_fs(s: str) -> str:
    """Map an id to a filename, injectively.

    It used to collapse every disallowed byte to "_", so `a/b`, `a b` and `a_b`
    all named the same file -- two distinct agents silently sharing a roster
    entry and an inbox. A hash suffix is appended only when a substitution
    actually happened, so every id already on disk keeps the exact filename it
    has today. Colons are allowed through, which is why
    `inboxes/claude:26bc255e-....jsonl` is a real path here; that makes these
    names illegal on Windows, where nothing else in this package runs either.

    Never reversed: load_roster reads the id from the JSON body, so this stays
    the single one-way id -> filename chokepoint.
    """
    sub = re.sub(r'[^A-Za-z0-9_.:-]', '_', s)
    if sub == s:
        return sub
    return f"{sub}.{hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]}"


def _inbox_path_for(entry_id: str, home: str | None = None) -> str:
    h = home or get_home()
    safe = _safe_id_for_fs(entry_id)
    return os.path.join(_inbox_dir(h), f"{safe}.jsonl")


def _roster_path(entry_id: str, home: str | None = None) -> str:
    h = home or get_home()
    safe = _safe_id_for_fs(entry_id)
    return os.path.join(_roster_dir(h), f"{safe}.json")


def load_roster(home: str | None = None) -> list[RosterEntry]:
    ensure_dirs(home)
    entries: list[RosterEntry] = []
    rdir = _roster_dir(home)
    if not os.path.isdir(rdir):
        return []
    for fn in os.listdir(rdir):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(rdir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            entries.append(dict_to_roster(data))
        except (OSError, ValueError, KeyError, TypeError):
            # Unreadable, not JSON, or not the shape dict_to_roster expects.
            # One bad file must not empty the roster.
            continue
    return entries


def save_roster_entry(entry: RosterEntry, home: str | None = None) -> None:
    ensure_dirs(home)
    path = _roster_path(entry.id, home)
    data = roster_to_dict(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def has_mail(entry_id: str, home: str | None = None) -> bool:
    """Undelivered mail, not "the file is non-empty".

    ack_message() rewrites a record with read: true rather than removing it, so
    file size never returns to zero. Testing size meant any agent that had ever
    received a message was retained forever -- which both contradicts the
    roster not growing without bound and leaves stale entries around for a
    recycled pid to collide with.
    """
    return _count_unread_lines(_inbox_path_for(entry_id, home)) > 0


def prune_dead_roster(home: str | None = None) -> int:
    """Drop presence for agents whose process is gone -- but never their mail.

    An entry is both a presence record and the only pointer to a mailbox.
    Deleting it on process exit used to throw the mailbox away with it, so a
    reply to an agent that had just exited failed with "no such agent" and
    anything already queued became unreachable. That is correct only if a peer
    is by definition a live socket, which is true of Claude and false of a
    Codex thread -- addressable precisely because nothing is running.

    So an entry with mail waiting is kept. Callers that want live agents filter
    on liveness; callers that want to deliver do not.
    """
    removed = 0
    for entry in load_roster(home):
        # The rule is the address space's, not a pid check. `not entry.pid` used
        # to mean "never prune", which combined with get_live_roster's pid
        # filter to make a pid-less entry permanently on disk AND permanently
        # invisible -- exactly what a registered Codex thread would have been.
        if addressing.is_live(entry):
            continue
        if has_mail(entry.id, home):
            continue  # gone, but with undelivered mail -- keep it addressable
        path = _roster_path(entry.id, home)
        try:
            if os.path.exists(path):
                os.unlink(path)
                removed += 1
        except OSError:
            pass
    return removed


def _make_inbox_ref(entry_id: str, home: str | None = None) -> str:
    return f"file:{_inbox_path_for(entry_id, home)}"


def register(
    name: str,
    kind: Kind,
    cwd: str | None = None,
    pid: int | None = None,
    home: str | None = None,
    aliases: list[str] | None = None,
    native: dict[str, Any] | None = None,
) -> RosterEntry:
    ensure_dirs(home)
    if pid is None:
        pid = os.getpid()
    if cwd is None:
        cwd = os.getcwd()
    if not name or not kind:
        raise ValueError("name and kind required")

    prune_dead_roster(home)

    # is_process_alive, not is_pid_alive: a recycled pid must not adopt a
    # retained dead entry, inheriting its id and reading its queued mail.
    live = [e for e in load_roster(home) if is_process_alive(e.pid, e.procStart)]
    for existing in live:
        if existing.pid == pid:
            other_live = [e for e in live if e.pid != pid]
            used_names = {e.name for e in other_live}
            final_name = name
            if name in used_names:
                i = 2
                while f"{name}-{i}" in used_names:
                    i += 1
                final_name = f"{name}-{i}"
            existing.name = final_name
            existing.kind = kind
            existing.cwd = cwd
            existing.updatedAt = now_iso()
            # Refresh, never inherit: persisting the previous holder's start
            # time onto a live registrant gives the entry a provably wrong
            # identity instead of a merely missing one.
            existing.procStart = proc_start(pid)
            if aliases:
                existing.aliases = sorted(set(existing.aliases) | set(aliases))
            if native:
                existing.native = {**existing.native, **native}
            save_roster_entry(existing, home)
            return existing
    used_names = {e.name for e in live}
    final_name = name
    if name in used_names:
        i = 2
        while f"{name}-{i}" in used_names:
            i += 1
        final_name = f"{name}-{i}"

    rid = new_id()
    now = now_iso()
    entry = RosterEntry(
        id=rid,
        name=final_name,
        kind=kind,
        pid=pid,
        cwd=cwd,
        status="idle",
        inbox=_make_inbox_ref(rid, home),
        native=dict(native or {}),
        registeredAt=now,
        updatedAt=now,
        # recorded at registration so a recycled pid cannot later impersonate us
        procStart=proc_start(pid),
        aliases=sorted(set(aliases or [])),
    )
    save_roster_entry(entry, home)
    return entry

def unregister(name: str | None = None, home: str | None = None) -> bool:
    ensure_dirs(home)
    if not name:
        return False
    removed = False
    for entry in load_roster(home):
        if entry.name == name:
            path = _roster_path(entry.id, home)
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    removed = True
                    # Stop at the first match. Names can repeat (a manual
                    # `register --name` alongside a hook-derived one); without
                    # this, one session ending wipes the other's entry too.
                    break
            except OSError:
                pass
    return removed


def unregister_by_pid(pid: int | None, home: str | None = None) -> bool:
    """Drop presence for a pid on clean shutdown -- keeping undelivered mail.

    This is the graceful SessionEnd path, and it has to honour the same rule as
    prune_dead_roster(). Deleting unconditionally here bypassed the retention
    entirely: an agent that exited cleanly with mail waiting still became
    unreachable, which is the exact failure the retention exists to prevent.
    A dead entry holding unread mail is marked not-live rather than removed.
    """
    if not pid:
        return False
    ensure_dirs(home)
    removed = False
    for entry in load_roster(home):
        if entry.pid != pid:
            continue
        if has_mail(entry.id, home):
            # keep it addressable; it is no longer live, which get_live_roster
            # already decides from the process rather than from this file
            continue
        path = _roster_path(entry.id, home)
        try:
            if os.path.exists(path):
                os.unlink(path)
                removed = True
        except OSError:
            pass
    return removed




def find_entry(name_or_id: str, home: str | None = None) -> RosterEntry | None:
    """Resolve for delivery. A dead agent with a mailbox is still addressable.

    Prefers a live match, so a restarted agent reusing a name wins over the
    stale entry it replaced.
    """
    prune_dead_roster(home)
    stale: RosterEntry | None = None
    for e in load_roster(home):
        if name_or_id in (e.id, e.name) or name_or_id in e.aliases:
            if addressing.is_live(e):
                return e
            if stale is None:
                stale = e
    return stale


def get_live_roster(home: str | None = None) -> list[RosterEntry]:
    """Only agents whose process is running -- what a presence view wants."""
    prune_dead_roster(home)
    return [e for e in load_roster(home) if addressing.is_live(e)]


def discover_agents(home: str | None = None) -> list[RosterEntry]:
    from .adapters import discover_all

    raw = discover_all()
    out: list[RosterEntry] = []
    seen_ids: set[str] = set()
    for d in raw:
        if d.get("id") in seen_ids:
            continue
        pid = d.get("pid")
        # The hard gate. A bare pid check here is what made any agent without a
        # process undiscoverable, no matter what its harness said about it.
        # Behaviour-identical for every adapter shipping today -- all of them
        # report live pids -- but the rule is now the address space's to state.
        if not addressing.is_live(d):
            continue
        rid = d.get("id") or new_id()
        now = now_iso()
        entry = RosterEntry(
            id=rid,
            name=d.get("name", "unknown"),
            kind=d.get("kind", "other"),
            pid=pid,
            cwd=d.get("cwd"),
            status=d.get("status", "unknown"),
            inbox=_make_inbox_ref(rid, home),
            native=d.get("native", {}),
            registeredAt=d.get("registeredAt", now),
            updatedAt=d.get("updatedAt", now),
        )
        out.append(entry)
        seen_ids.add(rid)
    return out
def _address_key(text: str, kind_hint: str | None = None) -> tuple[str | None, str, str]:
    """Identity of an address, independent of how it was spelled."""
    a = parse_address(text, kind_hint=kind_hint)
    return (a.kind, a.space, a.value)


def list_agents(
    kind: str | None = None, home: str | None = None
) -> list[RosterEntry]:
    roster = get_live_roster(home)
    discovered = discover_agents(home)

    by_id: dict[str, RosterEntry] = {e.id: e for e in roster}
    # A registered agent is also *discovered* by its harness, under a different
    # address: `agent-bus list` showed one Claude session twice, once as the
    # uuid it registered with and once as `claude:<sessionId>`, under two
    # different names. Merging only on id could never reconcile them, because
    # nothing said the two addresses denote the same thing.
    # Keyed on the parsed address, not its spelling: an alias is minted
    # canonically as `claude:session:<sid>` while discovery still emits the
    # legacy two-part `claude:<sid>`. Both denote the same address, and
    # comparing text would silently never match.
    aliased: dict[tuple[str | None, str, str], RosterEntry] = {
        _address_key(alias): e for e in roster for alias in e.aliases
    }
    # Retroactive for entries already on disk, which carry no aliases: the same
    # harness on the same live process is the same agent. Deliberately not
    # comparing procStart -- session files and `ps -o lstart=` write two
    # different formats into one field name, so it yields silent false
    # negatives.
    by_kind_pid: dict[tuple[str, int], RosterEntry] = {
        (e.kind, e.pid): e for e in roster if e.pid
    }

    for d in discovered:
        if d.id in by_id:
            continue
        held = aliased.get(_address_key(str(d.id), d.kind)) or (
            by_kind_pid.get((d.kind, d.pid)) if d.pid else None
        )
        if held is not None:
            # The roster entry is authoritative for identity -- it is the name
            # the agent claimed on the bus. The discovered record is
            # authoritative for what changes moment to moment.
            held.status = d.status
            if d.native:
                held.native = {**d.native, **held.native}
            continue
        by_id[d.id] = d

    agents = list(by_id.values())

    if kind and kind != "all":
        agents = [a for a in agents if a.kind == kind]

    agents.sort(key=lambda a: (a.kind, a.name, a.id))
    return agents


def _count_unread_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    count = 0
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not obj.get("read", False):
                        count += 1
                except (ValueError, AttributeError):
                    # A half-written line, or JSON that is not an object. The
                    # next append completes it; skipping is right.
                    pass
    except OSError:
        pass
    return count


def _read_all_messages(path: str) -> list[Message]:
    msgs: list[Message] = []
    if not os.path.exists(path):
        return msgs
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msgs.append(json_to_message(obj))
                except (ValueError, KeyError, TypeError):
                    # A torn line, or a record json_to_message cannot read.
                    continue
    except OSError:
        pass
    return msgs


def _age_seconds(msg: Message) -> float | None:
    """Seconds since a message was sent, or None if its `ts` is unreadable.

    Unreadable means *live*: every caller below treats None as not-expired.
    Deleting a message because we could not parse its timestamp would be the
    worst possible failure mode for a store whose whole job is delivery.
    """
    try:
        sent = datetime.datetime.fromisoformat(str(msg.get("ts")))
    except (TypeError, ValueError):
        return None
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=datetime.UTC)
    return (datetime.datetime.now(datetime.UTC) - sent).total_seconds()


def is_expired(msg: Message, ttl: float = MESSAGE_TTL_SECONDS) -> bool:
    age = _age_seconds(msg)
    return age is not None and age > ttl


def reap(home: str | None = None, older_than: float = REAP_AFTER_SECONDS) -> int:
    """Delete long-dead messages from every inbox. Returns how many went.

    Runs at 2x the TTL, not 1x, and the extra factor is what makes this safe to
    run at any moment: anything it removes was already invisible to every
    reader, because get_inbox filters at 1x. So there is no race to lose and no
    correctness burden here -- this is garbage collection, nothing more.

    It also mostly finds work only when no `watch` has been running, since a
    live watcher compacts at 1x as it goes. That is the case where nothing holds
    a file offset, which is precisely when rewriting is cheapest to get right.
    """
    ensure_dirs(home)
    removed = 0
    for path in glob.glob(os.path.join(_inbox_dir(home or get_home()), "*.jsonl")):
        removed += compact_inbox(path, older_than)
    return removed


def compact_inbox(path: str, older_than: float = MESSAGE_TTL_SECONDS) -> int:
    """Drop expired messages from one inbox file. Returns how many went.

    Rewrites the file, so it shrinks -- which invalidates any byte offset held
    over it. Only call this from a process that owns the offset (watch, for its
    own inbox) or when none is likely to be held (reap). Never from
    send_message: writes must only ever grow the file, or a live watcher's
    offset breaks under it.
    """
    msgs = _read_all_messages(path)
    if not msgs:
        return 0
    keep = [m for m in msgs if not is_expired(m, older_than)]
    if len(keep) == len(msgs):
        return 0
    _write_messages(path, keep)
    return len(msgs) - len(keep)


def _write_messages(path: str, msgs: list[Message]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(message_to_json(m)) + "\n" for m in msgs)
    os.replace(tmp, path)


def resolve_target(to: str, home: str | None = None) -> RosterEntry | None:
    """The entry a name or id addresses: roster first, then discovery.

    Extracted so the send router resolves a target exactly the way the file
    bus does. Routing on one resolution and delivering on another is how a
    message ends up in a channel the recipient does not read.
    """
    entry = find_entry(to, home)
    if entry is not None:
        return entry
    for d in discover_agents(home):
        if to in (d.id, d.name):
            return d
    return None


def send_message(
    to: str,
    text: str,
    summary: str = "",
    from_name: str | None = None,
    from_kind: Kind = "other",
    home: str | None = None,
    read: bool = False,
    message_id: str | None = None,
) -> str:
    """`message_id` is for mail that already HAS an identity elsewhere.

    A message the bridge carries in from the cloud was given an id there. Minting
    a second one here would mean nothing joins the two halves of its journey, and
    a redelivery -- which the bridge does on purpose, after a transport failure
    leaves the cloud copy unacked -- would arrive as a second, different message
    rather than the same one again.

    Locally-originated mail passes None and gets a fresh id, as before.
    """
    ensure_dirs(home)
    if len(text) > MAX_TEXT:
        raise ValueError(
            f"text too long: {len(text)} > {MAX_TEXT}. Send a pointer, not the "
            "file -- a path or URL the recipient can fetch. For a desktop peer "
            "it must be a public URL: it has no access to this filesystem, and "
            "a local path reads as exfiltration to a classifier."
        )

    target = resolve_target(to, home)
    if target is None:
        raise ValueError(_no_such_agent(to, home))

    # Refuse before writing, not after. Some addresses have no file inbox at
    # all: a Claude session is handed peer messages by its harness and never
    # polls one, a Codex thread is written to through thread/queue/add. Filing
    # a message for either produces an unread nobody can ever clear -- which is
    # how four inboxes on this machine were orphaned holding seven real
    # messages. The guard lives here rather than in the send command so that
    # every caller is covered: MCP, the watch loop, an inbound UDS frame.
    if not addressing.has_mailbox(target):
        raise ValueError(
            f"{target.name} has no bus mailbox ({addressing.for_entry(target).SPACE} "
            "address) -- reach it through its own transport"
        )

    roster_target = find_entry(target.id, home)
    if roster_target is None:
        now = now_iso()
        persisted = RosterEntry(
            id=target.id,
            name=target.name,
            kind=target.kind,
            pid=target.pid,
            cwd=target.cwd,
            status=target.status,
            inbox=target.inbox,
            native=target.native,
            registeredAt=now,
            updatedAt=now,
        )
        save_roster_entry(persisted, home)
        roster_target = persisted

    inbox_path = _inbox_path_for(roster_target.id, home)

    unread = _count_unread_lines(inbox_path)
    if unread >= MAX_UNREAD:
        raise ValueError(f"inbox full: {unread} unread >= {MAX_UNREAD}")

    # Resolve who we are. Without this every message is from "anonymous" with a
    # fresh random id, so two messages from the same agent look like different
    # senders and a recipient has no address to reply to. An explicit from_name
    # still wins (the CLI uses it); the MCP tool does not expose it, so an agent
    # cannot claim another agent's identity.
    sender_kind = from_kind
    if from_name:
        sender_name = from_name
        sender_id = new_id()
    else:
        me = get_self(home)
        if me is not None:
            sender_name, sender_id, sender_kind = me.name, me.id, me.kind
        else:
            sender_name = "anonymous"
            sender_id = new_id()
    from_ref = make_agent_ref(sender_id, sender_name, sender_kind)

    msg: Message = {
        "id": message_id or new_id(),
        "ts": now_iso(),
        "from_": from_ref,
        "to": {"id": roster_target.id, "name": roster_target.name},
        "summary": summary or (text[:60] + ("..." if len(text) > 60 else "")),
        "text": text,
        "replyTo": None,
        # Pre-acked when a native transport already delivered it. The peer never
        # polls this inbox, and an unread it cannot clear is exactly what the
        # old NO_MAILBOX_KINDS exclusion existed to prevent.
        "read": read,
    }

    with open(inbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message_to_json(msg)) + "\n")

    return msg["id"]


def _no_such_agent(name_or_id: str, home: str | None) -> str:
    """The message, with what the roster actually held at the time.

    "no such agent: X" is true and unactionable: it does not say whether the
    roster was empty, held X under a process that had died, or held something
    else entirely. That difference is the whole diagnosis, and in CI the error
    string is the only evidence there will ever be -- a flake that reads the
    same whatever caused it costs a fresh investigation every time it fires.

    Names, kinds and pids only. Never message text: a store that quotes
    payloads into exceptions puts them in every log that catches one.
    """
    try:
        rows = [
            f"{e.name}({e.kind},pid={e.pid},"
            f"{'live' if addressing.is_live(e) else 'dead'})"
            for e in load_roster(home)
        ]
    except Exception:  # noqa: BLE001  # diagnosing a failure must not fail
        rows = ["<roster unreadable>"]
    return (f"no such agent: {name_or_id}; roster holds "
            f"{', '.join(rows) if rows else '(nothing)'}")


def _mailbox_id_for(name_or_id: str, home: str | None = None) -> str | None:
    """The inbox a name or address refers to, entry or no entry.

    A mailbox outlives the agent it belongs to -- that is what retention is for
    -- so requiring a live roster entry to read one made mail unreachable the
    moment its owner exited. An id is enough, because an id is an address and
    an address names a file. A bare *name* is not, and resolves only through
    the roster.
    """
    e = find_entry(name_or_id, home)
    if e is not None:
        return str(e.id)
    for d in discover_agents(home):
        if name_or_id in (d.id, d.name):
            return str(d.id)
    if os.path.exists(_inbox_path_for(name_or_id, home)):
        return name_or_id
    return None


def get_inbox(
    name_or_id: str | None = None,
    unread_only: bool = False,
    home: str | None = None,
) -> list[Message]:
    ensure_dirs(home)
    target_id = None
    if name_or_id:
        target_id = _mailbox_id_for(name_or_id, home)
        if target_id is None:
            # Never fall through to our own inbox. Reporting "empty" for
            # someone else's mailbox and then quietly showing the caller their
            # own is worse than an error: it looks like an answer.
            raise ValueError(_no_such_agent(name_or_id, home))
    else:
        self_entry = _entry_for_current_process(home)
        if self_entry:
            target_id = self_entry.id

    if not target_id:
        return []

    path = _inbox_path_for(target_id, home)
    msgs = _read_all_messages(path)
    # Filter rather than trust a sweep to have run. This is the load-bearing
    # half of expiry: whatever is still on disk, nothing stale is ever handed
    # back. `watch` compacting and `reap` collecting are both housekeeping on
    # top of this.
    msgs = [m for m in msgs if not is_expired(m)]
    if unread_only:
        msgs = [m for m in msgs if not m["read"]]
    return msgs


def ack_message(
    message_id: str, name_or_id: str | None = None, home: str | None = None
) -> bool:
    ensure_dirs(home)
    target_id = None
    if name_or_id:
        # Same resolution as get_inbox: mail you can read is mail you can ack,
        # or a recovered mailbox could be read forever and never cleared.
        target_id = _mailbox_id_for(name_or_id, home)
    else:
        self_entry = _entry_for_current_process(home)
        if self_entry:
            target_id = self_entry.id
    if not target_id:
        return False

    path = _inbox_path_for(target_id, home)
    msgs = _read_all_messages(path)
    changed = False
    for m in msgs:
        if m["id"] == message_id:
            m["read"] = True
            changed = True
    if changed:
        _write_messages(path, msgs)
    return changed


def set_status(status: str, name_or_id: str | None = None, home: str | None = None) -> bool:
    """Record status on the roster entry.

    publish_status() writes the Claude-facing session file, which is what a
    Claude peer reads. This is the other half: `agent-bus list` and the MCP
    list_agents tool read RosterEntry.status, which otherwise stayed "idle"
    from registration forever. A Claude peer has no listener at all, so the
    roster is the only place its status can live.
    """
    entry = find_entry(name_or_id, home) if name_or_id else _entry_for_current_process(home)
    if entry is None:
        return False
    entry.status = status  # type: ignore[assignment]
    entry.updatedAt = now_iso()
    save_roster_entry(entry, home)
    return True


def get_self(home: str | None = None) -> RosterEntry | None:
    return _entry_for_current_process(home)


def find_orphaned_inboxes(home: str | None = None) -> list[dict[str, Any]]:
    """Mailboxes on disk that no roster entry points at any more.

    These exist because presence and mail used to die together: a discovered
    peer was persisted, written to, and then pruned when its process exited,
    leaving the messages behind with nothing addressing them. Retention now
    keeps an entry that has unread mail, so this is recovery for what was
    stranded before that rule existed.

    The id is recovered from the first message's `to.id`, never from the
    filename. _safe_id_for_fs is one-way and was, until recently, lossy -- so
    inverting a filename is guesswork, and guessing wrong hands one agent's
    mail to another.
    """
    ensure_dirs(home)
    known = {str(e.id) for e in load_roster(home)}
    out: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(_inbox_dir(home or get_home()), "*.jsonl"))):
        msgs = _read_all_messages(path)
        if not msgs:
            continue
        recovered = (msgs[0].get("to") or {}).get("id")
        if not recovered or str(recovered) in known:
            continue
        out.append({
            "id": str(recovered),
            "path": path,
            "total": len(msgs),
            "unread": sum(1 for m in msgs if not m.get("read")),
            "kind": parse_address(str(recovered)).kind or FALLBACK_KIND,
            "name": (msgs[0].get("to") or {}).get("name") or str(recovered),
        })
    return out


def adopt_orphan(orphan: dict[str, Any], home: str | None = None) -> RosterEntry:
    """Give a stranded mailbox an entry again, so it can be addressed.

    The entry is deliberately not live: its process is long gone, so it stays
    out of `list` and is pruned as soon as the mail is acked. It exists to be
    *readable*, which is the one thing it could not be.
    """
    now = now_iso()
    entry = RosterEntry(
        id=orphan["id"],
        name=orphan["name"],
        kind=orphan["kind"],
        pid=None,
        cwd=None,
        status="unknown",
        inbox=_make_inbox_ref(orphan["id"], home),
        native={},
        registeredAt=now,
        updatedAt=now,
    )
    save_roster_entry(entry, home)
    return entry
