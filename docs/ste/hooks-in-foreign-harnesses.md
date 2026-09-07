# Hooks in Foreign Harnesses

Status: resolved 2026-08-24.

## Deleted hooks and shim

The `hooks/session-start` and `hooks/session-end` shims are deleted. `scripts/agent-bus` is deleted. These existed only to run `agent-bus` from Grok's Bash tool. The MCP server serves that purpose instead.

`agent-bus mcp` calls `session_start()` on startup and `session_end()` on exit. It runs in-process, using the harness's own environment. It registers the session and publishes the listener. It uses no bash, no stdin pipe, no exit code, and no plugin-root search.

## Design assumption

A hook can be discovered, imported, enabled, and run by a foreign harness. This can happen without a person choosing to install it there. So a hook is authored to run automatically. It must work in an unknown runtime, under an unknown invocation convention, with no installation step.

## The hook entrypoint

`agent-bus hook session-start|session-end` remains, for a harness with hooks and no MCP. The entrypoint is `python -m agent_bus hook <event>`.

Two invariants hold for this entrypoint:

1. Never claim an identity you cannot prove.
2. Never fail or stall the host: exit 0, diagnostics to stderr, no blocking read.

A test in `../tests/agent_bus/grok/test_hook_entrypoint.py` pins each invariant.

`_hook_payload()` reads stdin with a `select` deadline. It gives up if no input arrives within that deadline. This keeps a blocked or unclosed pipe from hanging the host. `agent-bus` passes `stdin=subprocess.DEVNULL` when it spawns its own listener.

## Core and adapters

Core takes an explicit descriptor: kind, session id, pid, cwd. It returns a result. Core does not read the environment. Core does not touch argv, stdout, or an exit code. Core raises no exception. Core names no vendor.

Detection selects the adapter for each harness. Each adapter owns what varies for its harness. The payload can arrive by argv, stdin, or env. Stdout has an adapter-specific meaning. An exit code has an adapter-specific meaning. One adapter exists per known harness.

A fallback adapter handles the unknown case. It performs no read that can block. It writes no output to stdout. It exits with code 0. It registers the agent as kind `other`.

## Vendor-specific code in core

| Location | Current state |
|---|---|
| `lifecycle.detect_kind` | Asks each adapter's `detect()`. |
| `lifecycle.host_pid` | Delegates to the adapter. Falls back to `getppid()`. |
| `lifecycle.session_start` | Takes a `SessionDescriptor`. |
| `listener.start_uds_listen` | Writes into `~/.claude/sessions/`. This is transport, not core. |
| `hooks/session-start`, `session-end` | Deleted. |
| `scripts/agent-bus` | Deleted. |

## The listener's role in the send path

An outbound `send_peer_message` frame carries `"from": "uds:<our_sock>"` as its return address. The recipient dials that socket back with `peer_message_status`. A peer with no listener has no address to be acked at. `send_peer_message` tries four strategies to resolve a socket of its own before it sends. It refuses to send if none of the four answer. The listener is the return path of the bus.

By default, a peer joins the bus and publishes a listener as part of joining.

## Identity across start and end

`session_start` and `session_end` each re-derive the pid. Both fall back to `getppid()`. A harness that runs the two hooks from different processes can make `session_end` call `unregister_by_pid` on a guessed pid. That call can remove a live roster entry that belongs to a different agent. `agent-bus` already writes `listeners/<host_pid>.pid` at start. Reading that value back at end, instead of re-deriving it, would close this gap.

## Remaining wiring gap

As of 2026-08-24, a fresh Grok install connects no hook or MCP server automatically. There is no `.mcp.json`, so Grok does not start `agent-bus mcp` on its own. That wiring is a separate, remaining task.
