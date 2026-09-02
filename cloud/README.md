# cloud

The remote MCP surface desktop peers reach over public HTTPS. Deployed, never
published: it is not in the `agent-bus-team` wheel and it does not import
`agent_bus`.

```sh
# tests that need nothing
cd cloud && uv run --with pytest python -m pytest tests -q

# all of them, including the store
gcloud emulators firestore start --host-port=127.0.0.1:8080 &
cd cloud && FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 \
  uv run --with pytest python -m pytest tests -q
```

Emulator tests skip with the start command in the skip reason, so a run that
tested no store says so rather than looking green.

```sh
# run it
AGENT_BUS_CLOUD_ISSUER=https://bus.example.com \
AGENT_BUS_CLOUD_SIGNING_KEY=$(openssl rand -hex 32) \
PORT=8080 \
  python -c 'import sys; sys.path.insert(0,"."); import app, store; app.main(store.Firestore)'
```

## Configuration

| | |
|---|---|
| `AGENT_BUS_CLOUD_ISSUER` | **required.** The OAuth issuer and the base of every URL a connector caches. It must not move after one registers |
| `AGENT_BUS_CLOUD_SIGNING_KEY` | **required.** 32+ bytes of hex, `openssl rand -hex 32` |
| `AGENT_BUS_CLOUD_ALLOWLIST` | JSON, redirect URI → peer address. Empty is a valid bridge-only deployment |
| `AGENT_BUS_CLOUD_PASSPHRASE` | required *once a connector is allowlisted*: the human half of the consent gate |
| `PORT` | Cloud Run sets it. Defaults to 8080 |
| `GOOGLE_CLOUD_PROJECT` | only for the log trace field, so app logs nest under the request they belong to. Absent, logging still works and the field is omitted |
| `AGENT_BUS_CLOUD_VERSION` | the running build, reported by `/health` and MCP `serverInfo`. **Set by the image, not by you** — `cloud/Dockerfile` bakes in the git tag the deploy built, so it cannot drift from what is running. A local build with no `--build-arg VERSION=` reports `0+unknown`, which is honest |
| `AGENT_BUS_CLOUD_DATABASE` | which Firestore database to use. Production's is `(default)`; staging sets `staging`, because a staging service sharing the database would be a second front end onto production's records rather than an environment |
| `AGENT_BUS_CLOUD_LOG_LEVEL` | `INFO` unset. `DEBUG` adds the quiet records — an empty bridge poll, a roster publish — which are frequent enough to drown the log if they were INFO, and are the ones that distinguish a healthy idle bridge from one that stopped. An unparseable value is INFO rather than an error: a typo in a deploy must not take logging down to nothing |

## Errors

Our own endpoints — `/bridge`, and routing 404/405 — answer in **RFC 7807
`application/problem+json`**: `type`, `title`, `status`, `detail`, `instance`.
`title` is the class of thing that went wrong; `detail` is what went wrong this
time, and a client rendering `detail or title` is never left holding a bare
status code.

**The OAuth endpoints and `/mcp` do not.** RFC 6749 §5.2 mandates
`{"error": ...}` on the token endpoint, RFC 7591 §3.2.2 the same for
registration, and JSON-RPC owns its own envelope. A connector parses those by
their specs — rewriting them as problem+json would be a spec violation dressed
up as consistency, and it would break at the one moment nobody is watching:
someone re-adding a connector. There is a test for each.

There is no fallback to the older `{"error": ...}` shape on our side. Both ends
are ours and ship together.

**The server refuses to start rather than serve a surface that authenticates
nobody.** That is the failure worth engineering against: `/health` answers,
discovery answers, and only a connector attempting a tool call ever finds out.
It looks like a healthy deployment for exactly as long as nobody uses it.

The passphrase is required only when the flow it gates is reachable. A bridge
token is minted out of band and never sees the consent page, so a bridge-only
deployment needs none — and demanding one there would be a prerequisite that
buys nothing.

## Reading the logs

One JSON object per line on stdout, with `severity` — the exact key Cloud
Logging reads, and a line without it is INFO forever however loudly it was
logged.

**The values are fields, not a sentence.** `message` is the verb; `status`,
`path`, `http_method`, `verb` and the redacted `headers` are their own keys, so
"everything that was not a 200" is a comparison rather than a text match. Every
record also carries `version` — the build that wrote it.

`verb` is what the caller asked for, and it is the field to filter on: every
call is `POST /bridge` or `POST /mcp`, so the HTTP method separates nothing. A
bridge polls every two minutes forever, which makes `pull` most of all traffic:

```sh
# what the bridge actually did, minus the polling
gcloud logging read 'resource.type=cloud_run_revision
  AND jsonPayload.verb!="pull" AND jsonPayload.status>=200' \
  --project agent-bus-cloud --limit 20

# anything that was not a success
gcloud logging read 'resource.type=cloud_run_revision AND jsonPayload.status>=400' \
  --project agent-bus-cloud --limit 20
```

Every record made during a request carries
`logging.googleapis.com/trace`, built from the `X-Cloud-Trace-Context` header
Cloud Run sends, so the console nests app logs under the request entry.

