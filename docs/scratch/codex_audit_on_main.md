# Current-source report

[VERIFIED] This report is against clean `main` at commit `197a501704c69e76a1b17672e8ea00619d78aea3`. Python imports resolved to this checkout’s `src/agent_bus`.

[VERIFIED] I ran 275 targeted tests: 50 identity/UDS/routing tests and 225 CLI/MCP/lifecycle/store tests; all passed.

[VERIFIED] I also ran isolated wire-level exercises using real Unix sockets, detached listeners, roster files, inbox files, CLI commands, and MCP dispatch. I did not run the spendy live-agent suite.

[UNCONFIRMED] The installed Claude Code is `2.1.251`; the UDS docs claim verification against `2.1.239`. I did not run a paid live Claude↔Claude exchange, so claims about what current Claude does after accepting these bytes remain unconfirmed. The repository’s own side of the wire is verified below.

A command decorated with `@logged` first enters [`log.logged.wrapper`](../../src/agent_bus/log.py:283); the chains below name the underlying project calls and omit only routine stdlib serialization/filesystem calls unless they affect delivery.

## 1. Identity

### Lifecycle registration

[TRACED] MCP startup follows:

```text
cli.main
→ mcp_server.main
→ mcp_server.serve
→ mcp_server._startup_identity
→ lifecycle.describe
→ lifecycle.detect_kind
→ each lifecycle adapter.detect
→ lifecycle.host_pid
→ adapter.session_id / adapter.host_pid
→ adapter.workspace / adapter.session_name or lifecycle.derive_name
→ lifecycle.session_start
→ store.register
→ store.prune_dead_roster
→ store.load_roster
→ process.is_process_alive
→ protocol.new_id, if this PID has no live entry
→ store.save_roster_entry
```

The relevant source begins at [`mcp_server.serve`](../../src/agent_bus/mcp_server.py:496), [`lifecycle.describe`](../../src/agent_bus/lifecycle.py:92), [`lifecycle.session_start`](../../src/agent_bus/lifecycle.py:116), and [`store.register`](../../src/agent_bus/store.py:272).

[TRACED] Only Grok and Claude have environment lifecycle adapters. Grok wins the first-match loop; otherwise Claude; otherwise the fallback is `other`. Claude detection requires `CLAUDE_PLUGIN_ROOT` or `CLAUDE_PROJECT_DIR`; Grok requires `GROK_HOOK_EVENT` or `GROK_PLUGIN_ROOT`. See [`adapters/lifecycle/__init__.py:14`](../../src/agent_bus/adapters/lifecycle/__init__.py:14), [`claude.py:14`](../../src/agent_bus/adapters/lifecycle/claude.py:14), and [`grok.py:18`](../../src/agent_bus/adapters/lifecycle/grok.py:18).

[TRACED] Before an MCP client identifies itself, an unrecognised environment is changed from `other` to `pending`, named `pending-<pid>`. On `initialize`:

```text
serve
→ _read_stdio_message
→ handle_rpc
→ _dispatch
→ _adopt_identity_from_client
→ store.get_self
→ identify_mcp_client
→ _better_name
→ commands.agents.register
→ resolve_host_pid
→ store.register
→ listener.rename_uds_listen
→ listener._patch_published_session
```

Codex, OMP and Grok are recognised from `clientInfo.name`; unknown clients settle as `other`. See [`_adopt_identity_from_client`](../../src/agent_bus/mcp_server.py:245) and [`identify_mcp_client`](../../src/agent_bus/adapters/lifecycle/__init__.py:28).

### What registration creates

[TRACED] A new registration gets a bare UUID from `uuid4`; parsed later, a bare ID is a `bus` address. Its liveness is the registered process, guarded by PID plus recorded process start time. It also gets an inbox path derived from that UUID. See [`store.register`](../../src/agent_bus/store.py:326), [`address.parse`](../../src/agent_bus/address.py:70), and [`addressing/bus.py`](../../src/agent_bus/adapters/addressing/bus.py:19).

