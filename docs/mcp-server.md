# The MCP server

`agent-bus mcp` is a stdio JSON-RPC server (`src/agent_bus/mcp_server.py`).
This is a reference for its protocol surface -- what it declares, what it
answers, what it sends unprompted -- not for any particular client.

## Tools

`register`, `set_status`, `list_agents`, `send_message`, `get_inbox`,
`read_message`, `ack_message`, `self`. Each is argument-shaping over the
same functions the CLI calls, in `commands/`.

`register`'s `kind` names the harness/transport, not the model answering --
`claude` specifically means this process is the native Claude Code CLI with
its own delivery socket, and a `kind=claude` claim that contradicts what
`initialize`'s own `clientInfo` identified (omp, codex, grok) is rejected
rather than accepted into an unreachable registration.

## Resources

Declared via `capabilities.resources.subscribe = true` on `initialize`.
`resources/list` returns two:

- `agentbus://inbox` -- unread mail addressed to this connection's own
  identity. Scoped per connection: each stdio process sees only its own
  inbox. `resources/read` returns a notice per message (from/id/summary,
  not the body) -- fetch the full text afterward with the `read_message`
  tool, by the id the notice names.
- `agentbus://roster` -- every agent currently on the bus, the same list
  the `list_agents` tool returns. Not scoped to the connection: every
  subscriber sees the same feed. `resources/read` returns the full list
  directly; there is no per-entry follow-up tool the way there is for the
  inbox, because the roster already is the payload.

`resources/subscribe` / `resources/unsubscribe` take a `uri` and enable or
disable `notifications/resources/updated` for that resource, independently
of the other -- subscribing to one has no effect on the other's state. The
notification itself carries only the URI, never content; a subscriber
re-reads the resource to see what changed.

Roster notifications are muted by default
(`mcp_server.ROSTER_NOTIFICATIONS_ENABLED`). Subscribing to
`agentbus://roster` still succeeds, but no notification is ever sent for
it -- a roster churns on every peer's join/rename/leave, not just mail
addressed to this connection, and a client that auto-subscribes to
everything a server advertises as subscribable turned that into a
notification per peer event. `resources/read` on the roster is unaffected
and still returns the live list. Inbox notifications are unaffected too.

Change detection is native per-platform directory watching
(`src/agent_bus/fswatch.py`: `select.kqueue()` on macOS/BSD, `inotify` via
`ctypes` on Linux, stdin-only elsewhere), not polling on a timer. One
`Waiter` can watch several directories at once, so a connection subscribed
to both resources still uses a single watcher. A 30-second safety net
rechecks both currently-subscribed resources regardless of whether a
directory event fired, covering an event that was somehow missed.

The roster resource's change detection is scoped to the *registered*
roster -- entries this server itself wrote to disk via `register()` --
not the full discovery-merged view `resources/read` returns. A peer known
only through discovery (never itself connected to `agent-bus mcp`) has no
local file here to watch, so its arrival or departure is caught only by
the 30-second safety net, not the directory event.

## Server-initiated requests

The server can also send a request the client did not ask for. There is
one today: `roots/list`, sent once `notifications/initialized` arrives,
and only if the client's own `initialize` declared `capabilities.roots`.
The answer -- the client's own project root -- replaces a bare pid-derived
peer name (`omp-58935`) with a project-scoped one (`omp-agent-bus`),
without a manual `agent-bus register` call. A name a human, or a test,
has already claimed is never overwritten.

## Client requirements

None of this needs anything beyond implementing the relevant part of the
MCP spec. A client that declares `capabilities.roots` gets named by
project; a client with resource-subscription support and
`notifications/resources/updated` handling gets live inbox and roster
push. Some agent harnesses, such as omp, implement resource notifications
and `roots/list` already -- see `docs/harnesses/omp.md` for what that
looks like running live.
