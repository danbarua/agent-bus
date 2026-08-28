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
import os
import signal
import sys
import time
from typing import Any

from agent_bus import log
from agent_bus.paths import get_home

from .bridge import (
    INBOUND_POLL_IDLE_SECONDS,
    HttpCloudClient,
    SpoolClient,
    bridge,
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


def main(argv: list[str] | None = None) -> int:
    _stop_on_sigterm()
    log.configure()
    log.identify(surface="bridge")
    p = argparse.ArgumentParser(
        prog="agent-bridge",
        description="Stand in on the bus for a peer that is only reachable remotely.",
    )
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
    p.add_argument(
        "--spool-dir",
        default=None,
        help="write outbound mail here and read replies from here instead of "
             "reaching the cloud. Wins over a token: pass it to work offline "
             "on a machine that has one",
    )
    p.add_argument(
        "--auto-reply",
        action="store_true",
        help="reply to each sender with a one-line receipt saying the message "
             "was queued but not yet read (off by default: it is an unprompted "
             "message into someone else's context)",
    )
    p.add_argument(
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
    args = p.parse_args(argv)

    # Nothing to configure. A token at ~/.agent-bus/cloud-token names its own
    # server, so installing it is the whole of "connect this bridge to the
    # cloud" -- and `--spool-dir` is how you opt back out without moving it.
    try:
        client = _client(args.spool_dir)
    except RuntimeError as e:
        print(f"agent-bridge: {e}", file=sys.stderr)
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
        return 2


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
            print(f"cloud endpoint: {url} (token from the {token_source()})",
                  file=sys.stderr)
            exp = token_expiry(token)
            if exp is not None:
                days = (exp - time.time()) / 86400.0
                print(f"token expires in {days:.1f} days", file=sys.stderr)
            return HttpCloudClient(url, token)

    root = spool_dir or os.path.join(get_home(), "cloud-spool")
    if not spool_dir:
        print(
            f"no cloud endpoint configured; spooling to {root}. "
            "Mail is written there rather than sent, and replies are read from "
            "the same place -- visible rather than silently dropped.",
            file=sys.stderr,
        )
    return SpoolClient(root)


if __name__ == "__main__":
    raise SystemExit(main())