[TRACED] Re-registering the same live PID preserves the UUID and updates name, kind, cwd, native fields and aliases. A name collision with another live entry becomes `name-2`, then `name-3`, etc. A restarted process with a new PID gets a new UUID even when it reuses the same name. See [`store.py:293-344`](../../src/agent_bus/store.py:293).

[TRACED] If lifecycle knows a harness session ID, it adds the canonical alias `<kind>:session:<session-id>` while retaining the UUID as the primary ID. See [`lifecycle.py:127-146`](../../src/agent_bus/lifecycle.py:127).

### Discovery is separate from registration

[TRACED] Discovery is read-only and creates in-memory entries; it does not call `register`:

```text
commands.agents.list_agents
→ store.list_agents
├→ store.get_live_roster
│ → store.prune_dead_roster
│ → store.load_roster
│ → addressing.is_live
└→ store.discover_agents
  → adapters.discovery.discover_all
  ├→ claude.discover
  ├→ grok.discover
  └→ omp.discover
```

See [`store.list_agents`](../../src/agent_bus/store.py:461), [`store.discover_agents`](../../src/agent_bus/store.py:422), and [`discover_all`](../../src/agent_bus/adapters/discovery/__init__.py:23).

[TRACED] Current discovered IDs are:

- Claude: `claude:<sessionId>`; an agent-bus-published session becomes `agentbus:<sessionId>`. [`claude.discover`](../../src/agent_bus/adapters/discovery/claude.py:20)
- Grok: `grok:<session_id>`. [`grok.discover`](../../src/agent_bus/adapters/discovery/grok.py:44)
- OMP: `omp:<id>`. [`omp.discover`](../../src/agent_bus/adapters/discovery/omp.py:16)
- Codex: no discovery adapter.

[TRACED] `list_agents` reconciles the registered and discovered views in this order: exact ID, parsed alias, then `(kind, pid)` fallback. The roster row retains UUID/name/kind; discovery supplies current status and fills missing native fields. See [`store.py:467-503`](../../src/agent_bus/store.py:467).

[VERIFIED] `../../tests/agent_bus/presence/test_presence_reconciliation.py` passed: a registered UUID row and separately discovered Claude session were returned as one row, retaining the claimed roster name while taking discovered status.

[TRACED] Discovery may become persistent during delivery. If `store.send_message` targets a discovered mailbox entry not yet in the roster, it writes a roster row before appending the message. Merely listing does not persist it. See [`store.py:686-702`](../../src/agent_bus/store.py:686).

### Non-Claude registration via CLI only

[TRACED] The exact CLI chain is:

```text
cli.main
→ cli.cmd_register
→ commands.agents.resolve_host_pid
  → explicit PID, else store.get_self,
    else store.session_entry_for_current_process,
    else os.getpid
→ reject if the only answer is the short-lived CLI PID
→ commands.agents.register
→ resolve_host_pid
→ store.register
→ listener.rename_uds_listen
```

See [`cli.cmd_register`](../../src/agent_bus/cli.py:139), [`resolve_host_pid`](../../src/agent_bus/commands/agents.py:45), and [`agents.register`](../../src/agent_bus/commands/agents.py:82).

[VERIFIED] With `--pid` pointing at a live `sleep` process, the CLI created `cli-only-peer`, kind `mystery`, with a UUID and live roster row. It published zero Claude session files.

[VERIFIED] Without `--pid` and with no discoverable ancestor session, the CLI exited 1 with:

```text
register failed: cannot tell which process is the session.
...
Pass the session's pid: agent-bus register --name no-pid-peer --pid $PPID
```

[TRACED] `register` alone does not call `start_uds_listen`. It creates a roster address/mailbox but no Claude-facing session file, key, or socket. Consequently, native Claude discovery cannot see a peer registered only this way. `listen`, `join`, MCP startup, or lifecycle startup is a separate operation.

### Listener identity

[TRACED] For every non-Claude lifecycle peer:

