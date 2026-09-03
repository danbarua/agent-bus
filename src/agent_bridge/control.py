"""The three things an agent can say *to* a webhook bridge.

    SUBSCRIBE danbarua/agent-bus:pr.merge
    UNSUBSCRIBE danbarua/agent-bus:pr.merge
    SUBSCRIPTIONS

**This is a peer reading its own mail, not a courier inspecting cargo.** The
distinction is what keeps the "not an AI secretary" rule intact (#59): these
messages are addressed *to* the bridge, and every peer on the bus reads its own
inbox. The rule binds what a bridge **carries**, and it carries none of these.

Case-insensitive on the verb, because #59 wrote `Subscribe` and #223 wrote
`SUBSCRIBE` and an agent composing one from a skill file will write whichever
it saw. The topic is matched exactly -- it is the key an `UNSUBSCRIBE` has to
hit later.
"""

from __future__ import annotations

from . import topics as topic_grammar
from .subscriptions import Subscriptions


def _listing(subs: Subscriptions, subscriber: str) -> str:
    """Always the full list, after every change.

    #223 specifies the reply as "that agent's active subscriptions" rather than
    an acknowledgement of the one thing that changed: an agent that has been
    compacted cannot otherwise tell what it is holding, and echoing the whole
    set makes every reply a status query as well as a confirmation.
    """
    held = subs.of(subscriber)
    if not held:
        return "No active subscriptions."
    return "Subscribed to:\n" + "\n".join(f"- {t}" for t in held)


def handle(text: str, subscriber: str, subs: Subscriptions) -> str | None:
    """The reply, or None when this is not a control message at all.

    None matters: a webhook bridge has no cloud inbox, so a message that is not
    a verb has nowhere to go. The caller decides what to say about that -- this
    function's job is to know a verb when it sees one.
    """
    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return None
    verb = parts[0].upper()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if verb == "SUBSCRIPTIONS":
        return _listing(subs, subscriber)

    if verb not in ("SUBSCRIBE", "UNSUBSCRIBE"):
        return None

    if not argument:
        return f"{verb} needs a topic, e.g. SUBSCRIBE owner/repo:pr.merge"
    if not topic_grammar.valid(argument):
        # Refused rather than stored. A topic that cannot match anything is a
        # subscription an agent believes it holds, and silent deafness is the
        # failure this whole surface exists to avoid.
        return (f"{argument!r} is not a topic. The form is "
                "owner/repo:selector, e.g. owner/repo:pr.merge.main")

    if verb == "SUBSCRIBE":
        subs.add(subscriber, argument)
    else:
        subs.remove(subscriber, argument)
    return _listing(subs, subscriber)
