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
AGENT_BUS_CLOUD_ISSUER=https://agent-bus.framesift.ai PORT=8080 \
  python -c 'import sys; sys.path.insert(0,"."); import app, store; app.serve(store.Firestore())'
```

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
