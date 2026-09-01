#!/usr/bin/env python3
"""A stand-in for Claude Desktop, to exercise a deployed bus end to end.

Run against the real server. It does what a connector does and nothing else:
discovery, dynamic registration, the OAuth code flow with PKCE, then MCP over
HTTP -- `initialize`, `tools/list`, `tools/call`.

**It imports nothing from the server it is talking to.** Not `oauth`, not
`contract`, not `store`. A client that shared code with its server would agree
with it by construction and prove nothing; every value here is built from the
wire format alone, which is the only thing a real connector has.

Stdlib only, for the same reason the bridge is: the wire is the contract.

    # once -- prints a URL, wants the code you are redirected to
    python cloud/mock_connector.py auth --issuer https://bus.example.com

    # thereafter
    python cloud/mock_connector.py tools
    python cloud/mock_connector.py send --to claude-bus-dev --text "hello"
    python cloud/mock_connector.py inbox
    python cloud/mock_connector.py read <id>
    python cloud/mock_connector.py ack <id>
    python cloud/mock_connector.py agents

The token is cached in ~/.agent-bus/mock-connector.json (0600). It is a real
credential for a real deployment -- `auth --forget` removes it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

STATE = os.path.expanduser("~/.agent-bus/mock-connector.json")
# The value Claude Desktop actually redirects to. A connector never gets to
# choose this, which is why the server treats it as the thing that names the
# vendor rather than trusting a client's word.
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _req(url, data=None, headers=None, method=None):
    # S310: the URL is the issuer this tool was pointed at, not input.
    req = urllib.request.Request(url, data=data, headers=headers or {},  # noqa: S310
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(url, payload=None, token=None, form=False):
    headers = {}
    body = None
    if payload is not None:
        if form:
            body = urllib.parse.urlencode(payload).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, raw = _req(url, data=body, headers=headers)
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {"_raw": raw.decode(errors="replace")[:400]}


def _load() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ------------------------------------------------------------------ discovery

def discover(issuer: str) -> dict:
    """Read the metadata a connector reads, and check what one would check.

    Every URL naming the issuer is not pedantry: a document still advertising
    the `*.run.app` hostname is what strands a connector later, after it has
    cached them.
    """
    out = {}
    for doc in ("oauth-authorization-server", "openid-configuration",
                "oauth-protected-resource", "jwks.json"):
        status, body = _json(f"{issuer}/.well-known/{doc}")
        out[doc] = (status, body)
        print(f"  {status}  /.well-known/{doc}")
        if status != 200:
            raise SystemExit(f"discovery failed at {doc}: {status}")
        for url in json.dumps(body).split('"'):
            if url.startswith("https://") and not url.startswith(issuer):
                raise SystemExit(f"{doc} advertises {url}, not {issuer}")
    return out


# ----------------------------------------------------------------- the dance

def _resume(args) -> int:
    """Second half of the dance, after a human has been to the consent page.

    Split from the first half because a person stands between them: the code
    arrives minutes later, in another terminal. The PKCE verifier has to
    survive that gap or the exchange cannot be completed -- it is the whole
    point of PKCE that the code alone is not enough.
    """
    state = _load()
    pending = state.get("pending")
    if not pending:
        raise SystemExit("no authorization in progress: run `auth` first")
    landed = args.code.strip()
    code = landed
    if "?" in landed:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(landed).query).get("code", [""])[0]
    if not code:
        raise SystemExit(f"no ?code= in {landed!r}")

    print("exchanging the code:")
    status, tok = _json(f"{pending['issuer']}/token", {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": pending["verifier"],
        "redirect_uri": REDIRECT_URI,
        "client_id": pending["client_id"],
    }, form=True)
    if status != 200 or "access_token" not in tok:
        raise SystemExit(f"token exchange failed: {status} {tok}")
    print(f"  got an access token, expires_in {tok.get('expires_in')}")
    _save({"issuer": pending["issuer"], "client_id": pending["client_id"],
           "access_token": tok["access_token"],
           "refresh_token": tok.get("refresh_token", "")})
    print(f"saved to {STATE}")
    return 0


def cmd_auth(args) -> int:
    if args.code:
        return _resume(args)
    if args.forget:
        try:
            os.remove(STATE)
            print(f"removed {STATE}")
        except OSError:
            print("nothing to remove")
        return 0

    issuer = args.issuer.rstrip("/")
    print(f"discovery at {issuer}:")
    docs = discover(issuer)
    meta = docs["oauth-authorization-server"][1]

    print("\nregistering (DCR):")
    status, reg = _json(meta["registration_endpoint"],
                        {"client_name": "mock-claude-desktop",
                         "redirect_uris": [REDIRECT_URI]})
    if status != 200 or "client_id" not in reg:
        raise SystemExit(f"registration failed: {status} {reg}")
    print(f"  client_id {reg['client_id']}")

    verifier = _b64(secrets.token_bytes(48))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    query = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": reg["client_id"],
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": _b64(secrets.token_bytes(12)),
        "scope": "mcp",
    })
    url = f"{meta['authorization_endpoint']}?{query}"

    _save({"pending": {"issuer": issuer, "client_id": reg["client_id"],
                       "verifier": verifier}})

    print("\nOpen this, enter the passphrase, and copy the URL you land on.")
    print("claude.ai will 404 -- expected, and fine. The code is in the address")
    print("bar, and a code is single-use: if you reload that page it is spent.\n")
    print(f"  {url}\n")
    print("then, with the URL you landed on:\n")
    print("  python cloud/mock_connector.py auth --code '<the url>'")
    return 0


# --------------------------------------------------------------------- MCP

def _rpc(method: str, params: dict | None = None) -> dict:
    state = _load()
    if not state.get("access_token"):
        raise SystemExit(f"no token: run `auth` first (state: {STATE})")
    status, body = _json(f"{state['issuer']}/mcp",
                         {"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params or {}},
                         token=state["access_token"])
    if status == 401:
        raise SystemExit("401 -- the token has expired. Run `auth` again.")
    if "error" in body:
        raise SystemExit(f"{method} -> {body['error']}")
    return body.get("result", {})


def cmd_tools(_args) -> int:
    init = _rpc("initialize", {"protocolVersion": "2025-06-18",
                               "clientInfo": {"name": "mock-claude-desktop"}})
    print(f"server: {init['serverInfo']['name']} {init['serverInfo'].get('version','')}")
    print(f"capabilities: {sorted(init['capabilities'])}")
    # A connector calls these whether or not they were advertised. The whole
    # point of #71: a `Method not found` here hides the tools as well.
    for eager in ("resources/list", "prompts/list"):
        print(f"{eager}: {_rpc(eager)}")
    for t in _rpc("tools/list")["tools"]:
        print(f"  {t['name']:12} {t.get('description','')[:70]}")
    return 0


def _call(name: str, **arguments) -> str:
    res = _rpc("tools/call", {"name": name, "arguments": arguments})
    return "\n".join(c.get("text", "") for c in res.get("content", []))


def cmd_agents(_args) -> int:
    print(_call("list_agents"))
    return 0


def cmd_send(args) -> int:
    print(_call("send_message", to=args.to, text=args.text, summary=args.summary or ""))
    return 0


def cmd_inbox(_args) -> int:
    print(_call("get_inbox"))
    return 0


def cmd_read(args) -> int:
    print(_call("read_message", message_id=args.message_id))
    return 0


def cmd_ack(args) -> int:
    print(_call("ack_message", ids=args.ids))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mock_connector", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="discovery, DCR and the OAuth code flow")
    # No default: the deployment's hostname is not in this repository. Set
    # AGENT_BUS_CLOUD_ISSUER in your shell, or pass it once -- `auth` remembers
    # it in the saved state, so the other subcommands need it only that once.
    a.add_argument("--issuer", default=os.environ.get("AGENT_BUS_CLOUD_ISSUER"),
                   required=not os.environ.get("AGENT_BUS_CLOUD_ISSUER"))
    a.add_argument("--code", help="skip the prompt: the code, or the URL you landed on")
    a.add_argument("--forget", action="store_true", help="delete the saved token")
    a.set_defaults(fn=cmd_auth)

    sub.add_parser("tools", help="initialize, eager discovery, tools/list"
                   ).set_defaults(fn=cmd_tools)
    sub.add_parser("agents", help="who is on the bus").set_defaults(fn=cmd_agents)
    sub.add_parser("inbox", help="what is waiting: id, sender, summary"
                   ).set_defaults(fn=cmd_inbox)

    r = sub.add_parser("read", help="one message, whole, by id")
    r.add_argument("message_id")
    r.set_defaults(fn=cmd_read)

    w = sub.add_parser("send", help="send a message to a bus peer")
    w.add_argument("--to", required=True)
    w.add_argument("--text", required=True)
    w.add_argument("--summary")
    w.set_defaults(fn=cmd_send)

    k = sub.add_parser("ack", help="mark messages read")
    k.add_argument("ids", nargs="+")
    k.set_defaults(fn=cmd_ack)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
