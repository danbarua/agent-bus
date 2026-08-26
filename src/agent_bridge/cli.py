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
import os
import sys

from agent_bus import log
from agent_bus.paths import get_home

from .bridge import PROVIDERS, SpoolClient, bridge


def main(argv: list[str] | None = None) -> int:
    log.configure()
    log.identify(surface="bridge")
    p = argparse.ArgumentParser(
        prog="agent-bridge",
        description="Stand in on the bus for a peer that is only reachable remotely.",
    )
    p.add_argument(
        "--provider",
        required=True,
        choices=list(PROVIDERS),
        help="which remote peer this bridge stands in for; one process each",
    )
    p.add_argument(
        "--spool-dir",
        default=None,
        help="write outbound mail here and read replies from here, instead of "
             "reaching a cloud service (the default until one is deployed)",
    )
    p.add_argument(
        "--auto-reply",
        action="store_true",
        help="reply to each sender with a one-line receipt saying the message "
             "was queued but not yet read (off by default: it is an unprompted "
             "message into someone else's context)",
    )
    args = p.parse_args(argv)

    root = args.spool_dir
    if not root:
        root = os.path.join(get_home(), "cloud-spool")
        print(
            f"no cloud endpoint configured; spooling to {root}. "
            "Mail is written there rather than sent, and replies are read from "
            "the same place -- visible rather than silently dropped.",
            file=sys.stderr,
        )

    try:
        return bridge(args.provider, SpoolClient(root), auto_reply=args.auto_reply)
    except KeyboardInterrupt:
        return 0
    except (ValueError, RuntimeError) as e:
        print(f"agent-bridge: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