```text
lifecycle.session_start
→ listener.start_uds_listen
→ detached `python -m agent_bus listen --pid <host> --adopt`
→ cli.main
→ cli.cmd_listen
→ uds.run_listen
→ store.get_live_roster, to adopt the host row
→ store.register, to add agentbus:session:<entry-id> alias
→ uds._write_our_session
```

See [`lifecycle.py:147-153`](../../src/agent_bus/lifecycle.py:147), [`listener.start_uds_listen`](../../src/agent_bus/listener.py:48), and [`uds.run_listen`](../../src/agent_bus/uds.py:157).

[VERIFIED] A host PID `76733` produced listener PID `76735`; session JSON, key, and socket were all published under `76735`, while `listeners/76733.pid` contained `76735`.

### Liveness and retained mail

[TRACED] Process-backed bus/session/PID addresses use PID plus `procStart`; Codex thread addresses always report live and have no file mailbox. See [`addressing.is_live`](../../src/agent_bus/adapters/addressing/__init__.py:47) and [`thread.py:24-32`](../../src/agent_bus/adapters/addressing/thread.py:24).

[TRACED] Dead entries without unread mail are pruned. Dead entries with unread mail remain resolvable, but disappear from the live roster. `find_entry` prefers a live same-name row over a retained stale one. See [`prune_dead_roster`](../../src/agent_bus/store.py:235), [`find_entry`](../../src/agent_bus/store.py:399), and [`get_live_roster`](../../src/agent_bus/store.py:416).

[TRACED] A restarted same-named process does not inherit the old mailbox: it receives a new UUID, and name resolution prefers the new live row. Old unread mail remains addressable only by the old ID.

[TRACED] Teardown is not uniform. `lifecycle.session_end → unregister_by_pid` preserves an unread row; explicit CLI `unregister` and `commands.agents.leave → store.unregister` remove the roster row unconditionally and can orphan its inbox. See [`unregister_by_pid`](../../src/agent_bus/store.py:367), [`unregister`](../../src/agent_bus/store.py:346), and [`agents.leave`](../../src/agent_bus/commands/agents.py:171).

## 2. Delivery

### Routing rule

[TRACED] The sender’s kind does not select transport. The resolved recipient’s kind does:

```text
messages.send
→ store.resolve_target
→ messages._refuse_if_not_live
→ transport.for_kind(recipient.kind)
```

Only `claude` and `codex` have native adapters. Grok, OMP, desktop, `other`, and unknown kinds take filebus. See [`messages.send`](../../src/agent_bus/commands/messages.py:23) and [`transport.for_kind`](../../src/agent_bus/adapters/transport/__init__.py:30).

### Claude ↔ Claude

[TRACED] A native Claude `SendMessage` from one Claude session to another executes no function in this repository. Both discovery and delivery belong to Claude. `claude.discover` only runs when agent-bus itself lists/resolves agents; `session_start` explicitly does not start a shim listener for a Claude kind.

[UNCONFIRMED] The docs’ claim that current Claude Code performs native Claude↔Claude delivery over the described UDS protocol was not independently verified against installed Claude `2.1.251`.

[TRACED] If someone explicitly invokes `agent-bus send` targeting Claude, that is the non-Claude→Claude router path below regardless of the caller’s actual harness. It is not the native Claude→Claude path.

### Claude → non-Claude peer with a listener

[TRACED] Native ingress uses the socket listener and bypasses `commands.messages.send`:

```text
external Claude connects
→ uds.run_listen accept loop
→ nested handle
→ socket.recv
→ nested _process_frame
→ json.loads
→ authenticate against this listener’s published peerToken
→ extract/unwrap user content and from-name
→ store.send_message(to=bus_id)
  → ensure_dirs
  → resolve_target
  → find_entry
  → prune_dead_roster / load_roster
  → addressing.has_mailbox
  → find_entry by target ID
  → _inbox_path_for
  → _count_unread_lines
  → make_agent_ref / new_id / now_iso
  → message_to_json
  → append JSONL
→ construct peer_message_status for the original wire msg_id
→ _key_path for sender
→ socket.connect to sender’s reply address
→ send auth, then status
→ shutdown, drain, close
```

