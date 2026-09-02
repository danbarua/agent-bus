"""`agent-bridge` -- run a bridge for one remote peer.

Its own entry point rather than an `agent-bus` subcommand. The dependency runs
one way: a bridge needs agent-bus, and agent-bus must never need a bridge. A
subcommand would have inverted that, or bought its way out with an optional
import nobody could reason about.

Practically, it also means someone working on the bus never has to think about
cloud context to run `agent-bus`.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import time
from typing import Any

from agent_bus import log
from agent_bus.paths import get_home
from agent_bus.protocol import MessageId

from .bridge import (
    INBOUND_POLL_IDLE_SECONDS,
    HttpCloudClient,
    SpoolClient,
    bridge,
    bridge_address,
    read_cloud_token,
    token_expiry,
    token_source,
)


def _stop_on_sigterm() -> None:
    """Make SIGTERM behave like Ctrl-C, so the bridge gets to leave the bus.

    Python's default SIGTERM handler exits the interpreter without unwinding,
    so `finally` never runs -- and the listener, which is a detached process
    and does not die with its parent, survives.

    launchd is what makes that visible. `launchctl kickstart -k` waits for the
    whole process group before restarting, so the orphaned listener held the
    restart for about two minutes; and while it lived it went on publishing a
    Claude-shaped session file, so the peer stayed discoverable after the thing
    it stood in for had stopped.
    """
    def _raise(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    with contextlib.suppress(ValueError):  # not the main thread
        signal.signal(signal.SIGTERM, _raise)


def _address_args(p: argparse.ArgumentParser) -> None:
    """`--kind` and `--name`, on every verb that needs an address.

    Repeated per verb rather than hoisted above the subcommand, so each one
    reads the way it is typed: `agent-bridge start --kind desktop --name
    claude`, not `agent-bridge --kind desktop --name claude start`.
    """
    # No `choices=`. It named two providers, so a third job could not start
    # until the enum was edited -- and the kind is what decides behaviour, not
    # a list of names we happen to have thought of.
    p.add_argument(
        "--kind",
        required=True,
        help="what sort of peer this stands in for: desktop, webhook, ...",
    )
    p.add_argument(
        "--name",
        required=True,
        help="which one; `--kind desktop --name claude` is addressed as "
             "desktop:claude, and there is one bridge per address",
    )


def build_parser() -> argparse.ArgumentParser:
    """Verbs, with `start` as the one that runs a daemon.

    `agent-bus mcp` is the shape: a verb starts the long-running process, and
    the others are commands. Until this existed `agent-bridge` was flags only,
    so there was nowhere to put a query -- which is what #219 needs.

    The bare-flag form is gone rather than kept working. Nothing outside this
    machine runs it, so the migration is to stop the service, uninstall it and
    install the new one; a shim would have outlived the thing it was shimming.
    """
    p = argparse.ArgumentParser(
        prog="agent-bridge",
        description="Stand in on the bus for a peer that is only reachable remotely.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser(
        "start", help="run the bridge for one address; blocks until stopped")
    _address_args(start)
    start.add_argument(
        "--spool-dir",
        default=None,
        help="write outbound mail here and read replies from here instead of "
             "reaching the cloud. Wins over a token: pass it to work offline "
             "on a machine that has one",
    )
    start.add_argument(
        "--auto-reply",
        action="store_true",
        help="reply to each sender with a one-line receipt saying the message "
             "was queued but not yet read (off by default: it is an unprompted "
             "message into someone else's context)",
    )
    start.add_argument(
        "--inbound-poll",
        type=float,
        default=INBOUND_POLL_IDLE_SECONDS,
        metavar="SECONDS",
        help="how long to wait between cloud polls when nothing is moving "
             f"(default {INBOUND_POLL_IDLE_SECONDS:.0f}). After any traffic it "
             "polls every few seconds for a minute regardless, so this is the "
             "worst case for a message arriving out of the blue, not for a "
             "reply in a conversation",
    )
    start.set_defaults(func=cmd_start)

    read = sub.add_parser(
        "read",
        help="where a message got to: which cloud queue holds it, and whether "
             "it has been read. Does not consume it",
    )
    read.add_argument("message_id", help="the id `agent-bus inbox` reported")
    _address_args(read)
    read.add_argument(
        "--spool-dir",
        default=None,
        help="look in a spool directory instead of the cloud",
    )
    read.add_argument("--json", action="store_true")
    read.set_defaults(func=cmd_read)
    return p


def cmd_read(args: argparse.Namespace) -> int:
    """Where a message got to, inside its lifetime.

    The id is the same string on both sides -- a local message travels as the
    cloud's document id -- so the id `agent-bus inbox` printed addresses it
    here without any correlation step.

    Exit 1 when nothing holds it. Not an error: "expired, or it never arrived"
    is a real answer, and a script asking about a message that has gone should
    be able to tell without parsing prose.
    """
    try:
        address = bridge_address(args.kind, args.name)
        client = _client(args.spool_dir)
        found = client.read(address, MessageId(args.message_id))
    except (RuntimeError, ValueError) as e:
        print(f"agent-bridge: {e}", file=sys.stderr)
        log.warn("read failed", trace_id=args.message_id, error=str(e))
        return 2

    if args.json:
        print(json.dumps(found, indent=2, default=str, sort_keys=True))
        return 0 if found.get("queue") else 1

    queue, msg = found.get("queue"), found.get("message")
    if not queue or not msg:
        print(f"no message {args.message_id} in either queue for {address}.\n"
              "It was delivered and has expired, or it never arrived.")
        return 1

    # Which queue is the answer, so it leads. `inbox` and `outbox` are named
    # from the *peer's* side, which is not obvious from the words alone.
    whose = ("waiting for the peer to read it" if queue == "inbox"
             else "written by the peer, waiting for this bridge to pull it")
    state = "read" if msg.get("read") else "unread"
    print(f"{args.message_id} is in {address}:{queue}, {state} -- {whose}")
    if msg.get("from"):
        print(f"from {msg['from']}" + (f": {msg['summary']}" if msg.get("summary") else ""))
    if msg.get("text"):
        print()
        print(msg["text"])
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    # Nothing to configure. A token at ~/.agent-bus/cloud-token names its own
    # server, so installing it is the whole of "connect this bridge to the
    # cloud" -- and `--spool-dir` is how you opt back out without moving it.
    try:
        client = _client(args.spool_dir)
    except RuntimeError as e:
        print(f"agent-bridge: {e}", file=sys.stderr)
        log.warn("bridge did not start", error=str(e), kind=args.kind, name=args.name)
        return 2

    try:
        return bridge(
            args.kind, args.name, client,
            auto_reply=args.auto_reply,
            inbound_poll=args.inbound_poll,
            expires_at=_expires_at(args.spool_dir),
        )
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"agent-bridge: {e}", file=sys.stderr)
        log.warn("bridge stopped", error=str(e), kind=args.kind, name=args.name)
        return 2


def main(argv: list[str] | None = None) -> int:
    _stop_on_sigterm()
    # service picks the file (agent-bridge.jsonl, not agent-bus.jsonl) --
    # #197. Passed to configure() itself, not left to a later identify(),
    # because configure() is what opens the file and only does it once.
    log.configure(service="agent-bridge")
    log.identify(service="agent-bridge", surface="bridge")
    args = build_parser().parse_args(argv)
    return args.func(args)


def _expires_at(spool_dir: str | None) -> float | None:
    """When the credential this run will use runs out, if it has one.

    Read separately from `_client` rather than threaded out of it: a spooling
    bridge has no token and must not be told about one, and `_client` already
    decides that.
    """
    if spool_dir:
        return None
    cloud = read_cloud_token()
    return token_expiry(cloud[1]) if cloud else None


def _client(spool_dir: str | None):
    if not spool_dir:
        cloud = read_cloud_token()
        if cloud:
            url, token = cloud
            # Which of the two places it came from. They can both hold one, the
            # Keychain wins, and "which is live" is the first question anyone
            # debugging a 401 asks.
            source = token_source()
            # Both registers, deliberately. stderr is for the person who just
            # ran this and wants to know it came up pointed at the right place;
            # the record is for the person reading agent-bridge.jsonl a week
            # later asking which deployment it had been talking to. Neither
            # substitutes for the other -- a launchd service's stderr is not
            # where anyone looks, and a person watching a terminal does not
            # tail a jsonl.
            print(f"cloud endpoint: {url} (token from the {source})",
                  file=sys.stderr)
            log.info("cloud endpoint", url=url, token_source=source)
            exp = token_expiry(token)
            if exp is not None:
                days = (exp - time.time()) / 86400.0
                print(f"token expires in {days:.1f} days", file=sys.stderr)
                # A credential that runs out is the failure this exists to see
                # coming, so it is a record rather than only a line on a stream
                # nobody keeps.
                log.info("token expiry", days=round(days, 1), token_source=source)
            return HttpCloudClient(url, token)

    root = spool_dir or os.path.join(get_home(), "cloud-spool")
    if not spool_dir:
        print(
            f"no cloud endpoint configured; spooling to {root}. "
            "Mail is written there rather than sent, and replies are read from "
            "the same place -- visible rather than silently dropped.",
            file=sys.stderr,
        )
        # WARNING, not info: mail is being written to disk instead of sent, and
        # a bridge that has been quietly spooling for a week looks healthy from
        # every other angle.
        log.warn("no cloud endpoint; spooling", spool_dir=root,
                 token_source=token_source())
    else:
        log.info("spooling by request", spool_dir=root)
    return SpoolClient(root)


if __name__ == "__main__":
    raise SystemExit(main())
