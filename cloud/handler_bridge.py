"""The `/bridge` transport: the mirror of the connector's tools, for our own
client.

Deliberately its own verbs, not the connector's with the meaning flipped by
role: a connector's `get_inbox` drains the inbox this fills, its `send_message`
fills the outbox this drains. These are transport ops between two pieces of our
own code, so they answer to what the bridge needs; the connector surface
answers to the bus's vocabulary. One set moving must not drag the other.

**`push` also relays, for a mutually paired address (#296).** Every other kind
here has an occupant on the *other* transport -- a human or an AI connected via
OAuth, reading the inbox this fills and writing the outbox this drains. A
`remote` bridge's counterpart is not that: it is another bridge, on another
machine, playing the identical self-referential role. So two addresses that
have each declared the other as their `pair` do not get their own inbox
written at all -- the write lands straight in the peer's outbox, addressed to
the bare name the pushing address stands for, which is exactly the local peer
name on the far machine. Unpaired, or paired one-sidedly, `push` behaves
exactly as it always has.
"""

from __future__ import annotations

import hmac
import json
import logging

import logs
from handler_base import Base
from store import INBOX, OUTBOX, Rejected, mutual_peer, queue

log = logging.getLogger(logs.LOGGER_NAME)

#: A bridge names its own address; the header is what makes that visible on
#: every access log line for free (see `handler_base.py`'s `redact()` -- the
#: body is deliberately the channel kept unreliable, not this).
ADDRESS_HEADER = "X-Agent-Bus-Address"


