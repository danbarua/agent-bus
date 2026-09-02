"""Everything this server hands back as a document, rather than computes.

The five `.well-known` metadata documents and the three HTML pages, together
because they are the same kind of thing: fixed content served by exact path.

**`/.well-known/openid-configuration` must be 200, never 404.** ChatGPT
hard-aborts on a 404 there and does not fall back to RFC 8414 -- and a failed
discovery is cached client-side, producing *no server traffic* on retry. It is
the one mistake that cannot be iterated out of.
"""

from __future__ import annotations

from typing import Any


def metadata(issuer: str) -> dict[str, dict[str, Any]]:
    """The five documents a connector reads before it will talk to us.

    `openid-configuration` and `oauth-authorization-server` are served from the
    same structure on purpose: ChatGPT probes the OIDC path unconditionally and
    aborts on 404, and answering it with the RFC 8414 document is what stops
    that without pretending to be an OpenID Provider.
    """
    as_doc = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }
    prm = {
        "resource": f"{issuer}/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
    }
    return {
        "/.well-known/oauth-authorization-server": as_doc,
        "/.well-known/openid-configuration": as_doc,
        "/.well-known/oauth-protected-resource": prm,
        "/.well-known/oauth-protected-resource/mcp": prm,
        "/.well-known/jwks.json": {"keys": []},
    }


# A face for the hostname, and nothing more.
#
# Certificate transparency publishes the name the moment a cert issues, so
# anyone can find this address; what they should not get for free is who is
# behind it or what is on the other end. The page names no operator, no agent
# and no peer, and there is a test that keeps it that way.
#
# It also stops the bare domain being a 404, which reads as misconfigured
# rather than deliberate.
FRONT_PAGE = """<!doctype html>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=robots content="noindex,nofollow">
<title>agent-bus</title>
<style>
  :root { color-scheme: light dark; --ink: #1a1a1a; --bg: #fbfbfa; --dim: #6b6b6b; }
  @media (prefers-color-scheme: dark) {
    :root { --ink: #e8e6e3; --bg: #16161a; --dim: #8b8b8b; }
  }
  body { background: var(--bg); color: var(--ink); margin: 0;
         font: 16px/1.6 ui-sans-serif, system-ui, -apple-system, sans-serif;
         display: grid; place-items: center; min-height: 100vh; }
  main { max-width: 30rem; padding: 2rem; }
  h1 { font-size: 1.1rem; font-weight: 600; letter-spacing: .02em; margin: 0 0 .75rem; }
  p { color: var(--dim); margin: 0; }
  svg { display: block; margin-bottom: 1.25rem; }
</style>
<main>
  <svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
    <circle cx="20" cy="8" r="3.5" fill="currentColor"/>
    <circle cx="8" cy="30" r="3.5" fill="currentColor"/>
    <circle cx="32" cy="30" r="3.5" fill="currentColor"/>
    <path d="M20 11.5v9m0 0-9 6.5m9-6.5 9 6.5" stroke="currentColor"
          stroke-width="1.5" stroke-linecap="round" opacity=".55"/>
  </svg>
  <h1>agent-bus</h1>
  <p>A message endpoint. Nothing here is served to a browser.</p>
</main>
"""

FAVICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40">'
    '<circle cx="20" cy="8" r="4"/><circle cx="8" cy="30" r="4"/>'
    '<circle cx="32" cy="30" r="4"/></svg>'
)

# Exact paths to (content type, bytes). **A map, not a directory.**
#
# This is an OAuth server. Serving files by path is how `/../../etc/passwd`
# becomes a feature, and no amount of normalising is as safe as having no path
# handling at all: a request either names a key here or it is a 404. Adding an
# asset means adding an entry, which is the point.
STATIC: dict[str, tuple[str, bytes]] = {
    "/": ("text/html; charset=utf-8", FRONT_PAGE.encode()),
    "/favicon.svg": ("image/svg+xml", FAVICON.encode()),
    "/favicon.ico": ("image/svg+xml", FAVICON.encode()),
}


CONSENT_PAGE = """<!doctype html><meta charset=utf-8>
<title>agent-bus</title>
<h1>Connect this client to the bus?</h1>
<p>Client <code>{client_id}</code> wants to send and read messages as
<strong>{address}</strong>, with replies delivered to <code>{redirect_uri}</code>.</p>
<form method=post>
{hidden}
<label>Passphrase <input type=password name=passphrase autofocus></label>
<button type=submit>Allow</button>
</form>
<p><small>The hostname of this server is public: certificate transparency logs
are world-readable, so anyone can find it. The passphrase is what stops them
finishing this form.</small></p>"""
