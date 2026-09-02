"""The OAuth flow: consent, registration, code redemption, token issue.

Every method here takes its `OAuthConfig` as a parameter rather than reading
it from the server. These endpoints cannot run without one, and the router is
what knows whether one exists -- so passing it is what makes that a fact the
type checker holds rather than an assumption repeated at each call site.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.parse

import logs
import oauth
from config import OAuthConfig
from handler_base import Base
from pages import CONSENT_PAGE

log = logging.getLogger(logs.LOGGER_NAME)


class OAuthFlow(Base):
    def _consent(self, cfg: OAuthConfig, params: dict[str, str],
                 refused: bool = False) -> None:
        """Render the form. Deliberately not the predecessor's single Allow
        button: that survived on hostname obscurity, and CT logs mean the
        hostname was never secret.

        **The same two checks the POST makes, in the same place.** They used
        to differ -- this rendered on the redirect_uri alone -- so anyone
        could produce a URL that showed this page naming any client id they
        liked. No code could be issued, but the page is the only thing a
        person is ever asked to trust, and one that renders for a client the
        server has never heard of teaches them to click through it.
        """
        store = self.deps.store
        address = (cfg.allowlist or {}).get(params.get("redirect_uri", ""), "")
        if not address or not store.client(params.get("client_id", "")):
            self._send_html(400, "<p>Unknown client, or redirect_uri is "
                                 "not permitted.</p>")
            return
        hidden = "\n".join(
            f'<input type=hidden name="{html.escape(k)}" value="{html.escape(v)}">'
            for k, v in params.items() if k != "passphrase")
        # Failing closed is right; failing silently is not. An unchanged
        # form cannot be told apart from a broken server, and the second
        # reading ends with someone abandoning a system that works. There
        # is nothing to leak: the client knows it submitted a passphrase.
        note = ("<p><strong>That was not the right passphrase.</strong> "
                "Nothing has been connected.</p>" if refused else "")
        self._send_html(200, note + CONSENT_PAGE.format(
            client_id=html.escape(params.get("client_id", "")),
            address=html.escape(address),
            redirect_uri=html.escape(params.get("redirect_uri", "")),
            hidden=hidden))

    def _register(self, cfg: OAuthConfig) -> None:
        store = self.deps.store
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = json.loads(raw or b"{}")
        except ValueError:
            self._send(400, {"error": "invalid_request"})
            return
        try:
            record = oauth.register_client(store, body,
                                           allowlist=list(cfg.allowlist))
        except oauth.Refused as e:
            # The reason goes to the log, not to the client: an error that
            # says which half was wrong tells a stranger what to try next.
            log.warning("register", extra={"verb": "register", "ok": False,
                                           "reason": str(e)})
            self._send(400, {"error": "invalid_redirect_uri"})
            return
        self._send(200, {"client_id": record["client_id"],
                         "redirect_uris": record["redirect_uris"],
                         "token_endpoint_auth_method": "none"})

    def _authorize(self, cfg: OAuthConfig) -> None:
        store, issuer = self.deps.store, self.deps.issuer
        form = self._form()
        redirect_uri = form.get("redirect_uri", "")
        # Kept alongside the identical check in `_consent`, not folded into
        # it: this is the path that mints a code, and a POST need never have
        # been preceded by a GET.
        address = (cfg.allowlist or {}).get(redirect_uri, "")
        if not address or not store.client(form.get("client_id", "")):
            self._send_html(400, "<p>Unknown client, or redirect_uri is "
                                 "not permitted.</p>")
            return
        if not oauth.consent_granted(form.get("passphrase"), cfg.passphrase):
            # Re-render rather than redirect. A refused consent must not
            # reach the callback at all -- no code, and nothing for a
            # watcher of the redirect to learn.
            log.warning("authorize", extra={"verb": "authorize", "ok": False,
                                            "reason": "passphrase",
                                            "client_id": form.get("client_id")})
            self._consent(cfg, {k: v for k, v in form.items()
                                if k != "passphrase"}, refused=True)
            return
        code = oauth.issue_code(
            store,
            client_id=form["client_id"],
            redirect_uri=redirect_uri,
            code_challenge=form.get("code_challenge", ""),
            resource=f"{issuer}/mcp",
            scope=form.get("scope") or "mcp",
        )
        query = {"code": code}
        if form.get("state"):
            # Round-tripped or the client aborts, and it is the client's
            # CSRF defence rather than ours to skip.
            query["state"] = form["state"]
        self._redirect(f"{redirect_uri}?{urllib.parse.urlencode(query)}")

    def _token(self, cfg: OAuthConfig) -> None:
        store = self.deps.store
        form = self._form()
        grant = form.get("grant_type")
        if grant == "refresh_token":
            claims = oauth.verify_token(form.get("refresh_token", ""), cfg.key)
            if not claims or claims.get("kind") != "refresh":
                self._send(400, {"error": "invalid_grant"})
                return
            self._issued(cfg, claims["address"], claims.get("client_id", ""))
            return
        if grant != "authorization_code":
            self._send(400, {"error": "unsupported_grant_type"})
            return
        try:
            record = oauth.redeem_code(
                store, code=form.get("code", ""),
                verifier=form.get("code_verifier", ""),
                redirect_uri=form.get("redirect_uri", ""),
                client_id=form.get("client_id", ""))
        except oauth.Refused as e:
            log.warning("token", extra={"verb": "token", "ok": False,
                                        "reason": str(e)})
            self._send(400, {"error": "invalid_grant"})
            return
        self._issued(cfg, cfg.allowlist[record["redirect_uri"]],
                     record["client_id"])

    def _issued(self, cfg: OAuthConfig, address: str, client_id: str) -> None:
        self._send(200, {
            "access_token": oauth.mint_access(address, cfg.key, client_id),
            "refresh_token": oauth.mint_refresh(address, cfg.key, client_id),
            "token_type": "Bearer",
            "expires_in": oauth.ACCESS_TTL_SECONDS,
            "scope": "mcp",
        })
