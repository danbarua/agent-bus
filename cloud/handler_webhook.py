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
        # Which hook, from the first line, so every record on every path names
        # it -- including the ones that refuse before the event is known. The
        # verb becomes the event type once there is one.
        self._intent = {"verb": "webhook", "peer": name}
        length = int(self.headers.get("Content-Length") or 0)
        if length > webhooks.MAX_EVENT_BYTES:
            # Refused *before* reading -- the cap is what the store can hold,
            # and 413 is the one status a sender can act on.
            #
            # Which leaves the body unread on the socket, so the connection
            # cannot be reused: HTTP/1.1 keeps it open, the next request line
            # would be read out of the middle of a JSON payload, and the
            # resulting garbage is answered as if it were a request. Closing
            # is the only correct end to a message we declined to consume.
            # Set before answering: it governs whether the handler loops for
            # another request on this socket, and after the response has been
            # written is too late to change what happens next.
            self.close_connection = True
            self._problem(413, "Delivery too large",
                          f"{length} bytes; the limit is {webhooks.MAX_EVENT_BYTES}")
            return

        # Read before any other refusal. Every path below answers without the
        # body, and leaving it on the socket corrupts the *next* request on the
        # connection rather than this one -- a failure that surfaces nowhere
        # near its cause. Found as a stray log line naming a method of `?` and
        # the path of the request before it.
        body = self._body = self.rfile.read(length)

        secret = self.deps.webhook_secrets.get(name)
        if secret is None:
            # Not 401. An unconfigured name is not a failed authentication,
            # and telling the two apart is the whole point of the reasons
            # below -- an operator who sees 401 goes looking at the secret
            # they set, when the name never existed here.
            self._problem(404, "No such webhook", f"no webhook peer named {name!r}")
            return

        signature = self.headers.get(webhooks.SIGNATURE_HEADER) or ""
        why = webhooks.verify_github(body, signature, secret)
        if why != "ok":
            # The reason is logged and not returned. It tells an operator
            # whether the secret is unset or wrong -- different fixes -- while
            # the caller learns only that it failed.
            log.warning(f"{name}: signature rejected",
                        extra={"verb": "webhook", "ok": False,
                               "reason": why, "peer": name})
            self._problem(401, "Signature rejected",
                          "the delivery did not verify against the configured secret")
            return

        # GitHub offers two content types and only one of them is a payload.
        # `application/x-www-form-urlencoded` sends `payload=<urlencoded json>`,
        # which verifies its HMAC perfectly -- the signature covers whatever
        # bytes were sent -- and is then undecodable by the bridge minutes
        # later, in another process, as "event was not JSON". A misconfigured
        # hook would look like a working one from here and a broken one there.
        #
        # Refused rather than unwrapped. Unwrapping would make this component
        # normalise its input, which is the job the design puts downstream, and
        # it would accommodate a setting forever that wants fixing once. GitHub
        # shows this response in the delivery UI, which is exactly where
        # somebody can act on it.
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            log.warning(f"{name}: wrong content type",
                        extra={"verb": "webhook", "ok": False, "peer": name,
                               "reason": "content-type", "content_type": ctype})
            self._problem(415, "Wrong content type",
                          f"this endpoint takes application/json, not {ctype or 'nothing'}. "
                          "Set the hook's Content type to application/json.")
            return

        event = self.headers.get(webhooks.EVENT_HEADER) or ""
        delivery = self.headers.get(webhooks.DELIVERY_HEADER) or ""
        # The event *is* the verb here. `message` is the verb by the contract in
        # docs/structured-logging.md, and this path was setting none -- so every
        # delivery read `POST /webhook/github` in the summary column, and a
        # second line beside it read `webhook`. Two records per delivery,
        # neither saying which event had arrived.
        self._intent["verb"] = event or "unknown"
        # `ping` is GitHub proving the hook works when someone saves it. It
        # carries no event to deliver, and answering anything but 200 makes
        # the hook look broken in a UI at the moment it is being set up.
        if event == "ping":
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
            log.warning(f"{name}: {event} not accepted",
                        extra={"verb": event, "ok": False,
                               "peer": name, "reason": str(e)})
            self._problem(503, "Not accepted", str(e))
            return

        # No separate success line. The response record carries the verb, the
        # peer and now the id, so a second one would be the same facts twice --
        # and a delivery every few seconds is exactly where that costs.
        self._intent["trace_id"] = mid
        # What the delivery is about, from the payload it just verified.
        #
        # Reading four keys for a log line is not the cloud filtering on them:
        # #59 puts *routing* downstream, and it stays there. Without these a
        # record says a `pull_request` arrived and not which repository, which
        # number, or what happened to it -- and the payload is in Firestore,
        # where nobody reading a log is looking.
        self._intent.update(webhooks.about(body))
        self._send(202, {"ok": True, "id": mid})
