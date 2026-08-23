"""Store and bus logic. Roster + inboxes under AGENT_BUS_HOME (default ~/.agent-bus).

All operations are best-effort. No network. Pid checks via os.kill.
"""
from __future__ import annotations

import json
import os
import re

from .process import is_pid_alive, is_process_alive, proc_start
from .protocol import (
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

DEFAULT_HOME = os.path.expanduser("~/.agent-bus")
MAX_TEXT = 1_000_000
MAX_UNREAD = 50


def get_home() -> str:
    return os.environ.get("AGENT_BUS_HOME", DEFAULT_HOME)


def _roster_dir(home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, "roster")


def _inbox_dir(home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, "inboxes")


def _captures_dir(home: str | None = None) -> str:
    h = home or get_home()
    return os.path.join(h, "captures")


def ensure_dirs(home: str | None = None) -> None:
    h = home or get_home()
    os.makedirs(_roster_dir(h), exist_ok=True)
    os.makedirs(_inbox_dir(h), exist_ok=True)
    os.makedirs(_captures_dir(h), exist_ok=True)


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
    try:
        import subprocess

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
    except Exception:
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
    return re.sub(r'[^A-Za-z0-9_.:-]', '_', s)


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
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entries.append(dict_to_roster(data))
        except Exception:
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
        if not entry.pid or is_process_alive(entry.pid, entry.procStart):
            continue
        if has_mail(entry.id, home):
            continue  # dead process, undelivered mail -- keep it addressable
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
        native={},
        registeredAt=now,
        updatedAt=now,
        # recorded at registration so a recycled pid cannot later impersonate us
        procStart=proc_start(pid),
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
        if e.id == name_or_id or e.name == name_or_id:
            if is_process_alive(e.pid, e.procStart):
                return e
            if stale is None:
                stale = e
    return stale


def get_live_roster(home: str | None = None) -> list[RosterEntry]:
    """Only agents whose process is running -- what a presence view wants."""
    prune_dead_roster(home)
    return [e for e in load_roster(home) if is_process_alive(e.pid, e.procStart)]


def discover_agents(home: str | None = None) -> list[RosterEntry]:
    from .adapters import discover_all

    raw = discover_all()
    out: list[RosterEntry] = []
    seen_ids: set[str] = set()
    for d in raw:
        if d.get("id") in seen_ids:
            continue
        pid = d.get("pid")
        if not is_pid_alive(pid):
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
def list_agents(
    kind: str | None = None, home: str | None = None
) -> list[RosterEntry]:
    roster = get_live_roster(home)
    discovered = discover_agents(home)

    by_id: dict[str, RosterEntry] = {e.id: e for e in roster}
    for d in discovered:
        if d.id not in by_id:
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
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not obj.get("read", False):
                        count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


def _read_all_messages(path: str) -> list[Message]:
    msgs: list[Message] = []
    if not os.path.exists(path):
        return msgs
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    msgs.append(json_to_message(obj))
                except Exception:
                    continue
    except Exception:
        pass
    return msgs


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
        if d.id == to or d.name == to:
            return d
    return None


def send_message(
    to: str,
    text: str,
    summary: str = "",
    from_name: str | None = None,
    from_kind: Kind = "other",
    home: str | None = None,
) -> str:
    ensure_dirs(home)
    if len(text) > MAX_TEXT:
        raise ValueError(f"text too long: {len(text)} > {MAX_TEXT}")

    target = resolve_target(to, home)
    if target is None:
        raise ValueError(f"no such agent: {to}")

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
        "id": new_id(),
        "ts": now_iso(),
        "from_": from_ref,
        "to": {"id": roster_target.id, "name": roster_target.name},
        "summary": summary or (text[:60] + ("..." if len(text) > 60 else "")),
        "text": text,
        "replyTo": None,
        "read": False,
    }

    with open(inbox_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(message_to_json(msg)) + "\n")

    return msg["id"]


def get_inbox(
    name_or_id: str | None = None,
    unread_only: bool = False,
    home: str | None = None,
) -> list[Message]:
    ensure_dirs(home)
    target_id = None
    if name_or_id:
        e = find_entry(name_or_id, home)
        if e is None:
            for d in discover_agents(home):
                if d.id == name_or_id or d.name == name_or_id:
                    target_id = d.id
                    break
        else:
            target_id = e.id
    else:
        self_entry = _entry_for_current_process(home)
        if self_entry:
            target_id = self_entry.id

    if not target_id:
        return []

    path = _inbox_path_for(target_id, home)
    msgs = _read_all_messages(path)
    if unread_only:
        msgs = [m for m in msgs if not m["read"]]
    return msgs


def ack_message(
    message_id: str, name_or_id: str | None = None, home: str | None = None
) -> bool:
    ensure_dirs(home)
    target_id = None
    if name_or_id:
        e = find_entry(name_or_id, home)
        if e:
            target_id = e.id
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


def capture_path(pid: int | None = None, home: str | None = None) -> str:
    ensure_dirs(home)
    if pid is None:
        pid = os.getpid()
    h = home or get_home()
    return os.path.join(_captures_dir(h), f"{pid}.jsonl")
