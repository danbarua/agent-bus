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
| `/health` | for the deploy |

`/authorize`, `/token` and `/register` are advertised by those documents and are
not implemented here — the metadata is discovery and belongs to the skeleton,
the flows behind it are their own change.

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
