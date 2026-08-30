"""CLI entrypoint for agent-bus."""
from __future__ import annotations

import argparse
import json
import os
import select
import sys
import time
from typing import Any

from . import __version__, log
from .commands import agents, messages
from .lifecycle import session_end, session_start
from .mcp_server import main as mcp_main
from .protocol import KNOWN_KINDS
from .store import unregister as do_unregister
from .uds import run_listen


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str, sort_keys=True))


def cmd_list(args: argparse.Namespace) -> int:
    rows = agents.list_agents(kind=args.kind)
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("no agents")
        return 0
    # Width from the data, not a guess. A fixed 20 was fine until an omp
    # session turned up called 58660-5cec406c-d32c-4861-b00a-447b0a23ed87 and
    # shoved every column after it out of line for that row only.
    width = min(max((len(a["name"]) for a in rows), default=4), 40)

    # An id is only worth printing when the name is not enough to address the
    # agent -- which happens for real: two omp sessions both called omp-58754.
    # Printing every id every time buried that case in forty characters of uuid
    # per row. `--json` still carries all of them.
    seen: dict[str, int] = {}
    for a in rows:
        seen[a["name"]] = seen.get(a["name"], 0) + 1

    print(f"{'NAME':<{width}} {'KIND':<8} {'PID':>7} STATUS")
    for a in rows:
        pid = a["pid"] or ""
        name = a["name"] if len(a["name"]) <= width else a["name"][: width - 1] + "\u2026"
        print(f"{name:<{width}} {a['kind']:<8} {pid!s:>7} {a['status']}")
        if seen[a["name"]] > 1:
            print(f"{'':<{width}} shares this name -- address it as {a['id']}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    try:
        sent = messages.send(
            to=args.target,
            text=args.message,
            summary=args.summary or "",
            from_name=args.from_name,
        )
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return 1
    # What the sender needs to know is that it went, and to whom. Which
    # channel carried it, and the id it was filed under, are ours -- `--json`
    # is where a caller that genuinely wants the mechanism should look.
    print(f"sent to {sent.get('to') or args.target}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    try:
        # An unknown target is an error now, not an empty inbox -- store used to
        # answer "empty" and then quietly read the caller's own mailbox.
        msgs = messages.inbox(name=args.name, unread_only=args.unread)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        _print_json(msgs)
        return 0
    if not msgs:
        print("no messages")
        return 0
    for m in msgs:
        state = "read  " if m["read"] else "unread"
        when = m["ts"][11:16] if len(m["ts"]) > 16 else m["ts"]
        # The summary is a subject line, so it goes on the header. Indented
        # under the body it was indistinguishable from the first line of it.
        subject = f": {m['summary']}" if m["summary"] else ""
        print(f"{state}  {when}  from {m['from']['name']}{subject}")
        # Whole. MAX_TEXT caps a body at 32,768 when it is sent.
        for line in m["text"].splitlines() or [""]:
            print(f"        {line}")
        # Name the action rather than printing a field and leaving the reader
        # to work out what to do with it.
        if not m["read"]:
            print(f"        mark read with: agent-bus ack {m['id']}")
        print()
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """One message, whole, by the id the notice gave you."""
    try:
        msg = messages.read_one(args.message_id, name=args.name)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if msg is None:
        print("no such message", file=sys.stderr)
        return 1
    if args.json:
        _print_json(msg)
        return 0
    subject = f": {msg['summary']}" if msg["summary"] else ""
    print(f"from {msg['from']['name']}{subject}")
    print()
    print(msg["text"])
    if not msg["read"]:
        print()
        print(f"mark read with: agent-bus ack {msg['id']}")
    return 0


def cmd_ack(args: argparse.Namespace) -> int:
    try:
        ok = messages.ack(args.message_id, name=args.name)["acked"]
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("marked read" if ok else "no such message")
    return 0 if ok else 1


def cmd_register(args: argparse.Namespace) -> int:
    """Claim a name for the session, never for this command.

    The pid is resolved before registering rather than left to the library
    default, because the library's last resort is `os.getpid()` -- correct for
    an agent that imported agent_bus into its own long-lived process, and a
    guaranteed no-op here. This process exits microseconds from now and the
    entry is pruned on the next roster read, so registering it reported success
    while writing nothing.
    """
    pid, source = agents.resolve_host_pid(args.pid, None)
    if source == agents.PID_OWN:
        print(
            "register failed: cannot tell which process is the session.\n"
            "No harness on this machine claims an ancestor of this command, so "
            "registering would claim a pid that dies with it.\n"
            "Pass the session's pid: agent-bus register --name "
            f"{args.name} --pid $PPID",
            file=sys.stderr,
        )
        return 1
    try:
        entry = agents.register(args.name, args.kind, pid=pid, cwd=args.cwd)
    except Exception as e:
        print(f"register failed: {e}", file=sys.stderr)
        return 1
    print(f"registered as {entry['name']} (pid {entry.get('pid')})")
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    ok = do_unregister(args.name)
    print(f"removed {args.name}" if ok else f"no agent called {args.name}")
    return 0


def cmd_self(args: argparse.Namespace) -> int:
    """Say what is true of *this* session, not one sentence for both cases.

    "not registered -- run: agent-bus register" was advice that could not work
    while register claimed a dying pid, and it read as "you are not on the
    bus" to eleven agents that eleven peers could already address. Being
    reached needs nothing; initiating is the other half. Which of those two an
    unregistered session is in is knowable, so it is answered rather than
    hedged over.
    """
    e = agents.self_info()
    if not e["registered"]:
        if args.json:
            _print_json(e)
        elif e.get("reachable"):
            print(
                f"not registered -- but reachable as {e['name']} ({e['kind']}), "
                "discovered by your harness. Peers can send to you already.\n"
                "Register to claim a name of your own and a mailbox to read:\n"
                "  agent-bus register --name <name>"
            )
        else:
            print(
                "not registered, and no harness on this machine publishes this "
                "session -- so nothing can address you.\n"
                "  agent-bus register --name <name> --pid $PPID"
            )
        return 1
    if args.json:
        _print_json(e)
        return 0
    print(f"{e['name']} ({e['kind']}) in {e['cwd']}")
    return 0


def cmd_listen(args: argparse.Namespace) -> int:
    # blocks
    try:
        run_listen(
            name=args.name or "agent-bus",
            pid=args.pid,
            inbox_name=getattr(args, "inbox_name", None),
            adopt=getattr(args, "adopt", False),
        )
        return 0
    except Exception as e:
        print(f"listen error: {e}", file=sys.stderr)
        return 1


# Long enough for a harness that writes a payload immediately, short enough
# that a harness which never writes one costs nothing.
HOOK_STDIN_TIMEOUT = 0.25


def _hook_payload(timeout: float = HOOK_STDIN_TIMEOUT) -> dict[str, Any] | None:
    """Read a hook payload without ever blocking the host.

    This used to be a plain sys.stdin.read(). A harness may hand a hook a pipe
    it opens and never closes -- Grok pipes hook stdin
    (xai-grok-hooks/src/runner/command.rs:188) -- and reading such a pipe never
    returns. Verified with a fifo: the old code sat there until killed. A hook
    that hangs is worse than one that fails, so we wait a bounded moment for
    something to arrive and give up otherwise.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        fd = sys.stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return None

    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            ready, _, _ = select.select([fd], [], [], remaining)
        except (OSError, ValueError):
            break
        if not ready:
            break
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:  # EOF -- the harness wrote and closed
            break
        chunks.append(chunk)

    raw = b"".join(chunks).decode("utf-8", errors="replace")
    if not raw.strip():
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def cmd_hook(args: argparse.Namespace) -> int:
    """Session lifecycle for a harness that runs hooks rather than our MCP server.

    Always exits 0. We do not know what an unknown harness does with a non-zero
    hook exit -- in some it is a control signal -- and a messaging bus must
    never be able to stop a session starting. Diagnostics go to stderr.

    The MCP server is the better path and needs none of this: serve() calls
    session_start() on startup and session_end() on exit, in-process, with the
    harness's own environment. This remains for a harness that has hooks and no
    MCP.
    """
    payload = _hook_payload()
    if args.event == "session-start":
        try:
            entry = session_start(payload=payload)
        except Exception as e:
            print(f"agent-bus: session-start failed: {e}", file=sys.stderr)
            return 0
        try:
            unread = len(messages.inbox(name=entry.name, unread_only=True))
        except Exception:
            unread = 0
        # stderr, not stdout. stdout used to carry Claude Code's
        # hookSpecificOutput envelope *and* a duplicate top-level
        # additionalContext -- a shotgun fired at two schemas. An unknown
        # harness may ignore stdout, parse it against a schema we have never
        # seen, or inject it verbatim into a model's context, so we say nothing
        # there rather than guess.
        print(
            f"agent-bus: registered as {entry.name} ({entry.kind}), {unread} unread",
            file=sys.stderr,
        )
        return 0
    try:
        ok = session_end(payload=payload)
    except Exception as e:
        print(f"agent-bus: session-end failed: {e}", file=sys.stderr)
        return 0
    print("agent-bus: unregistered" if ok else "agent-bus: no match", file=sys.stderr)
    return 0

def cmd_watch(args: argparse.Namespace) -> int:
    """Follow this agent's inbox, one line per message.

    Intended as the command a harness watch mechanism runs -- Grok's monitor
    tool turns each stdout line into a conversation event.
    """
    from .watch import watch

    try:
        return watch(args.name, from_start=args.from_start)
    except KeyboardInterrupt:
        return 0


def cmd_reap(args: argparse.Namespace) -> int:
    """Delete long-dead messages from every inbox.

    Runs at twice the TTL, which is what makes it safe to invoke at any moment:
    anything it removes was already invisible to every reader, because get_inbox
    filters at one TTL. So this is garbage collection with no correctness
    burden -- it cannot lose a race, and skipping it costs disk, not delivery.
    """
    from .store import REAP_AFTER_SECONDS, reap

    older = args.older_than if args.older_than is not None else REAP_AFTER_SECONDS
    removed = reap(older_than=older)
    hours = older / 3600
    print(f"reaped {removed} message(s) older than {hours:g}h")
    if older < REAP_AFTER_SECONDS:
        print(
            "note: below the default threshold -- messages a reader could still "
            "have been shown were removed",
            file=sys.stderr,
        )
    return 0


def cmd_orphans(args: argparse.Namespace) -> int:
    """Find mailboxes no roster entry points at, and optionally re-home them.

    Presence and mail used to die together, so a peer that exited left its
    messages behind with nothing addressing them. Retention fixed that for an
    agent that stays dead.

    It does not fix a **restart**, which is why this is routine rather than a
    one-off cleanup of historical damage. A named agent that comes back gets a
    new entry with a new id, and a mailbox is keyed by id, so whatever was
    unread stays in the old one: measured, `labkit-dev` restarted, `inbox`
    answered empty, and the mail was still on disk. Worth running whenever an
    agent insists it received nothing.

    `agent_bridge` no longer needs it -- it drains its own role inbox before
    re-registering. That is the same fix, applied where the identity is
    unambiguous enough to automate it; a bare name is not.
    """
    from .store import adopt_orphan, find_orphaned_inboxes

    orphans = find_orphaned_inboxes()
    if not orphans:
        print("no orphaned mailboxes")
        return 0
    for o in orphans:
        if args.adopt:
            adopt_orphan(o)
        flag = "adopted" if args.adopt else "orphaned"
        print(f"[{flag}] {o['id']}  {o['unread']} unread of {o['total']}")
    if not args.adopt:
        total = sum(o["unread"] for o in orphans)
        print(f"\n{total} unread message(s) unreachable. Re-run with --adopt to address them.")
    return 0


def cmd_grok_status(args: argparse.Namespace) -> int:
    """Grok session activity, from the leader that hosts them.

    `agent-bus list` already folds this into a grok peer's status. This is the
    direct view, and `--watch` is the push channel: the leader broadcasts every
    upsert and removal to every connected client, so one watcher sees the whole
    machine. One line per change, which is what a monitor tool can consume.
    """
    from .grok_leader import (
        LeaderClient,
        LeaderError,
        activity_to_status,
        leader_available,
        leader_socket,
    )

    if not leader_available():
        print(
            f"no grok leader at {leader_socket()} -- grok only runs one in "
            "leader mode, and its roster is per-leader and in-memory",
            file=sys.stderr,
        )
        return 1
    try:
        with LeaderClient() as client:
            if not args.watch:
                for s in client.list_sessions():
                    status = activity_to_status(s.get("activity")) or "-"
                    title = (s.get("title") or "")[:44]
                    print(f"{s.get('sessionId','?'):38} {s.get('activity',''):12} "
                          f"{status:8} {title}")
                return 0
            client.list_sessions()  # prime; the roster is the current truth
            for delta in client.watch():
                for e in delta["upserted"]:
                    status = activity_to_status(e.get("activity")) or "-"
                    print(f"[grok] {e.get('sessionId','?')} {e.get('activity','')} "
                          f"-> {status}", flush=True)
                for sid in delta["removed"]:
                    print(f"[grok] {sid} removed", flush=True)
    except LeaderError as e:
        print(f"grok-status failed: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Report this agent's status so other agents' listings show it."""
    result = agents.set_status(args.status, cwd=args.cwd)
    if not result["recorded"]:
        reason = result.get("reason", "could not record status")
        print(reason, file=sys.stderr)
        return 1
    # A Claude peer publishes no listener, so there is no session file to
    # patch. That is not a failure: the roster is the status of record.
    suffix = "" if result["published"] else " (visible on the bus only)"
    print(f"status set to {args.status}{suffix}")
    return 0


def cmd_help(args: argparse.Namespace) -> int:
    if args.topic is None:
        args.root_parser.print_help()
        return 0
    parser = args.subparsers.choices.get(args.topic)
    if parser is None:
        args.root_parser.error(
            f"argument topic: invalid choice: {args.topic!r} "
            f"(choose from {', '.join(sorted(args.subparsers.choices))})"
        )
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    log.configure()
    log.identify(surface="cli")
    if argv is None:
        argv = sys.argv[1:]
    p = argparse.ArgumentParser(prog="agent-bus", description="inter-agent messaging bus")
    # The MCP handshake reports this in serverInfo and every log record carries
    # it as `v`, so the CLI was the one surface that could not answer "which
    # agent-bus is this?" -- the question you ask when a harness is running the
    # published package and the checkout is something else.
    p.add_argument("--version", action="version", version=f"agent-bus {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    phlp = sub.add_parser("help", help="show help for agent-bus or a command")
    phlp.add_argument("topic", nargs="?")
    phlp.set_defaults(func=cmd_help, root_parser=p, subparsers=sub)

    # list
    pl = sub.add_parser("list", help="list live agents from roster + native adapters")
    pl.add_argument("--kind", default=None, help="claude|grok|omp|codex|all")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_list)

    # send (file bus)
    ps = sub.add_parser(
        "send",
        help="send text to an agent by name or id; agent-bus works out how to reach them",
    )
    ps.add_argument("target", help="name or id (from list)")
    ps.add_argument("-m", "--message", required=True, help="plain text (max 1M)")
    ps.add_argument("--summary", default=None)
    ps.add_argument("--from-name", default=None)
    ps.set_defaults(func=cmd_send)

    # inbox
    pi = sub.add_parser("inbox", help="read messages addressed to self or --name")
    pi.add_argument("--name", default=None)
    pi.add_argument("--unread", action="store_true")
    pi.add_argument("--json", action="store_true")
    pi.set_defaults(func=cmd_inbox)

    # ack
    prd = sub.add_parser("read", help="print one message, whole")
    prd.add_argument("message_id")
    prd.add_argument("--name", default=None)
    prd.add_argument("--json", action="store_true")
    prd.set_defaults(func=cmd_read)

    pa = sub.add_parser("ack", help="mark message read")
    pa.add_argument("message_id")
    pa.add_argument("--name", default=None)
    pa.set_defaults(func=cmd_ack)

    # register
    pr = sub.add_parser("register", help="register this process so others can send to you")
    pr.add_argument("--name", required=True)
    # no `choices`: an unknown harness must be able to name itself
    pr.add_argument(
        "--kind",
        required=True,
        metavar="KIND",
        help=f"harness name; commonly one of {', '.join(KNOWN_KINDS)}",
    )
    pr.add_argument("--cwd", default=None)
    pr.add_argument("--pid", type=int, default=None)
    pr.set_defaults(func=cmd_register)

    # unregister
    pu = sub.add_parser("unregister", help="remove by name")
    pu.add_argument("--name", required=True)
    pu.set_defaults(func=cmd_unregister)

    # self
    psf = sub.add_parser("self", help="show current registered self")
    psf.add_argument("--json", action="store_true")
    psf.set_defaults(func=cmd_self)

    # listen: how a non-Claude agent becomes a peer
    plis = sub.add_parser(
        "listen",
        help="publish as a Claude peer (writes sessions/<pid>.json + binds <sock-dir>/<pid>.sock)",
    )
    plis.add_argument("--name", default="agent-bus", help="name visible to ListAgents")
    plis.add_argument(
        "--pid",
        "--watch-pid",
        type=int,
        default=None,
        help="WATCH-PID only (host pid); if it dies, listen exits and cleans "
             "up. NOT the pid advertised in sessions/<getpid()>.json -- the "
             "binder always uses the listener's own getpid, for Claude "
             "getpeereid compatibility",
    )
    plis.add_argument(
        "--inbox-name",
        default=None,
        help="file-bus inbox target name for inbound UDS user frames (defaults to --name)",
    )
    plis.add_argument(
        "--adopt",
        action="store_true",
        # Set by start_uds_listen, which registers before it spawns. Not for
        # people: a listener started by hand has nothing to adopt.
        help=argparse.SUPPRESS,
    )
    plis.set_defaults(func=cmd_listen)
    pw = sub.add_parser(
        "watch",
        help="follow this agent's inbox, one line per message (for monitor tools)",
    )
    pw.add_argument("--name", default=None, help="agent to watch; defaults to self")
    pw.add_argument(
        "--from-start",
        action="store_true",
        help="replay existing messages first (off by default: a backlog can "
             "trip a watcher's rate limit immediately)",
    )
    pw.set_defaults(func=cmd_watch)

    pst = sub.add_parser("status", help="report this agent's status")
    pst.add_argument("status", help="e.g. idle, busy, waiting")
    pst.add_argument("--cwd", default=None)
    pst.set_defaults(func=cmd_status)

    pr = sub.add_parser(
        "reap",
        help="delete messages past twice the TTL; get_inbox already hides them",
    )
    pr.add_argument(
        "--older-than",
        type=float,
        default=None,
        help="seconds; defaults to twice the message TTL",
    )
    pr.set_defaults(func=cmd_reap)

    po = sub.add_parser(
        "orphans",
        help="find mailboxes with no roster entry; --adopt makes them addressable",
    )
    po.add_argument(
        "--adopt",
        action="store_true",
        help="write a roster entry for each, so its mail can be read again",
    )
    po.set_defaults(func=cmd_orphans)

    pgs = sub.add_parser(
        "grok-status",
        help="grok session activity from its leader; --watch streams changes",
    )
    pgs.add_argument(
        "--watch",
        action="store_true",
        help="subscribe to the leader's broadcast and print each change",
    )
    pgs.set_defaults(func=cmd_grok_status)

    ph = sub.add_parser("hook", help="plugin SessionStart/SessionEnd (register host pid)")
    ph.add_argument("event", choices=["session-start", "session-end"])
    ph.set_defaults(func=cmd_hook)

    pm = sub.add_parser("mcp", help="stdio MCP server (plugin process: tools + UDS listen)")
    pm.set_defaults(func=lambda _args: mcp_main())
    args = p.parse_args(argv)
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