The executable ingress is [`uds.py:288-478`](../../src/agent_bus/uds.py:288); persistence is [`store.send_message`](../../src/agent_bus/store.py:640).

[VERIFIED] My wire exercise sent wire ID `claude-wire-id-report` into a real detached Grok-kind listener. It produced:

- an unread inbox record containing `claude-to-peer-wire-message`;
- stored sender name `claude-source`;
- a separate dial-back connection with auth first;
- `peer_message_status`, `status:"delivered"`, `orig_msg_id:"claude-wire-id-report"`.

[TRACED] Persistence failure prevents the delivered receipt for a user frame. `_process_frame` sets `inbox_ok=False`; no status is constructed for that user frame.

[TRACED] The stored message gets a new local UUID. The inbound wire ID is used only for the receipt; it is not passed as `message_id` to `store.send_message`.

[TRACED] The receiver reads and acknowledges through either surface:

```text
MCP: handle_rpc → _dispatch → _call_inbox
CLI: cli.main → cmd_inbox
→ messages.inbox
→ store.get_inbox
→ _mailbox_id_for
→ find_entry
→ _inbox_path_for
→ _read_all_messages
→ json_to_message
→ is_expired
→ message_to_json

MCP: _call_ack
CLI: cmd_ack
→ messages.ack
→ store.ack_message
→ _mailbox_id_for
→ _read_all_messages
→ resolve_message_id
→ _write_messages
→ message_to_json
```

See [`messages.inbox/ack`](../../src/agent_bus/commands/messages.py:175), [`store.get_inbox`](../../src/agent_bus/store.py:792), and [`store.ack_message`](../../src/agent_bus/store.py:853).

### Non-Claude → Claude

[TRACED] CLI ingress is:

```text
cli.main
→ cli.cmd_send
→ log.logged.wrapper
→ messages.send
```

MCP ingress is:

```text
mcp_server.serve
→ _read_stdio_message
→ handle_rpc
→ _dispatch
→ _CALLS["send_message"]
→ _call_send
→ log.logged.wrapper
→ messages.send
```

See [`cli.cmd_send`](../../src/agent_bus/cli.py:56), [`mcp_server._call_send`](../../src/agent_bus/mcp_server.py:182), and [`_dispatch`](../../src/agent_bus/mcp_server.py:380).

[TRACED] The remainder is:

```text
messages.send
→ store.resolve_target
  → find_entry
  → otherwise discover_agents → discover_all → claude.discover
→ _refuse_if_not_live
→ addressing.is_live
→ transport.for_kind("claude")
→ claude.send
→ claude.socket_for
  → native.messagingSocketPath, else sessions/<target-pid>.json
→ uds.send_peer_message
  → resolve sender socket from env/current PID/ancestor listener/single listener
  → read target peerToken
  → _advertised_name(sender socket)
  → create a fresh wire UUID
  → connect
  → send auth
  → send user frame
  → shutdown/drain/close
→ messages._keep_a_delivered_copy
→ store.send_message(read=True)
→ messages._sent
→ protocol.delivery_expectation
```

See [`claude.send`](../../src/agent_bus/adapters/transport/claude.py:58), [`send_peer_message`](../../src/agent_bus/uds.py:561), and [`_keep_a_delivered_copy`](../../src/agent_bus/commands/messages.py:132).

[VERIFIED] Against a fake Claude socket, the code sent exactly two frames: target auth first, then a user frame with a fresh UUID, `priority:"next"`, `from:"uds:<sender socket>"`, and the `<cross-session-message>` wrapper. It then wrote a durable copy with `read:true`.

[TRACED] “Success” does not mean a delivery receipt was correlated. `send_peer_message` returns `True` after connect/send/half-close; it does not parse a dial-back receipt, and errors during the drain are swallowed. The fake target sent no receipt and the command still returned success.

