"""Names for the agents a test creates.

Minted per run, for three reasons.

**Nothing may match by luck.** A test that registers `smoke-target` and asserts
a message came from `smoke-target` passes if anything at all produced that
name -- a leftover from the previous run, another tier, a stale roster entry
the reaper had not reached. A name nothing else could have chosen makes the
assertion mean what it says.

**Names collided across tests.** Three tests registered `pi-peer` against the
real sessions directory, so their peers were distinguishable only by timing.

**They read like the test, not like an agent.** `smoke-a`, `smoke-target`,
`pi-peer` describe a test's internals; a real roster holds `labkit-dev` and
`exo-ledger`. The e2e suite exists to be realistic, and a listing full of
`smoke-*` is not what anyone's bus looks like.
"""

from __future__ import annotations

import secrets

# Deliberately ordinary. These land in a roster, a log and sometimes a Claude
# session's conversation, and the point is that they look like something a
# person chose.
FIRST = (
    "amber", "brisk", "candid", "dapper", "eager", "fleet", "gentle", "hardy",
    "idle", "jolly", "keen", "lucid", "merry", "nimble", "opal", "prompt",
    "quiet", "rapid", "sunny", "tidy", "upbeat", "vivid", "warm", "zesty",
)
SECOND = (
    "otter", "heron", "badger", "marten", "falcon", "lynx", "puffin", "shrew",
    "tern", "vole", "wren", "auk", "ibis", "kite", "newt", "quail",
    "raven", "stoat", "teal", "weasel",
)


def mint_agent_name(prefix: str | None = None) -> str:
    """A name no other run will have used.

    The suffix is what guarantees that; the words are so a human reading a
    roster or a log can tell two of them apart at a glance.
    """
    name = f"{secrets.choice(FIRST)}-{secrets.choice(SECOND)}-{secrets.token_hex(2)}"
    return f"{prefix}-{name}" if prefix else name
