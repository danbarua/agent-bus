"""`POST /webhook/<name>` -- the only door GitHub knocks on.

The trust boundary, and #59 puts it here because this is the one component
without a body parser: HMAC covers the bytes as sent, and nothing upstream of
a route has already turned them into a dict.

What it does is deliberately small -- verify, shape, write -- and what it does
not do is the design: no filtering, no summarising, no understanding of what a
`pull_request` event means. That work is the bridge's, so that filter rules
change without a deploy.
"""

from __future__ import annotations

import logging

import logs
import webhooks
from handler_base import Base
from store import OUTBOX, Rejected, queue

log = logging.getLogger(logs.LOGGER_NAME)


class WebhookIngress(Base):
    def _webhook(self, name: str) -> None:
        secret = self.deps.webhook_secrets.get(name)
        if secret is None:
            # Not 401. An unconfigured name is not a failed authentication,
            # and telling the two apart is the whole point of the reasons
            # below -- an operator who sees 401 goes looking at the secret
            # they set, when the name never existed here.
            self._problem(404, "No such webhook", f"no webhook peer named {name!r}")
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length > webhooks.MAX_EVENT_BYTES:
            # Refused before reading. The cap is what the *store* can hold, not
            # what GitHub will send, so this is a real answer rather than an
            # error -- and 413 is the one status a sender can act on.
            self._problem(413, "Delivery too large",
                          f"{length} bytes; the limit is {webhooks.MAX_EVENT_BYTES}")
            return
        body = self.rfile.read(length)

        signature = self.headers.get(webhooks.SIGNATURE_HEADER) or ""
        why = webhooks.verify_github(body, signature, secret)
        if why != "ok":
            # The reason is logged and not returned. It tells an operator
            # whether the secret is unset or wrong -- different fixes -- while
            # the caller learns only that it failed.
            log.warning("webhook", extra={"verb": "webhook", "ok": False,
                                          "reason": why, "peer": name})
            self._problem(401, "Signature rejected",
                          "the delivery did not verify against the configured secret")
            return

        event = self.headers.get(webhooks.EVENT_HEADER) or ""
        delivery = self.headers.get(webhooks.DELIVERY_HEADER) or ""
        # `ping` is GitHub proving the hook works when someone saves it. It
        # carries no event to deliver, and answering anything but 200 makes
        # the hook look broken in a UI at the moment it is being set up.
        if event == "ping":
            log.info("webhook", extra={"verb": "webhook", "peer": name, "event": "ping"})
            self._send(200, {"ok": True, "pong": True})
            return

        address = f"webhook:{name}"
        try:
            mid = self.deps.store.write(
                queue("webhook", name, OUTBOX),
                webhooks.as_message(address, event, delivery, body))
        except Rejected as e:
            # 503, not 400: nothing about the delivery is wrong. The queue is
            # full or the store refused, both of which are ours to fix, and
            # GitHub's redelivery is the right response to a 5xx.
            log.warning("webhook", extra={"verb": "webhook", "ok": False,
                                          "peer": name, "event": event,
                                          "reason": str(e)})
            self._problem(503, "Not accepted", str(e))
            return

        log.info("webhook", extra={"verb": "webhook", "peer": name, "event": event,
                                   "trace_id": mid})
        self._send(202, {"ok": True, "id": mid})
