"""The `/bridge` transport: the mirror of the connector's tools, for our own
client.

Deliberately its own verbs, not the connector's with the meaning flipped by
role: a connector's `get_inbox` drains the inbox this fills, its `send_message`
fills the outbox this drains. These are transport ops between two pieces of our
own code, so they answer to what the bridge needs; the connector surface
answers to the bus's vocabulary. One set moving must not drag the other.
"""

from __future__ import annotations

import json
import logging

import logs
import oauth
from handler_base import Base
from store import INBOX, OUTBOX, Rejected, queue

log = logging.getLogger(logs.LOGGER_NAME)


class BridgeOps(Base):
    def _token_presented(self) -> str:
        auth = self.headers.get("Authorization") or ""
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    def _bridge(self) -> None:
        """One op in, one JSON body out. See the module docstring for why the
        verbs are its own rather than the connector's."""
        store, cfg = self.deps.store, self.deps.cfg
        claims = oauth.verify_token(self._token_presented(), cfg.key) if cfg else None
        if not claims or claims.get("kind") != "access":
            self._problem(401, "Unauthenticated", "no usable bearer token was presented")
            return
        # A connector's token is valid and names the same address. Without
        # this it could push into its own inbox, forging mail that looks
        # like it came from the team.
        if claims.get("client_id") != "bridge":
            self._problem(403, "Not a bridge token",
                          "this endpoint is for a bridge, not a connector")
            return

        address = claims.get("address") or ""
        kind, _, name = address.partition(":")
        if not (kind and name):
            self._problem(403, "Token names no address",
                          "the token carries no peer address to act for")
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
                message = {**(body.get("message") or {}), "to": address}
                mid = store.write(inbox, message)
                # The message id is the journey; the request trace above is
                # one hop within it. Both, not one -- see
                # docs/structured-logging.md.
                log.info("bridge push", extra={"trace_id": mid, "to": address})
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
            else:
                self._problem(
                    400, "Unknown operation",
                    f"this server does not implement the `{op}` op. A newer "
                    "client against an older deployment reaches here.")
        except Rejected as e:
            log.warning(op or "bridge", extra={"verb": op, "ok": False,
                                               "reason": str(e)})
            self._problem(400, "Refused", str(e))
