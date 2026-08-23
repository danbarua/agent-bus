# Authoring hooks for a harness that never installed them

Status: review, not yet implemented. Written 2026-08-23 against `09e8267`.

## The assumption

A hook may be discovered, imported, enabled, and executed by a foreign harness
without the user consciously installing it there. So a hook must be authored as
though it will run automatically, in an unknown runtime, under an unknown
invocation convention, with no installation ceremony.

Everything below follows from taking that literally. It is a description of what
the current hook does under that assumption, not a plan with a schedule.

## What happens today

Run `hooks/session-start` with no `GROK_*` and no `CLAUDE_*` in the environment,
every path redirected to a temp dir — that is, exactly the foreign-harness case:

```
registered id=0c48… name=grok-82801        kind=grok
listeners/82801.pid                        detached process spawned
sessions/82831.json + 82831.….key          published as a Claude teammate
stdout: {"hookSpecificOutput": {"hookEventName": "SessionStart", …}}
```

Nothing in that environment was Grok. `82801` was the invoking shell, via
`getppid()`.

### It claims an identity it cannot prove

`hooks/session-start` reasons that "these hooks ship only in the Grok plugin, so
a bare invocation is Grok", and exports `GROK_PLUGIN_ROOT` so that `detect_kind`
agrees. Under the assumption that reasoning is inverted: a bare invocation is
the one case we can be certain is *not* a deliberate Grok install.

The forged kind then steers `host_pid()` (now `lifecycle.py`) into the Grok
branch, which looks up a session id it does not have and falls through to
`os.getppid()`. A foreign harness is registered under a Grok identity at a
guessed pid.

Absent positive evidence the kind is `other`, which costs nothing: since #6
every non-Claude kind gets a listener, so `other` is fully functional.

### It can hang the host

`_hook_payload()` calls `sys.stdin.read()` whenever stdin is not a tty. Given a
pipe the harness opens and does not close, that blocks forever — verified with a
fifo, `timeout 6` returns 124. A hook that hangs is worse than one that fails,
and the hazard is already known here: we pass `stdin=subprocess.DEVNULL` when
spawning our own listener.

### It can fail the host

`cmd_hook` returns 1 when `session_start` raises, and the shim runs
`set -euo pipefail` with `exec`. We do not know what an unknown harness does
with a non-zero session-start exit; in some harnesses a hook exit code is a
control signal. A messaging bus must never be able to stop a session starting.

### It guesses at someone else's protocol

stdout carries Claude Code's `hookSpecificOutput` envelope *and* a duplicate
top-level `additionalContext` — a shotgun fired at two schemas. In an unknown
harness stdout may be ignored, parsed against a different schema, or injected
verbatim into a model's context. The right instinct (compatibility) in the wrong
form (guessing rather than detecting).

### Its identity is not durable across start and end

Both hooks re-derive the pid, and both fall back to `getppid()`. If the harness
runs them from different processes, `session_end` calls `unregister_by_pid` on a
pid it guessed, which is a cross-agent write that can remove a live entry
belonging to someone else. We already write `listeners/<host_pid>.pid` at start;
identity should be read back from what we wrote, not derived twice.

## What is not a defect: the listener

An earlier draft of this review proposed gating the UDS listener behind an
opt-in, on the grounds that registering on the file bus is inert while
publishing a socket writes into another agent's namespace.

That is wrong, and worth recording so it is not proposed again. There is no dual
bus. An outbound `send_peer_message` frame carries `"from": "uds:<our_sock>"` as
its return address (`uds.py:571`), and the recipient dials that socket back with
`peer_message_status`. A peer with no listener therefore has no address at which
to be acked, and `send_peer_message` gives up before connecting — it tries three
strategies to resolve a socket of its own first (`uds.py:468`). The listener is
the return path of a single bus, not a way of advertising yourself.

So the listener is not an optional side effect that could be deferred; it is
half of the send. The genuine question is only whether a peer joins the bus at
all, and the answer is yes, by default.

## Shape of the fix

Stable core, thin adapters, and an adapter for the unknown case.

**Core** takes an explicit descriptor — kind, session id, pid, cwd — and returns
a result. It never sniffs the environment, never touches argv, stdout or exit
codes, never raises, and names no vendor. `session_start` is close to this
already.

**Adapters** are selected by detection, never by assumption, and each owns the
things that vary: how the payload arrives (argv, stdin, env), what stdout means,
what an exit code means. One per known harness, plus a fallback whose contract
is the conservative one — read nothing that can block, write nothing to stdout,
exit 0 always, register as `other`.

**The entrypoint** is `python -m agent_bus hook <event>`. A bash file cannot
satisfy the "imported" half of the assumption, so the shim is kept only for
harnesses that require an executable file, reduced to a dumb `exec … || exit 0`
with no identity logic in it.

Two invariants hold everywhere:

1. Never claim an identity you cannot prove.
2. Never fail or stall the host — exit 0, diagnostics to stderr, no blocking read.

## Vendor names currently in core

These are the seams the split has to cut, listed so the work is not
archaeology:

| location | what is vendor-specific |
| --- | --- |
| `lifecycle.detect_kind` | ~~hardcodes both vendors' env vars~~ now asks each adapter's `detect()` |
| `lifecycle.host_pid` | ~~two vendor branches~~ now delegates to the adapter, `getppid()` as fallback |
| `lifecycle.session_start` | ~~imports `_session_title` from `adapters.grok`~~ now takes a `SessionDescriptor` |
| `listener.start_uds_listen` | still writes into `~/.claude/sessions/`; transport, not core |
| `hooks/session-start`, `session-end` | assume a bare invocation is Grok |
| `scripts/agent-bus` | searches `~/.grok/installed-plugins` |

## Unrelated, but adjacent

`hooks/hooks.json` declares no events, and there is no `.mcp.json`, so none of
the above runs today and a fresh Grok install registers nothing at all. The
wiring exists on `feat/grok-claude-plugins`. Fixing the hooks before they are
re-enabled is the cheaper order.