[TRACED] The durable copy is marked read on that socket-I/O success. Failure to write the copy—including inbox-full or oversized-text failure—is suppressed because the message was already handed to the native transport.

[TRACED] Two IDs are minted: the UDS frame ID and the durable-copy ID. The command returns the copy ID, not the wire ID.

[TRACED] `--from-name` is not passed into `send_peer_message`. The Claude-facing envelope uses the name from the sender’s published session via `_advertised_name`, falling back to `agent-bus`. The durable copy can therefore record a different sender name from the wire frame.

### Non-Claude peer registered only through CLI

[TRACED] For a live CLI-only recipient of Grok/OMP/desktop/other/unknown kind:

```text
CLI or MCP send ingress
→ messages.send
→ store.resolve_target
→ store.find_entry
→ _refuse_if_not_live
→ transport.for_kind = None
→ filebus.send
→ store.send_message(read=False)
→ append to inbox JSONL
→ messages._sent
```

See [`filebus.send`](../../src/agent_bus/adapters/transport/filebus.py:16).

[VERIFIED] The CLI-only `mystery` peer received an unread JSONL message, readable by `agent-bus inbox --name cli-only-peer --unread --json`, and `agent-bus ack` changed it to read.

[VERIFIED] After its holder process exited, another public send failed with `receiver unavailable`, while the already-written unread message remained readable. After it was acknowledged, the next lookup pruned the dead row and returned `no such agent`.

[TRACED] Native Claude cannot initiate to this CLI-only peer because `register` published no Claude session/socket. Conversely, the peer cannot send to Claude without separately starting a listener: `send_peer_message` requires a sender socket for the return address and otherwise returns `cannot determine our listen socket`.

[TRACED] A CLI-only peer can send to another filebus peer without a listener; sender-socket resolution is only required by the Claude native transport.

[VERIFIED] A registered Codex process is a special failure, not a filebus peer: `messages.send` selects the Codex adapter, but a normal registered process has no `native.threadId`, so `codex.send` raises “not a thread.” The relevant routing test passed; source is [`codex.send`](../../src/agent_bus/adapters/transport/codex.py:400).

[TRACED] A separately resolved Codex thread name/ID can be sent through `transport.resolve_unknown → codex.resolve → codex.send`, but that is not the registered bus peer’s inbox.

### Sender identity

[TRACED] Filebus sender identity works as follows:

- explicit `from_name`: accepted verbatim, paired with a newly generated unregistered sender ID;
- otherwise `get_self → _entry_for_current_process → get_live_roster → ancestor_pids`;
- if no ancestor matches, `anonymous` plus a fresh ID.

See [`store.py:710-726`](../../src/agent_bus/store.py:710).

[TRACED] UDS inbound trusts the frame/wrapper’s `from-name`; receiver-token authentication proves access to the receiving socket, not the claimed sender name. The stored sender gets kind `other` and a fresh ID. See [`uds.py:346-374`](../../src/agent_bus/uds.py:346).

[VERIFIED] Although the MCP tool schema does not advertise `from_name`, `_call_send` reads it and `_dispatch` performs no schema validation. Calling `handle_rpc` with an extra `from_name:"asserted-by-mcp-client"` succeeded and the inbox recorded that exact claimed name.

## 3. Documentation disagreements and unsupported claims

[TRACED] [`docs/UDS-protocol.md:47-50`](../UDS-protocol.md:47) shows session/key publication before socket bind. Actual order is bind/listen at [`uds.py:201-212`](../../src/agent_bus/uds.py:201), then session/key publication at [`uds.py:266-270`](../../src/agent_bus/uds.py:266).

[VERIFIED] [`docs/UDS-protocol.md:75-77`](../UDS-protocol.md:75) says `--pid` is the published session/socket PID. Actual and observed behavior always publishes under `os.getpid()` of the listener; `--pid` is the watched host PID.