```sh
gcloud logging read 'resource.type=cloud_run_revision AND jsonPayload.verb="tools/call"' \
  --project agent-bus-cloud --limit 20 --format='value(timestamp,jsonPayload.tool)'
```

**A verb we do not implement is logged too, at WARNING.** `send_error` is the
stdlib's own path and never passes through ours, so a `HEAD` — anything without
a `do_*` — used to be answered 501 and recorded nowhere. In Cloud Run's request
log that is indistinguishable from a 501 the front end produced without the
container ever being asked. Two of those arrived from a scanner on 2026-08-27
and could not be attributed either way. The record carries the user-agent,
which is the only thing on it that says what was calling.

**Headers are an allowlist, not a denylist** — `content-type`,
`content-length`, `user-agent`, `accept` are logged in full and everything else
reads `<redacted>`. A denylist forgets the header someone adds next year, and
these logs get pasted somewhere during exactly the kind of incident where that
matters.

## What it serves

| | |
|---|---|
| `POST /mcp` | the only JSON-RPC endpoint. `GET`/`DELETE` are 405 |
| `/.well-known/oauth-authorization-server` | RFC 8414 |
| `/.well-known/openid-configuration` | the same document. ChatGPT probes it unconditionally and **hard-aborts on 404** |
| `/.well-known/oauth-protected-resource` | RFC 9728, plus the `/mcp` resource-specific alias |
| `/.well-known/jwks.json` | empty, and present |
| `/register`, `/authorize`, `/token` | the flows those documents advertise: DCR, PKCE S256, single-use codes |
| `POST /bridge` | the bridge's own endpoint. Not MCP, and not for connectors |
| `GET /`, `/favicon.svg` | a face for the hostname. A fixed map of embedded assets, never a directory |
| `/health` | for the deploy |

## The bridge does not use the connector's tools

A bridge's operations are the *mirror* of a connector's: the connector's `read`
drains the inbox the bridge fills, and its `write` fills the outbox the bridge
drains. Reusing the four tools with their meaning flipped by role would make a
frozen surface depend on what the bridge needs next — which is the one thing
freezing it was meant to prevent. So `/bridge` is separate, and takes
`{"op": "push"|"pull"|"ack"|"roster", ...}`.

It is gated on `client_id == "bridge"`: a connector's own access token is valid,
names the same address, and is refused here with 403. Without that check it
could push into its own inbox and forge mail that looks like it came from the
team.

**The address is the token's, and so is `to`.** There is no field on the request
to override either with, which is why a bridge cannot ask to be someone else.

**A bridge token carries `iss`, and that is how the bridge finds the server.**
One artifact to install — `~/.agent-bus/cloud-token`, 0600 — instead of a token
plus a URL beside it that can drift apart. The bridge reads the claim without
verifying the signature, deliberately: it is the user's own config file, not
network input, and the server verifies for real at connect.

## Driving a deployment like a connector

`mock_connector.py` is a stand-in for Claude Desktop. It does what a connector
does and nothing else — discovery, dynamic registration, the OAuth code flow
with PKCE, then MCP over HTTP.

```sh
# once. Prints a URL; a human enters the consent passphrase and is redirected
# to a claude.ai 404 with the code in the address bar.
python cloud/mock_connector.py auth
python cloud/mock_connector.py auth --code '<the url you landed on>'

python cloud/mock_connector.py tools                       # initialize, eager discovery, tools/list
python cloud/mock_connector.py agents                      # who is on the bus
python cloud/mock_connector.py write --to some-agent --text "hello"
python cloud/mock_connector.py read
python cloud/mock_connector.py ack <id>
```

**It imports nothing from the server it talks to** — not `oauth`, not
`contract`, not `store`, and not `agent_bus`. A client that shared code with
its server would agree with it by construction and prove nothing; every value
is built from the wire format alone, because that is all a real connector has.
There is a test that reads the source and enforces it.

The token lands in `~/.agent-bus/mock-connector.json` (0600). It is a real
credential for a real deployment: `auth --forget` removes it.

## The front page

Certificate transparency publishes the hostname the moment a cert issues, so
anyone can find the address. What they should not get for free is **who is
behind it** — the page names no operator, no agent and no peer, and a test
keeps it that way.

Assets are a `dict` of exact paths to bytes, **not a directory**. This is an
OAuth server: serving files by path is how `/../../etc/passwd` becomes a
feature, and no amount of normalising is as safe as having no path handling at
all. Adding an image means adding an entry, which is the point. There is a test
that walks a handful of traversal shapes and expects 404 from every one.

## Two things that look wrong and are not

**Discovery answers without a token; only `tools/call` is gated.** A connector
pings `initialize` and `tools/list` before it ever attaches `Authorization`.
Gating uniformly means discovery 401s and **no tool is visible at all**, whether
or not auth works. Discovery exposes schemas; mailboxes are only reachable
through `tools/call`.

**`resources/list` and `prompts/list` return empties rather than an error.**
Some clients call them unconditionally, not gated on advertised capabilities,
and a `Method not found` there killed tool discovery entirely in the
predecessor. Both capabilities are declared for the same reason.

Both were found by watching a real connector fail. See
`docs/harnesses/` for the same discipline applied to coding harnesses.