class BridgeOps(Base):
    def _secret_presented(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _bridge(self) -> None:
        """One op in, one JSON body out. See the module docstring for why the
        verbs are its own rather than the connector's.

        Authentication here is not the OAuth path: a bridge is us talking to
        ourselves, not a third-party client, and proves nothing but knowledge
        of this environment's own signing key -- the one static secret meant
        to cover every local bridge, present and future, with no per-address
        minting step ever. It names which address it is acting for directly,
        rather than having that baked into a token, because a shared secret
        cannot also carry an identity claim without becoming per-consumer
        again.
        """
        store, cfg = self.deps.store, self.deps.cfg
        secret = self._secret_presented()
        # `cfg.key` is the decoded signing key (`bytes.fromhex(...)`); a
        # bridge presents the same hex string an operator copied verbatim
        # out of Secret Manager, so `.hex()` is what reconstructs it, not
        # `.decode()` -- the raw bytes are not valid UTF-8 text.
        if not cfg or not secret or not hmac.compare_digest(secret, cfg.key.hex()):
            self._problem(401, "Unauthenticated", "no usable bearer secret was presented")
            return

        address = self.headers.get(ADDRESS_HEADER) or ""
        kind, _, name = address.partition(":")
        if not (kind and name):
            self._problem(400, "Missing address",
                          f"the {ADDRESS_HEADER} header must name kind:name")
            return

        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw or b"{}")
        except ValueError:
            self._problem(400, "Malformed request", "the body is not JSON")
            return

        # The address is the token's. There is no field to override it with,
        # which is why a bridge cannot ask to be someone else.
        inbox, outbox = queue(kind, name, INBOX), queue(kind, name, OUTBOX)
        op = body.get("op")
        self._intent = {"verb": op}
        if op in self.QUIET_OPS:
            self._log_level = logging.DEBUG
        try:
            if op == "push":
                # `to` is the token's address, not the body's. The bridge
                # never names the recipient of an inbound message -- the
                # queue already is the recipient -- so there is nothing here
                # to spoof.
                #
                # Unless a mutual pair says otherwise (#296): then there is no
                # occupant to fill this address's own inbox, and the write
                # belongs in the peer's outbox instead, addressed to the bare
                # name this address stands for -- the real local peer on the
                # peer's own machine.
                declared = store.get_pair(address)
                partner = mutual_peer(address, declared,
                                      store.get_pair(declared) if declared else None)
                if partner:
                    peer_kind, _, peer_name = partner.partition(":")
                    target, to = queue(peer_kind, peer_name, OUTBOX), address.partition(":")[2]
                else:
                    target, to = inbox, address
                message = {**(body.get("message") or {}), "to": to}
                mid = store.write(target, message)
                # The message id is the journey; the request trace above is
                # one hop within it. Both, not one -- see
                # docs/structured-logging.md.
                log.info("bridge push", extra={"trace_id": mid, "to": address,
                                               "relayed_to": partner})
                self._send(200, {"id": mid})
            elif op == "pull":
                msgs = store.read(outbox, unread_only=True)
                # A record either way. Logging only per message meant an
                # empty poll emitted nothing at all, so a bridge that had
                # stopped polling looked exactly like one that was healthy
                # and idle -- and a bridge polls every two minutes forever,
                # so "nothing waiting" is the overwhelmingly common case
                # and belongs at DEBUG.
                if msgs:
                    log.info("bridge pull", extra={"count": len(msgs),
                                                   "to": address})
                    for m in msgs:
                        log.info("bridge pull message",
                                 extra={"trace_id": m.get("id"), "to": m.get("to")})
                else:
                    log.debug("bridge pull", extra={"count": 0, "to": address})
                self._send(200, {"messages": msgs})
            elif op == "ack":
                ids = body.get("ids") or []
                acked = store.ack(outbox, ids)
                log.info("bridge ack", extra={"count": len(ids), "acked": acked,
                                              "to": address})
                for mid in ids:
                    log.debug("bridge ack message", extra={"trace_id": mid})
                self._send(200, {"acked": acked})
            elif op == "read":
                # Where a message got to, inside its lifetime. Both queues,
                # because *which one holds it* is the whole diagnostic:
                # unread in `inbox` means the connector has not looked;
                # unread in `outbox` means the bridge has not pulled it;
                # absent means delivered and expired, or it never arrived.
                #
                # No special case for a send-only peer -- a webhook's inbox
                # is simply empty, per the note in `store.py`.
                #
                # Does not consume. This is a query, and an operator asking
                # where a message went must not be the reason it stops
                # being redelivered.
                mid = body.get("message_id")
                if not isinstance(mid, str) or not mid:
                    self._problem(400, "Missing field", "read needs a message_id")
                    return
                found, where = None, None
                for name, q in (("inbox", inbox), ("outbox", outbox)):
                    found = store.read_one(q, mid)
                    if found is not None:
                        where = name
                        break
                log.info("bridge read", extra={"trace_id": mid, "to": address,
                                               "queue": where or "not found"})
                self._send(200, {"queue": where, "message": found})
            elif op == "roster":
                agents = body.get("agents") or []
                store.publish_roster(address, agents)
                # The liveness signal every `list_agents` answer rests on,
                # and it logged nothing at any level: an empty roster and a
                # bridge that stopped publishing were indistinguishable
                # from outside, which is the exact confusion `list_agents`
                # own empty-case message exists to explain.
                log.debug("bridge roster", extra={"count": len(agents),
                                                  "to": address})
                self._send(200, {"ok": True})
            elif op == "subscriptions":
                # Whole-map, single-writer (#249): `_join` already guarantees
                # one bridge per address, so there is never a concurrent writer
                # to race -- a write always replaces the stored map outright,
                # never merges into it. Presence of the key decides the
                # direction, not truthiness: `{"set": {}}` is a real write (the
                # last UNSUBSCRIBE emptied it), not a read.
                if "set" in body:
                    topics = body.get("set") or {}
                    store.set_subscriptions(address, topics)
                    log.info("bridge subscriptions set",
                             extra={"count": len(topics), "to": address})
                    self._send(200, {"ok": True})
                else:
                    topics = store.get_subscriptions(address)
                    log.info("bridge subscriptions get",
                             extra={"count": len(topics), "to": address})
                    self._send(200, {"topics": topics})
            elif op == "pair":
                # #296: declares, clears, or reads who this address relays
                # with. One-sided by itself -- `push` only honours it once the
                # peer has echoed it back (`mutual_peer`), so declaring a peer
                # that never reciprocates, or one that is simply wrong, does
                # nothing but sit here.
                if "peer" in body:
                    peer = body.get("peer")
                    if peer is not None and (
                        not isinstance(peer, str) or not all(peer.partition(":")[::2])
                    ):
                        self._problem(400, "Malformed peer",
                                      "peer must be a kind:name address, or null to clear")
                        return
                    if peer == address:
                        # A self-pair would trivially satisfy `mutual_peer`
                        # (it points back at itself by definition), which
                        # would make `push` feed this address's own outbox
                        # instead of its inbox -- a pointless loop, not a
                        # relay, and worth refusing rather than "supporting."
                        self._problem(400, "Malformed peer",
                                      "an address cannot pair with itself")
                        return
                    store.set_pair(address, peer)
                    log.info("bridge pair set", extra={"to": address, "peer": peer})
                    self._send(200, {"peer": peer})
                else:
                    peer = store.get_pair(address)
                    log.info("bridge pair get", extra={"to": address, "peer": peer})
                    self._send(200, {"peer": peer})
            else:
                self._problem(
                    400, "Unknown operation",
                    f"this server does not implement the `{op}` op. A newer "
                    "client against an older deployment reaches here.")
        except Rejected as e:
            log.warning(op or "bridge", extra={"verb": op, "ok": False,
                                               "reason": str(e)})
            self._problem(400, "Refused", str(e))