[TRACED] [`docs/UDS-protocol.md:195`](../UDS-protocol.md:195) hardcodes outbound `from-name="agent-bus"`. Current source uses [`_advertised_name`](../../src/agent_bus/uds.py:128), normally the registered/published peer name.

[TRACED] [`docs/identity-and-peering.md:239-242`](../identity-and-peering.md:239) says standalone CLI registration claims the command’s own PID, reports success, then gets pruned. Current CLI refuses that case before registration; explicit/discovered host PIDs persist.

[TRACED] [`docs/identity-and-peering.md:247`](../identity-and-peering.md:247) says inbound UDS lands in “both the capture file and the inbox.” There is no executable capture-file writer. Source explicitly sends frames only to gated TRACE logging at [`uds.py:340-344`](../../src/agent_bus/uds.py:340).

[TRACED] [`docs/identity-and-peering.md:225`](../identity-and-peering.md:225) says a single-turn peer’s message survives to be read “on its next run.” The bytes survive under the old UUID, but a restarted process receives a new UUID and same-name lookup prefers its empty live mailbox. General mailbox inheritance is absent.

[TRACED] [`docs/identity-and-peering.md:253`](../identity-and-peering.md:253) says identity depends on the MCP server and “nothing registers it otherwise.” Hooks, explicit CLI registration, `listen`, and `join` all register without MCP.

[TRACED] [`docs/identity-and-peering.md:198`](../identity-and-peering.md:198) says the listener exists only while the MCP server runs. CLI `listen`, `commands.agents.join`, and bridge processes also create listeners.

[TRACED] [`docs/identity-and-peering.md:152-153`](../identity-and-peering.md:152) says an MCP peer cannot assert another identity because `from_name` is not exposed. It is absent from the schema but explicitly accepted by `_call_send`; the verified RPC call spoofed it successfully.

[VERIFIED] [`docs/harness-compatibility.md:183-185`](../harness-compatibility.md:183) says sending to a Codex bus name reaches its file inbox. Current routing selects the Codex adapter and rejects the process row for lacking `threadId`.

[TRACED] [`docs/harness-compatibility.md:255-265`](../harness-compatibility.md:255) says Claude session addresses are mailbox-less. Current [`session.has_mailbox`](../../src/agent_bus/adapters/addressing/session.py:39) returns `True`, and successful native sends write read=true copies.

[TRACED] [`docs/identity-and-peering.md:9-10`](../identity-and-peering.md:9) likewise says a Claude session has no inbox, contradicting both current source and the same document’s later “every peer got a mailbox” discussion.

[VERIFIED] [`docs/harnesses/claude-code-presence.md:156-161`](../harnesses/claude-code-presence.md:156) says dead-peer pruning deletes the entry and inbox. Current pruning retains a row with unread mail and never deletes the inbox file as part of pruning.

[TRACED] [`docs/harnesses/claude-code-presence.md:58-60`](../harnesses/claude-code-presence.md:58) and [`docs/comparison-note.md:83-84`](../comparison-note.md:83) say status is written once and never updated. `set_status` writes roster status and calls [`publish_status`](../../src/agent_bus/listener.py:137); each MCP tool call also invokes [`touch_published_session`](../../src/agent_bus/mcp_server.py:414).

[TRACED] [`docs/structured-logging.md:12`](../structured-logging.md:12) says the message ID is the trace ID “on both sides.” That is not true across UDS: inbound wire ID and stored message ID differ, and outbound wire ID and durable-copy ID differ.

[VERIFIED] CLI help says message text is “max 1M”; a live filebus target rejected 32,769 characters because [`store.MAX_TEXT`](../../src/agent_bus/store.py:50) is 32,768. The limit is not central: native adapters run before the store copy, and copy failures are swallowed, so the MCP description’s global limit/full-inbox promise is also not enforced for native delivery.

[UNCONFIRMED] Doc claims about Claude’s current receipt classifications, conversation injection, approval behavior, wake behavior, and native Claude↔Claude delivery were not independently established. The repository’s emitted/accepted frames were verified; current Claude `2.1.251` was not exercised.