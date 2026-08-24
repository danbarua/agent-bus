<!-- Provenance: external read-only source review of danbarua/grok-build,
     branch claude/grok-socket-messaging-docs-7mpde7, HEAD 07b2f71.
     Filed here unmodified. Claims carry file:line citations against that
     checkout and will drift as it moves. See comparison-note.md for what
     this means for agent-bus. -->

# Grok Build: Socket & Inter-Agent Messaging — Source Reference

Scope: `crates/codegen/xai-grok-shell/src` (plus `crates/codegen/xai-grok-hooks`,
`crates/codegen/xai-grok-mcp`, `crates/codegen/xai-grok-pager*`, `xai-grok-telemetry`,
`xai-grok-home`, `xai-grok-shared` where cited). Repo: `danbarua/grok-build`,
branch `claude/grok-socket-messaging-docs-7mpde7`, HEAD `07b2f71` at review time.

Methodology note: this document was assembled from source excerpts gathered by
several independent read-only passes over the codebase. All citations were
re-derived from actual file reads; no field/behavior is asserted without a
`file:line` pointer. Anything not directly stated in a comment/doc but
concluded from code behavior is explicitly labeled `INFERRED`. Anything
searched for and not found is stated as **NOT FOUND** rather than described
speculatively.

---

## 1. Leader Socket

### What creates it, and when

The socket is bound by an explicit `agent leader` subprocess — not lazily
inline in arbitrary CLI invocations, though ordinary commands transparently
cause that subprocess to be spawned the first time one is needed.

- CLI entry point: `Command::Leader`/`AgentCmd::Leader` dispatch —
  `crates/codegen/xai-grok-pager-bin/src/main.rs:1486`, which calls
  `xai_grok_shell::agent::app::run_leader`
  (`crates/codegen/xai-grok-shell/src/agent/app.rs:750`).
- Auto-spawn path: any client command calls `connect_or_spawn`
  (`crates/codegen/xai-grok-shell/src/leader/mod.rs:1443-1630`). It first
  tries `listener_is_ready(&sock_path)` (`mod.rs:1456`); only on failure does
  it acquire the lock file (`lock.try_acquire()`, `mod.rs:1492`) and, if it
  wins, spawn a leader subprocess via `spawn_leader_subprocess`
  (`mod.rs:1685-1743`), running
  `<exe> agent leader --no-exit-on-disconnect --relay-on-demand ...`
  (`mod.rs:1688-1692`).
- Bind sequence (`run_leader`, `agent/app.rs:750-879`): lock-then-socket —
  (1) acquire the exclusive flock on the `.lock` file first
  (`app.rs:774-815`), (2) `lock.cleanup_socket()` removes any pre-existing
  stale socket file (`app.rs:816`), (3) `run_leader_server` does
  `std::fs::remove_file(&socket_path)` then `LeaderListener::bind(&socket_path)`
  (`crates/codegen/xai-grok-shell/src/leader/server.rs:1580-1582`).
- Stale-socket handling: the socket file carries no locking semantics itself
  — the sibling `.lock` file (flock) is the sole leader-election primitive.
  Whichever process holds the flock unconditionally deletes and rebinds the
  socket path (`server.rs:1580`, `lock.rs:266-272` `cleanup_socket`). If a
  `LeaderLock` is dropped mid-leadership without an explicit `release()`
  (crash / abrupt exit), `Drop` removes both the `.lock` and `.sock` files
  (`lock.rs:304-316`).
- A "zombie leader" detector exists: if a process holds the flock (verified
  via `/proc/locks` on Linux, `mod.rs:1270-1354`) but its socket doesn't
  accept connections within `ZOMBIE_EVICT_DEADLINE` = 30s (`mod.rs:94`),
  `connect_or_spawn` SIGTERMs then SIGKILLs it (`evict_zombie_leader`,
  `mod.rs:1390-1425`) and retries.
- Version-floor eviction: a connectable leader running a strictly older
  parseable semver than the connecting client is asked to vacate
  (`RelaunchForUpdate`, or SIGTERM as fallback) and replaced — `should_evict`
  / `leader_is_older_than` (`mod.rs:97-111`), `evict_leader`
  (`mod.rs:1183-1218`).

### Transport and framing

- Unix: `LeaderListener`/`LeaderStream` are plain aliases for
  `tokio::net::UnixListener`/`UnixStream`
  (`crates/codegen/xai-grok-shell/src/leader/transport.rs:9-12`) — a real
  Unix domain socket, no wrapper.
- Windows: no AF_UNIX in tokio, so it's a Windows Named Pipe
  (`\\.\pipe\grok-leader-<siphash>`), the filesystem path hashed into the
  pipe name via SipHash-1-3 (`transport.rs:230-249`); `LeaderStream`/
  `LeaderListener` wrap `NamedPipeServer`/`NamedPipeClient`/`ServerOptions`
  (`transport.rs:44-196`).
- No TCP anywhere in this path.
- Framing is length-prefixed binary: a 4-byte big-endian `u32` length header
  followed by that many raw bytes, capped at `MAX_MESSAGE_SIZE = 64 * 1024 *
  1024` (64MB) (`protocol.rs:8`):

```rust
// protocol.rs:22-57
pub(crate) async fn read_frame<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Vec<u8>, ProtocolError> {
    let mut len_buf = [0u8; 4];
    reader.read_exact(&mut len_buf).await ...
    let len = u32::from_be_bytes(len_buf);
    if len > MAX_MESSAGE_SIZE { return Err(ProtocolError::MessageTooLarge(len)); }
    let mut buf = vec![0u8; len as usize];
    reader.read_exact(&mut buf).await?;
    Ok(buf)
}
pub(crate) async fn write_frame<W: AsyncWrite + Unpin>(writer: &mut W, data: &[u8]) -> Result<(), ProtocolError> {
    let len = data.len() as u32;
    writer.write_all(&len.to_be_bytes()).await?;
    writer.write_all(data).await?;
    writer.flush().await?;
    Ok(())
}
```

Not newline-delimited. `read_message`/`write_message` (`protocol.rs:59-75`)
layer JSON on top of `read_frame`/`write_frame`.

### Encoding

JSON via `serde_json`, one frame per message. All wire enums use
`#[serde(tag = "type", rename_all = "snake_case")]` (internally tagged),
except `ClientMode` which is a bare snake_case string.

Top-level enums:
- `ClientMessage` (client→leader): `Register`, `Acp`, `Control`, `Ping`,
  `Disconnect` (`protocol.rs:298-317`).
- `ServerMessage` (leader→client): `Registered`, `Acp`, `ControlResult`,
  `Pong`, `Error`, `ShuttingDown`, `Shutdown`, `LeaderReady`
  (`protocol.rs:348-402`).

Example frame payloads (field order not guaranteed; verified against real
test JSON at `protocol.rs:580-632` and `654-698`):

Registration request (`ClientMessage::Register`, `protocol.rs:301-307`):
```json
{"type":"register","client_type":"grok-tui","mode":"stdio","capabilities":{"yolo_mode":false,"auto_mode":false,"default_model":null,"client_version":"0.1.220","code_nav_enabled":false,"terminal":true,"fs_read":true,"fs_write":true,"status_line":true}}
```

Registration response (`ServerMessage::Registered`, `protocol.rs:357-368`):
```json
{"type":"registered","client_id":7,"ready":true,"leader_protocol_version":1,"leader_binary_version":"0.1.220","leader_capabilities":{"control_v1":true,"runtime_cpu_profile":true,"profile_formats":["svg"],"workspace_exposure":true,"relaunch_v1":true}}
```

ACP passthrough envelope (`protocol.rs:308-310`, `369-371`) — the payload is
an opaque JSON-RPC string (agent-client-protocol) the leader forwards,
namespacing request IDs as `"<client_id>|<original_id_json>"`
(`server.rs:279-303`, `ID_NAMESPACE_SEP = '|'` at `server.rs:40`):
```json
{"type":"acp","payload":"{\"jsonrpc\":\"2.0\",\"method\":\"initialize\",\"id\":1,...}"}
```

Control request/response, e.g. `GetLeaderInfo`
(`protocol.rs:311-314`, `372-375`, shape at `protocol.rs:238-252`):
```json
{"type":"control","request_id":"1","command":{"type":"get_leader_info"}}
```
```json
{"type":"control_result","request_id":"1","result":{"Ok":{"type":"leader_info","pid":4242,"socket_path":"/home/u/.grok/leader.sock","lock_path":"/home/u/.grok/leader.lock","ws_url_suffix":"","leader_protocol_version":1,"leader_binary_version":"0.1.220","profiling_supported":true,"profiling_compiled_in":true,"cpu_profile_active":false,"cpu_profile_stopping":false,"profile_started_at":null,"profile_formats":["svg"]}}}
```

### Handshake / version negotiation

Yes, at two levels:

1. **Registration**: client sends `ClientMessage::Register{...}` right after
   connecting, waits up to `REGISTRATION_RESPONSE_TIMEOUT = 10s`
   (`client.rs:28`) for `ServerMessage::Registered{...}`
   (`crates/codegen/xai-grok-shell/src/leader/client.rs:333-430`);
   leader-side timeout `REGISTRATION_TIMEOUT = 30s` (`server.rs:36`); any
   non-`Register` first message is an error (`server.rs:2435-2445`).
2. **Readiness gating**: if the leader is still starting (`ready: false`),
   the client blocks until `ServerMessage::LeaderReady`
   (`client.rs:398-430`, `LEADER_READY_TIMEOUT`); leader-side wait logic at
   `server.rs:2447-2473`.
3. **Protocol-version check**: `LEADER_PROTOCOL_VERSION: u32 = 1`
   (`protocol.rs:125`). Discovery rejects a leader advertising a lower
   version as `UnsupportedProtocol` (`mod.rs:290-299`); `send_control`
   refuses to send unless the leader's advertised version exactly matches
   the client's compiled-in constant (`client.rs:191-201`).
4. **Binary-version skew** (semver comparison of `leader_binary_version`,
   distinct from the fixed protocol version): `should_evict` /
   `leader_is_older_than` (`mod.rs:97-111`) decide eviction/replacement of
   an older leader; the leader also warns a newer client via
   `make_version_mismatch_notification` (`server.rs:1679-1694`), asserted by
   `tests/test_leader_version_skew.rs:206-230`
   (`old_client_adopts_new_leader_and_still_functions`, a two-real-binary
   `#[ignore]`d harness requiring `GROK_BINARY_LEADER`/`GROK_BINARY_CLIENT`).
5. Fields degrade gracefully via `#[serde(default)]` throughout (e.g.
   `default_ready()`, `protocol.rs:342-345`; comment at `protocol.rs:347`:
   "the leader and client can run different binary versions").

### Operations exposed

`ClientMessage` (client→leader, `protocol.rs:300-317`): `Register{client_type,
mode, capabilities}`, `Acp{payload}`, `Control{request_id, command}`, `Ping`,
`Disconnect`.

`ServerMessage` (leader→client, `protocol.rs:350-402`): `Registered{...}`,
`Acp{payload}`, `ControlResult{request_id, result}`, `Pong`, `Error{code,
message}`, `ShuttingDown{reason, delay_ms}`, `Shutdown`, `LeaderReady`.

`ControlCommand` (process-management RPC surface, `protocol.rs:203-233`,
dispatched at `server.rs:1250-1307` sync / `server.rs:1781-1808` async):
`GetLeaderInfo`, `CpuProfileStatus`, `StartCpuProfile{output, frequency_hz}`,
`StopCpuProfile`, `WorkspaceStart{hub_url, cwd}`, `WorkspacePause`,
`WorkspaceResume`, `WorkspaceStop`, `WorkspaceStatus`,
`RelaunchForUpdate{to_version}`.

`ControlPayload` responses (`protocol.rs:237-296`): `LeaderInfo{...}`,
`CpuProfileStatus{...}`, `CpuProfileStarted{...}`, `CpuProfileStopped{...}`,
`WorkspaceStatus{...}`, `Relaunching{...}`, `RelaunchDeclined{reason}`.

Internal-only extension methods injected into the ACP stream by the leader,
`_`-prefixed, not client-invocable (`InternalMethod`, `protocol.rs:404-442`):
`AuthCleared`, `EvictSessions`, `ReloadAllMcpServers`, `ReloadModels`,
`ReloadModelsCache`, `ReloadProjectMcpServers`, `ReloadSkills`,
`ReloadWorkflows`.

Beyond this control layer, most traffic is the ACP (agent-client-protocol)
JSON-RPC methods carried opaquely inside `ClientMessage::Acp` /
`ServerMessage::Acp.payload` (`initialize`, `session/new`, `session/load`,
`session/prompt`, etc. — `agent_client_protocol::AGENT_METHOD_NAMES`,
`server.rs:28`); the leader inspects/rewrites request IDs and routes by
`sessionId` (`server.rs:279-400`) but treats the payload as opaque JSON.

### Authentication

**NONE found.** No `SO_PEERCRED`/`peer_cred`/`UCred`/`getsockopt`, no
`set_permissions`/`PermissionsExt`/`chmod`/`umask`/octal mode literal, and no
token-file check anywhere in `crates/codegen/xai-grok-shell/src/leader/` or
the wider crate.

- `LeaderListener::bind(&socket_path)` (`server.rs:1582`) is a bare
  `UnixListener::bind` with no post-bind `fs::set_permissions` call anywhere
  in `server.rs`, `lock.rs`, or `app.rs`.
- The parent directory (`grok_home()`,
  `crates/codegen/xai-grok-home/src/lib.rs:54-65`) is created with plain
  `std::fs::create_dir_all(&home)` (`lib.rs:59`) — no explicit mode
  restriction beyond the process umask.
- `ClientMessage::Register` carries no credential/secret field — only
  `client_type`, `mode`, `ClientCapabilities` (self-reported flags, not
  identity) (`protocol.rs:127-180`, `300-307`).
- The only access control that exists at all is implicit standard Unix
  filesystem permissions on the socket file and its containing directory
  (`~/.grok` or `$GROK_HOME`), left at whatever the OS default + umask
  produce; the code never explicitly narrows it. Windows named pipes
  likewise have no explicit ACL/security-descriptor code in `transport.rs`.
- INFERRED: in practice, security relies entirely on same-user filesystem
  access to the socket path (single-user desktop assumption) — anyone who
  can connect to `~/.grok/leader.sock` (or point at another socket via
  `GROK_LEADER_SOCKET`/`--leader-socket`, `lock.rs:36-52`) can register as a
  client with no further check.

### `lock.rs` and leader election

A separate mechanism from the socket itself — an OS-level advisory `flock`
(via `fs2`'s `try_lock_exclusive`/`unlock`) on a sibling `.lock` file,
deciding only *which process gets to bind the socket*.

- Struct: `LeaderLock{lock_path, sock_path, lock_file: Option<File>,
  was_leader: bool}` (`lock.rs:139-148`); doc: "1. Exclusive lock indicates
  who is the leader (or who is spawning) / 2. File contents store the
  leader's PID for diagnostics" (`lock.rs:126-133`).
- Path derivation: `~/.grok/leader{suffix}.sock`/`.lock` by default, where
  `suffix` hashes the target `grok_ws_url` (empty for default production
  URL) — `compute_ws_url_suffix`/`socket_path_for_ws_url_in`/
  `lock_path_for_ws_url_in` (`lock.rs:13-90`), overridable wholesale via
  `GROK_LEADER_SOCKET`/`--leader-socket` (`lock.rs:44-52`; flag defined at
  `crates/codegen/xai-grok-pager/src/app/cli.rs:436-445`, consumed at
  `crates/codegen/xai-grok-pager-bin/src/main.rs:1984-1986`).
- Election: `try_acquire()` does non-blocking `try_lock_exclusive()` —
  `Ok(true)` = leader-designate, `Ok(false)` = someone else holds it
  (`lock.rs:191-202`); both `run_leader` (`app.rs:774-816`) and
  `connect_or_spawn` (`mod.rs:1492-1577`) call this. The winner calls
  `write_pid()` (`lock.rs:243-250`) and binds the socket; the loser either
  connects (client) or errors out (a second `run_leader`, `app.rs:781-792`).
- PID in the lock file is diagnostics-only, not identity proof: the real
  flock holder is separately (Linux-only) verified by parsing
  `/proc/locks` (`flock_holder_pid`/`parse_flock_holder`,
  `mod.rs:1288-1354`), used by the zombie-eviction safety gate
  (`evictable_holder`, `mod.rs:1278-1283`).
- Cleanup: the flock is held for the real leader's entire lifetime; a
  spawner that only won the race to fork the leader calls `release()` to
  hand off ownership without deleting files (`lock.rs:274-283`); `Drop`
  deletes `.lock`/`.sock` only if acquired-but-never-released (crash path)
  (`lock.rs:304-316`).

**Confidence: high.** Every claim above traces to a specific struct, enum
variant, or function with cited line numbers, cross-checked against a
version-skew integration test. The absence-of-auth conclusion is a negative
result from exhaustive grep across the leader module and the crate at large,
not merely an unchecked area.

---

## 2. Session Registry

### Does a discoverable `~/.grok/active_sessions.json`-style record exist?

**NOT FOUND** in that exact form — there is no single local JSON/JSONL file
listing all currently-running sessions. What exists instead are three
distinct, non-equivalent mechanisms:

**(a) In-memory, per-leader-process registry (not persisted, not
cross-process).** `SessionRegistry`
(`crates/codegen/xai-grok-shell/src/agent/mvp_agent/session_registry.rs:6-8`)
is a `HashMap` behind `Rc<RefCell<...>>`, held as a field on `MvpAgent`
(`agent/mvp_agent/mod.rs:744`):
```rust
#[derive(Clone, Default)]
pub(super) struct SessionRegistry {
    sessions: Rc<RefCell<HashMap<acp::SessionId, SessionResources>>>,
}
```

**(b) A remote REST "session replicas" registry — a cloud service, not a
local file.** `SessionRegistryClient`
(`crates/codegen/xai-grok-shell/src/agent/session_registry_client.rs:140-146`)
is an HTTP client for "cli-chat-proxy":
```
//! REST client for the session replicas registry (cli-chat-proxy).
//! Handles registering, updating, finalizing, searching, and downloading
//! session replicas for cross-host session replication.
```
POSTs to `/sessions/register`, `/sessions/{id}/replicas/update`,
`/sessions/{id}/replicas/finalize`; GETs `/sessions/search`,
`/sessions/{id}/replicas` (`session_registry_client.rs:263,275,287,302,317`).
It is gated: only built when `session_registry_enabled` is set **and** the
current auth is xAI auth (`agent/mvp_agent/agent_ops.rs:556-580`, gate at
lines 565-571).

**(c) The leader lock's single-value PID file**, `~/.grok/leader.lock`
(paired with `leader.sock`) — tracks only the one PID of whichever process
currently holds leadership, plain text, not JSON, not a session list
(`leader/lock.rs:78-90,125-129,242-263`).

### Entry fields

In-memory `SessionResources` (`session_registry.rs:204-211`):
```rust
struct SessionResources {
    retained: Option<RetainedResources>,
    resident: Option<ResidentResources>,   // cleared at idle-unload
    presence: Option<SessionPresence>,
    unavailable_model: Option<acp::ModelId>,
}
```
`SessionPresence` (`session_registry.rs:28-64`) is a lifecycle enum
(`Resident`/`Attaching`/`Evicted`/`Closed`/`Dead`/`Dormant`) carrying an
optional `SessionThread`/`SessionHandle` and, for `Resident`/`Attaching`, an
`Activity` (`Idle`/`Working`). **No pid, cwd, or socket-path field on this
struct** — this registry is scoped to a single process, so it doesn't need
to track a host/socket location.

The dashboard-facing projection, `RosterEntry`
(`crates/codegen/xai-grok-shell/src/agent/roster.rs:55-81`), carries `
session_id, title, cwd, is_worktree, model_id, reasoning_effort, yolo,
activity, last_turn_summary, resident, last_change_unix_ms, origin` — still
no pid, no socket path — and is built on demand (`build_roster`,
`agent/mvp_agent/session_lifecycle.rs:361-369`), not a stored file.

Remote replica entry, `SessionRecord` (`session_registry_client.rs:76-97`):
`session_id, summary, first_prompt, model_id, created_at, updated_at,
last_turn_number, restorable_turn_number, cwd, repo_remote_url, hostname,
status, gcs_trace_prefix, gcs_bucket, last_active_at`. `RegisterRequest`
additionally carries `device_id` and `parent_session_id`/`session_kind`
(`session_registry_client.rs:19-51`). No PID, no socket path; lives
server-side.

### Who writes entries, and when

Everything is written by the single hosting process (leader, or a
standalone agent process) — there is no separate per-session OS process
writing its own entry; sessions are in-process actors, not separate
processes.

- In-memory registry: `put_resident`/`insert_resident` on spawn
  (`session_registry.rs:445-475`, called from `session_lifecycle.rs:155-161`);
  `set_live`/`set_session_live_state` at state transitions
  (`session_registry.rs:320-357`, `session_lifecycle.rs:219-221`);
  `release`/`remove_session` on close/delete (`session_registry.rs:234-257`,
  `session_lifecycle.rs:199-212`). New-session creation in
  `new_session_inner` (`agent/mvp_agent/session_setup.rs:228`, spawn at
  `:510`); load/attach/resume in `load_session_inner`/`attach_session`/
  `resume_session_inner` (`session_setup.rs:676-1040,1487-1509`).
- Remote replica registry: `register()` on session creation; `update()`
  after each turn; `finalize()` only on explicit close, from
  `finalize_session_replica` (`session_lifecycle.rs:134-145`):
  ```rust
  /// Move the replica `active` -> `completed`. A hosting signal, not a
  /// conversation ending: only an explicit close sends it.
  pub(super) fn finalize_session_replica(&self, id: &acp::SessionId) { ... }
  ```
  No periodic heartbeat write to either registry.
- Leader lock PID file: written once, at leader-process startup, by
  `write_pid()` after `try_acquire()`/`acquire_reopen_timeout()`
  (`leader/lock.rs:191-250`) — not per session.

### Liveness

Not PID-existence or heartbeat+TTL per session — liveness is tracked purely
as in-process state:
```rust
pub(super) fn live_state(&self) -> Option<SessionLiveState> {
    match self {
        Self::Resident { activity: Activity::Working, .. } => Some(SessionLiveState::Working),
        Self::Resident { activity: Activity::Idle, .. } => Some(SessionLiveState::IdleResident),
        Self::Attaching { .. } => Some(SessionLiveState::Attaching),
        Self::Evicted { .. } => None,
        Self::Closed { .. } => Some(SessionLiveState::Completed),
        Self::Dead { .. } => Some(SessionLiveState::DeadFailed),
        Self::Dormant { .. } => Some(SessionLiveState::Dormant),
    }
}
```
(`session_registry.rs:69-85`). The actual liveness signal is the actor's own
thread handle (`SessionThread`) via `.is_finished()`
(`session_registry.rs:293-313`) — an in-process thread/task-join check, not
an OS PID probe. No cross-process heartbeat/TTL exists for sessions
anywhere in this crate.

(The leader lock does use real OS liveness — `flock`/`try_lock_exclusive`,
`leader/lock.rs:191-202` — but only for the single leader process, relying
on the OS releasing the lock on process death; not used for "is session X
alive.")

### Reaping stale entries

Explicit reap logic, not passive staleness. `sweep_dead_sessions`
(`session_lifecycle.rs:401-423`):
```rust
pub(super) fn sweep_dead_sessions(&self) {
    let dead = self.session_registry.finished_threads();
    for id in dead {
        if self.session_registry.live(&id) == Some(SessionLiveState::Attaching)
            && !self.is_resident(&id) { continue; }
        if self.is_resident(&id) {
            tracing::warn!(session_id = %id.0, "Resident session actor exited unexpectedly; reaping as DeadFailed");
            self.reap_dead_session(&id);
        } else {
            self.session_registry.clear_exited_thread(&id);
            tracing::debug!(session_id = %id.0, "Reaped finished thread for non-resident session (clean exit)");
        }
    }
}
```
Driven two ways: (1) a background sweep task spawned by
`ensure_session_supervisor` (`session_lifecycle.rs:424-447`), ticking every
`SESSION_SUPERVISOR_TICK` under `catch_unwind`; (2) reap-on-read, called
synchronously at the start of `attach_session` before load/resume
(`session_setup.rs:689`). Empty entries also auto-prune via `drop_if_empty`
(`session_registry.rs:783-788`) from every mutator.

The remote replica registry has no reap logic client-side — `
SessionRegistryClient` only registers/updates/finalizes/searches, no
delete/expire method.

### Exposure to other sessions/tools

Yes, via leader JSON-RPC extension methods over the leader socket — but only
for the in-memory, single-process roster, not a cross-machine
"list-running-processes" view:

- `x.ai/sessions/list` — request/response, `handle_roster_list`
  (`agent/handlers/session.rs:48-60`) → `agent.build_roster()`.
  **On the wire the method name is `_x.ai/sessions/list`** — ACP prefixes ext
  methods with an underscore, and the name as written here answers
  `-32601 Method not found`, which is indistinguishable from an unsupported
  build. Verified against grok 1.0.5; the tell was unsolicited notifications
  arriving as `_x.ai/mcp/servers_updated`. Same for `_x.ai/sessions/changed`.
  The response is nested **twice**, `result.result.sessions`.
- `x.ai/sessions/changed` — broadcast/push on upsert/removal deltas via
  `emit_roster_changed` (`session_lifecycle.rs:261-277`); the leader treats
  it as a machine-wide broadcast to every connected client
  (`leader/server.rs:394-395`).
- `x.ai/session/list` / `acp::ListSessionsRequest`
  (`agent/handlers/session.rs:262-345`) exposes a merged local+remote
  *conversation history* list (persisted/resumable sessions via
  `unified_list::build_unified_list`) — not a liveness view.
- `x.ai/subagent/list_running` (`extensions/task.rs:476`,
  `agent_ops.rs:2902-2924`) lists running *subagents* under one parent
  session, with orphan-healing against stale `running` meta.json — a
  narrower, separate mechanism from the top-level session roster.

No ACP/JSON-RPC method lists OS-level running `grok` processes/PIDs; the
closest is the leader-lock PID, a diagnostics artifact, not a listing API.

**Confidence: high.** Direct struct/function citations throughout, plus a
test file (`list_running_heal_tests.rs`) confirming the reap/heal behavior
of the subagent-scoped listing.

---

## 3. Inter-Agent Messaging

### Can one session address another directly?

**NOT FOUND.** No mechanism exists for one running Grok session to address a
session_id/name belonging to a *different, independently-running* session
and push it an arbitrary message. No RPC method, no
`ClientMessage`/`ServerMessage` variant, no `send_to_session`/
`route_message`/generic `notify_session` function does this (the two
`notify_session*` hits that exist —
`crates/codegen/xai-grok-shell/src/session/storage/search.rs:182`
`notify_session_updated` and
`crates/codegen/xai-grok-shell/src/extensions/session_admin.rs:390`
`notify_session_title` — update a session's *own* search index/title, not
deliver anything to a different session).

Two adjacent mechanisms exist and are easy to conflate with true
inter-agent messaging:

**(a) Same-session multi-client fan-out (leader broadcast).** The leader
keeps `session_subscribers: HashMap<String, HashSet<ClientId>>`
(`leader/server.rs:1586` onward), mapping one `session_id` to the set of
connected client processes attached to *that same* session. ACP
notifications for a session are fanned out to its subscribers:
```rust
// leader/server.rs:2246-2277
} else if let Some(subs) = session_subscribers.get(sid.as_str()) {
    for &cid in subs.iter() {
        if let Some(client) = clients.get(&cid) {
            if let Err(e) = client.tx.try_send(ClientOutbound::Acp(payload.clone())) { ... }
        }
    }
}
```
This is not addressing — the sender never names a target session; it's
redistribution of one session's own output to clients already watching it
(e.g. two terminals attached to the same session). Child (subagent) sessions
inherit the parent's subscriber set automatically
(`leader/server.rs:2280-2293`, `prune_child_route` at `leader/server.rs:599`)
so watchers see subagent output too — but a session cannot join another
unrelated session's subscriber set or push it a message. Confirmed by the
outer envelope: `ClientMessage` has exactly five variants — `Register`,
`Acp{payload}`, `Control{request_id, command}`, `Ping`, `Disconnect`
(`leader/protocol.rs:298-317`) — none carry a target session id belonging
to someone else.

**(b) Cloud relay/remote sync — not local IPC at all.** `relay/mod.rs:1-11`:
*"Relay session sharing module... syncing TUI sessions to the relay backend
via WebSocket, enabling cross-machine session persistence and real-time
sharing."* `RelaySync` (`relay/sync.rs:206`) streams a session's own ACP
notifications to a cloud WebSocket endpoint; `build_share_url`
(`relay/sync.rs:29-32`) builds a viewer URL like
`https://grok.com/build/{session_id}`. `agent/relay.rs:1-4` is the WebSocket
connection-management half, used by `run_headless`/`run_leader`. `remote/
mod.rs:1`: *"Remote storage client for the backend"* — `BackendClient`,
`ConversationsClient`, `WorkspacesClient`, `SkillsClient`, `SandboxClient`
(`remote/mod.rs:14-42`), pure REST clients to x.ai's backend for auth,
model catalogs, conversation storage, sandbox management. `remote/agent.rs:
1-3` is an HTTP client to "cli-chat-proxy" for sandbox sessions/environments.
`remote/sync.rs:1-17` is an async writeback queue pushing session updates to
the backend (best-effort, lossy on drop). `remote/pull.rs:1` pulls a
session's own prior data down from the cloud. None of these provide a
channel between two concurrently-running local sessions — strictly local
session ⇄ x.ai cloud backend.

### Addressing scheme / envelope / example frame

N/A — no such mechanism exists to describe. The closest identifier concept,
`ClientId(pub u64)` (`leader/protocol.rs:82`, generated by `ClientId::new()`,
`:90-100`), identifies a *connected client socket*, not a target another
session could name.

### Delivery acknowledgment

N/A, for the same reason. (For contrast: the leader's actual client↔leader
RPCs *are* acknowledged — `ClientMessage::Control{request_id,...}` gets
`ServerMessage::ControlResult{request_id, result}`,
`leader/protocol.rs:372-375` — but this is single-client-to-leader
management, not session-to-session.)

### Persistence when the target is absent

Nothing persists as a cross-session mailbox, because no cross-session send
exists. The word "mailbox" that does appear in the codebase (e.g.
`agent/mvp_agent/tests.rs:4258,4347,4422,4576,4594`) refers to a **session
actor's own internal command queue** — the async-actor inbox a session uses
to serialize commands (cancel/interject) to *itself*.

`agent/subagent/attempt_store/` (`codec.rs`, `decoder.rs`, `recovery.rs`,
`rewind.rs`, `intent.rs`, `completion.rs`, `accounting.rs`) implements a
size-bounded append-only journal for a subagent's own attempt/turn history
(segment kinds `InitialOriginalTask`, `InitialAgentMessage`, `AgentMessage`,
`AttachedHuman` — `codec.rs:36-41`); `AgentSenderRelationV1` has exactly one
variant, `ParentToOwnedDescendant` (`codec.rs:56-58`); `AgentAuthorityV1` has
exactly one variant, `ModelAuthoredUntrusted` (`codec.rs:70-72`). This is a
replay/recovery log for a parent's own owned descendant subagent — not
something an independent session can address or deposit mail into. The
whole module is currently `#[allow(dead_code, reason = "consumed by the next
storage slice")]` (`agent/subagent/attempt_store/mod.rs:3-22`) — not yet
wired into a live delivery path.

### Is `agent/subagent/` a separate addressable process?

No — an in-process worker of the parent session, with no independent socket
or externally-addressable identity:

- `agent/subagent/mod.rs:1-13`: "Child sessions share the parent's hunk
  tracker, filesystem, terminal, and env so that edits, bash commands, and
  file reads go through the same backends."
- `SubagentSpawnContext` (`mod.rs:108-150`) is built from handles taken
  directly from the parent's own runtime: `lsp` inherited via `ToolContext`
  (`:109-110`), `process_scope` inherited "so the subagent's own child
  processes are reaped when the parent session closes" (`:111-116`),
  `client_hooks` inherited "so the subagent's tool calls hit the same
  PreToolUse gate... over the parent's connection" (`:117-121`),
  `parent_session_id: String` (`:132`).
- Lifecycle is a plain in-process call chain:
  `MvpAgent::start_subagent_coordinator` → `spawn_subagent_coordinator` →
  `ShellChildRunner::run` → `run_shell_child` → `spawn_session_on_thread`
  (`agent/subagent/spawn.rs:1-16`), on an OS *thread* from a fixed-size
  worker pool (`worker_runtime()`, `spawn.rs:37-40`; comment: "Four suffice
  for 32 children (each runs on its own OS thread)"). No
  `std::process::Command`, `fork()`, `UnixListener::bind`, or leader-socket
  registration occurs anywhere on this path.
- At the leader layer, a spawned subagent is registered only as a routing
  entry piggybacking on the parent's existing subscriber list —
  `session_subscribers.insert(child_sid.clone(), parent_subs)`
  (`leader/server.rs:2280-2293`), torn down via `prune_child_route`
  (`leader/server.rs:599-626`). No separate client process, socket, or
  `ClientId` is created for a subagent.

**Confidence: high.** This is a well-supported negative finding: the search
covered the leader protocol's full envelope enumeration, the relay/remote
modules' own doc comments, and the subagent spawn path's actual call chain
— all converging on "no session-to-session messaging primitive exists."

---

## 4. Identity and Naming

### Id vs. name

Distinct concepts, deliberately.

- Identity = `Info { id: acp::SessionId, cwd: String }`
  (`crates/codegen/xai-grok-shared/src/session/info.rs:1-8`, doc: "Session
  identity: `id` + `cwd`"), re-exported at
  `crates/codegen/xai-grok-shell/src/session/mod.rs:402`.
- Id generation (`agent/mvp_agent/session_setup.rs:293-304`):
  ```rust
  let session_id = match client_session_id {
      Some(s) => {
          uuid::Uuid::try_parse(s).map_err(|e| { ... })?;
          acp::SessionId::new(s.to_string())
      }
      None => acp::SessionId::new(uuid::Uuid::now_v7().to_string()),
  };
  ```
  If the client's `NewSessionRequest._meta.sessionId` is present it must
  parse as a UUID and is used verbatim; otherwise the shell mints a fresh
  **UUIDv7** (time-ordered). This `id` is immutable for the session's life —
  the primary key for its on-disk directory and every registry lookup.
- Name is not part of identity. It lives on `Summary`
  (`session/persistence.rs:817-951`): `session_summary: String` (legacy
  short summary) and `generated_title: Option<String>` (preferred display
  title), plus `title_is_manual: bool` marking a `/rename`-pinned title.
  `Summary::display_title()` (`persistence.rs:1022-1029`) prefers
  `generated_title`, falling back to `session_summary`. At creation
  (`Summary::new`, `persistence.rs:965-1009`) both start blank/`None` — the
  name doesn't exist yet at session-start time.
  - Populated later: a background LLM task
    (`PersistenceMsg::GeneratedTitle`/`RegenerateTitle`, `persistence.rs:
    1807-1846`), refreshed at turns 3 and 6
    (`TITLE_REFRESH_TURNS: [usize; 2] = [3, 6]`,
    `session/helpers/session_summary.rs:18`), or set explicitly via
    `/rename` (manual, pinned via `title_is_manual`).

The machine-level `agent_id()` (`xai-grok-telemetry/src/id.rs:15-17`,
referenced at `extensions/session_admin.rs:38`) is unrelated to session
identity — a per-machine identifier (UUIDv5 from hostname/hardware, cached
at `$GROK_HOME/agent_id`, 0600 perms), used for telemetry/device
disambiguation (`RegisterRequest.device_id`,
`agent/session_registry_client.rs:33-35`) and passed into `fork_session`
(`extensions/session_admin.rs:1001-1002`) — not for naming the session.

**Summary: id = immutable UUIDv7 (or client-supplied UUID), assigned once at
creation, never changes. Name = mutable, optional, human-readable title,
absent at creation, auto-generated or manually set later** — a separate
field on `Summary`, not on `Info`.

### Renaming — mechanism and validation

Yes, via the `x.ai/session/rename` ACP extension method
(`extensions/session_admin.rs:46` → `handle_session_rename`,
`:104-228`).

Constants (`session/persistence.rs:37-45`):
```rust
pub const MAX_TITLE_SCALARS: usize = 100;
pub const MAX_TITLE_BYTES: usize = MAX_TITLE_SCALARS * 4 + 64;
```

Sanitizer (`session/persistence.rs:51-93`, verbatim):
```rust
pub fn is_forbidden_title_char(c: char) -> bool {
    c.is_control()
        || matches!(
            c,
            '\u{200E}' | '\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2066}'..='\u{2069}'
        )
}

pub fn sanitize_rename_title(title: &str) -> Cow<'_, str> {
    if title.chars().any(is_forbidden_title_char) {
        let mut cleaned: String = title.chars().filter(|c| !is_forbidden_title_char(*c)).collect();
        let trimmed = cleaned.trim();
        if trimmed.len() != cleaned.len() { cleaned = trimmed.to_string(); }
        Cow::Owned(cleaned)
    } else {
        Cow::Borrowed(title.trim())
    }
}

pub fn sanitize_and_cap_title(title: &str) -> Option<String> {
    let cleaned = sanitize_rename_title(title);
    if cleaned.is_empty() { return None; }
    if cleaned.chars().count() <= MAX_TITLE_SCALARS { Some(cleaned.into_owned()) }
    else { Some(cleaned.chars().take(MAX_TITLE_SCALARS).collect()) }
}
```
Strips control chars plus bidi/format-override code points (LRM/RLM,
LRE-RLO, isolates), then trims.

`handle_session_rename` flow (`session_admin.rs:104-228`):
1. Deserializes `SessionRenameRequest{session_id, title, cwd, kind,
   reset_to_auto}` (`:81-93`).
2. `reset_to_auto` branch rejects `kind == Chat`, rejects non-blank `title`
   alongside `resetToAuto` (`:106-119`), else calls
   `reset_session_title_to_auto`.
3. Manual rename: rejects `title.len() > MAX_TITLE_BYTES`; sanitizes;
   rejects empty result; rejects `chars().count() > MAX_TITLE_SCALARS`
   (`:125-136`).
4. `kind == Chat` delegates to `rename_chat_conversation` (`:411+`).
5. Otherwise: `storage.update_session_title(&info, req.title.clone())` — a
   `JsonlStorageAdapter` write (`session/storage/jsonl/mod.rs:1040-1049`)
   that patches `generated_title` unconditionally ("last write wins", per
   the trait doc at `session/storage/mod.rs:992-995`) and sets
   `title_is_manual = true`.
6. If resident, routes `PersistenceMsg::ManualTitleRenamed` through the
   actor's FIFO channel (avoiding a race with an in-flight auto-title
   write) and sends `SessionCommand::TitleRenamed{manual: true}` to freeze
   auto-refresh; if dormant, persists the frozen watermark directly
   (`:167-186`).
7. Updates the search index, notifies the client (`notify_session_title`),
   optionally syncs to the writeback backend, fires a fire-and-forget
   registry title update (`:188-223`).
8. Returns `{"success": true}`.

Documented gap (`session_admin.rs:95-103`): for relay-registered sessions,
the relay REST endpoint stays the sole title authority, so this ACP rename
"does not write through `relay_sync`" and reverts on the next relay sidebar
refetch unless the client also renames via the relay REST API.

### What happens to the old name

**Overwritten with no trace.** `update_session_title`
(`session/storage/jsonl/mod.rs:1040-1049`) applies a
`SummaryPatch{generated_title: Some(session_title), ..}` — explicitly
"last write wins" per the trait doc. `Summary` has exactly one
`generated_title: Option<String>` field and one `title_is_manual: bool` —
no array, no version list, no "previous title" field. A repo-wide search for
`previous_title`/`old_title`/`title_history`/`rename_history`/`alias` found
none of these constructs (only unrelated `#[serde(alias = ...)]` field-rename
shims and unrelated enum variant names like `TerminalStatus`). The only
place an old title could survive is incidentally, in raw chat-history JSONL
if it was ever echoed into a message — not a structured, queryable record.

### Grace period / alias table for stale names

**NOT FOUND.** No alias table or grace window for session names exists. A
search for "grace period" across the crate turns up only unrelated process
shutdown grace periods (`leader/server.rs:1397,2583`,
`leader/protocol.rs:224,286`) and flush/batching windows
(`agent/activity.rs:348,416`) — none concern title/name resolution.
Renaming is a synchronous, single-writer overwrite; nothing keeps the
pre-rename title resolvable afterward. (Session *id* resolution does have
"child" indirection logic — `resolve_local_session`/
`find_local_child_for_remote`, `persistence.rs:399-480` — but that resolves
a remote session id to a locally-restored child id, not an old name to a
session; it is not a naming grace period.)

**Confidence: high** on the mechanism and validation rules (directly cited
code with test coverage implied by named constants); **high** on the
negative finding for old-name retention and grace periods (exhaustive grep,
consistent with the "last write wins" doc comment).

---

## 5. Presence and State

### Does a session publish status externally?

Yes — the roster mechanism, purpose-built for this
(`agent/roster.rs:1-13`, module doc: "list of dashboard-sized summaries of
every session the leader hosts... Clients read it two ways: request/response
`x.ai/sessions/list` ... or broadcast notification `x.ai/sessions/changed`").

```rust
// agent/roster.rs:26-41
#[derive(Serialize, Deserialize, Clone, Copy, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RosterActivity {
    Working,
    Idle,
    NeedsInput,
    Dormant,
    Completed,
    Dead,
}
```
`RosterEntry` (`agent/roster.rs:53-81`): `session_id, title, cwd,
is_worktree, model_id, reasoning_effort, yolo, activity: RosterActivity,
last_turn_summary, resident: bool, last_change_unix_ms, origin`.

Broadcast to every connected ACP client (not only the owning session's own
client) via `gateway.forward_fire_and_forget(acp::ExtNotification::new(
SESSIONS_CHANGED_METHOD, ...))` in `emit_roster_changed`
(`agent/mvp_agent/session_lifecycle.rs:261-277`), method name
`x.ai/sessions/changed` (`agent/roster.rs:100`); also directly queryable via
`x.ai/sessions/list` → `build_roster()` (`session_lifecycle.rs:361-369`).

Underlying source of truth, `SessionLiveState`
(`session/handle.rs:23-38`):
```rust
pub(crate) enum SessionLiveState {
    Working, IdleResident, Dormant, Completed, DeadFailed, Attaching,
}
```
with an explicit doc comment: "A grok session has no terminal status field
on its own — it is a resumable log on disk — so 'liveness' is *residency +
turn-state*, not a pid... This is the data source the roster/dashboard
reads." (`session/handle.rs:14-21`). This state is **not** persisted to
`summary.json`; it lives only in the leader process's in-memory
`session_registry`.

### Does a session publish cwd externally?

Yes, in two places:

- **Roster** (local, live, to attached clients): `RosterEntry.cwd`
  (`agent/roster.rs:60`), populated for resident sessions in
  `resident_roster_entry`
  (`session_lifecycle.rs:316-351`; `h.display_cwd.clone().unwrap_or_else(||
  h.info.cwd.clone())`) and for dormant sessions straight from disk
  (`merge_roster`, `roster.rs:140`: `cwd: summary.info.cwd.clone()`).
  Broadcast the same way as activity.
- **Remote session-replica registry** (cross-host replication backend):
  `RegisterRequest.cwd: String` (`session_registry_client.rs:19-21`), sent
  once at first-prompt time (`agent/mvp_agent/acp_agent.rs:1640-1663`,
  `RegisterRequest{session_id, cwd: cwd_str, ...}` →
  `client.register(&reg_req).await`). The read-side `SessionRecord`
  (`session_registry_client.rs:76-97`) also carries `cwd` and a
  server-controlled `status: String` — but the client never *sends*
  `status`; the local `From<Summary> for SessionRecord` hardcodes
  `status: "local".to_string()` (`session_registry_client.rs:113`) — this
  registry does not carry idle/working presence, only replication
  bookkeeping.

### What updates these fields, and how often

Roster (`RosterActivity`/`cwd`) — **event/state-transition-triggered, not a
timer**:
- Spawn completion: `set_session_live_state(..., IdleResident)` then
  `push_roster_delta_upserted(&session_info.id)`
  (`agent/mvp_agent/agent_ops.rs:4906,4909`).
- Turn start: `push_roster_activity_delta(&arguments.session_id,
  RosterActivity::Working)` (`acp_agent.rs:1309-1312`).
- Turn end: activity recomputed as `NeedsInput` (if
  `handle.pending_interactions` is non-empty) or `Idle`, then
  `push_roster_activity_delta(...)` (`acp_agent.rs:1405-1416`).
- Teardown: `record_roster_delta(id, final_state)` →
  `emit_roster_changed(vec![], vec![id])` (removal delta)
  (`session_lifecycle.rs:229-240,389`).
- `resident_activity()` (`session_lifecycle.rs:280-314`) computes the live
  value on demand from `pending_interactions`/`current_prompt_id` locks plus
  `SessionLiveState`, precedence `NeedsInput` > `Working` > coarse lifecycle
  state.
- `cwd` is read live off `SessionHandle.display_cwd`/`info.cwd` whenever an
  entry is built — not independently pushed; it rides along with whatever
  delta/snapshot is emitted.

Remote registry (`cwd`, `status`) — **turn/lifecycle-triggered, fire-and-
forget, not periodic**:
- `register()` called once, from a `tokio::spawn`'d task, on the session's
  first prompt (`acp_agent.rs:1620-1663`), carrying `cwd` and machine/git
  metadata.
- `update()` called after each turn completes
  (`advance_last_turn`, `acp_agent.rs:1720-1749`, invoked at `:1773-1778`)
  and again once restore artifacts are durable (`advance_restorable_turn`,
  `:1754-1772`) — `UpdateRequest` (`session_registry_client.rs:55-68`) has
  no `cwd` field, so `cwd` is sent once and never re-sent.
- `finalize()` at session close (`session_lifecycle.rs:140`).

Separately, `resource_telemetry.rs` (`log_resource_usage`/
`report_resource_usage_if_due`, `:65-98`) is **not** a presence/status
publisher — it emits `ProcessResourceUsage` telemetry (RSS, threads, open
files, resident-session counts) to the telemetry pipeline
(`xai_grok_telemetry::session_ctx::log_event`), on a genuine periodic
cadence (`MIN_REPORT_INTERVAL = 5m` floor, `HEARTBEAT_INTERVAL = 1h`
guaranteed tick, `:12,16,41-58`). Process-level health telemetry only, not
readable by other sessions, not a session identity/cwd/activity broadcast.

**Confidence: high.** Direct citations for the enum, the struct, every
update trigger site, and an explicit doc comment stating the design
rationale ("liveness is residency + turn-state, not a pid").

---

## 6. Hooks and Environment Variables

Scope: `crates/codegen/xai-grok-shell/src` plus the two crates it delegates
actual child-process spawning to — `crates/codegen/xai-grok-hooks/src`
(hook execution engine) and `crates/codegen/xai-grok-mcp/src` (MCP client/
server spawning) — since `xai-grok-shell`'s own `util/hooks.rs` only does
hook-source *discovery*, and `session/mcp_servers.rs` only forwards to
`xai_grok_mcp::servers`.

### Hook child process env (command-type hooks)

Spawned in `crates/codegen/xai-grok-hooks/src/runner/command.rs`. HTTP-type
hooks (`runner/http.rs`) never spawn an OS process — they only interpolate a
URL — so they carry no OS env vars (noted separately below).

All vars below are set via `Command::env(...)` on that one hook's
`tokio::process::Command` — hook-scoped, not ambient. The child also
inherits the full ambient environment of the running `grok` process by
default, since neither this nor the MCP path ever calls `env_clear()`.

| Var | Value | Citation |
|---|---|---|
| `GROK_HOOK_EVENT` | firing hook event name (e.g. `PreToolUse`) | `runner/command.rs:197` |
| `GROK_HOOK_NAME` | `spec.name` | `runner/command.rs:198` |
| `GROK_SESSION_ID` | `ctx.session_id` | `runner/command.rs:199` |
| `GROK_WORKSPACE_ROOT` | workspace root path | `runner/command.rs:200` |
| `CLAUDE_PROJECT_DIR` | same value as `GROK_WORKSPACE_ROOT` (Claude-compat alias) | `runner/command.rs:202-204` |

```rust
// crates/codegen/xai-grok-hooks/src/runner/command.rs:188-206
let mut child = match cmd
    .stdin(std::process::Stdio::piped())
    .stdout(std::process::Stdio::piped())
    .stderr(std::process::Stdio::piped())
    .current_dir(ctx.workspace_root)
    // 1. user/plugin extra_env first (lowest precedence).
    .envs(&spec.extra_env)
    // 2. runner-injected vars last (highest precedence, always win).
    .env("GROK_HOOK_EVENT", envelope.hook_event_name.to_string())
    .env("GROK_HOOK_NAME", &spec.name)
    .env("GROK_SESSION_ID", ctx.session_id)
    .env("GROK_WORKSPACE_ROOT", env_root.as_ref())
    .env("CLAUDE_PROJECT_DIR", env_root.as_ref())
    .kill_on_drop(true)
    .spawn()
```

The 5 names are also centralized as `RUNNER_ALWAYS_SET_ENV`
(`crates/codegen/xai-grok-hooks/src/config.rs:205-211`), and
`strip_reserved_env_keys` (same file, ~line 606-616) strips any
user-supplied `extra_env` entry with these reserved names before spawn, so a
hook config file cannot spoof its own identity vars.

A hook's own `extra_env` (user/plugin-authored, from a JSON hook config's
`"env"` map) is applied via `.envs(&spec.extra_env)` at `command.rs:195` —
also `Command`-scoped, lower precedence than the 5 runner vars.

HTTP hooks (`runner/http.rs:153-163`) build a string map used only for
`${VAR}` substitution inside the target URL (same 5 names) — never an OS
process environment; no `Command` is spawned (a `reqwest` POST instead).

**NOT FOUND**: no `std::env::set_var` call anywhere in
`crates/codegen/xai-grok-hooks/src/` outside test-only helpers
(`test_support.rs`'s `EnvGuard`). Hook-injected identity vars are
exclusively `Command`-scoped.

### MCP server child process env

Stdio MCP servers spawn in `crates/codegen/xai-grok-mcp/src/servers.rs`,
via `start_mcp_server` → `apply_stdio_env`. All `Command`-scoped; again the
child inherits the full ambient parent environment since `env_clear()` is
never called on this path.

| Var | Value | Citation |
|---|---|---|
| every entry in the server's configured `env` list | user-supplied name/value pairs (`McpServerConfig::Stdio.env`, from `.mcp.json`/`config.toml`) | `servers.rs:4503-4506` |
| `GROK_SESSION_ID` | `ctx.session_id` (same live session id as hooks) | `servers.rs:4507-4509` |

```rust
// crates/codegen/xai-grok-mcp/src/servers.rs:4503-4510
fn apply_stdio_env(cmd: &mut Command, env: &[acp::EnvVariable], session_id: Option<&str>) {
    for env_variable in env {
        cmd.env(&env_variable.name, &env_variable.value);
    }
    if let Some(session_id) = session_id {
        cmd.env("GROK_SESSION_ID", session_id);
    }
}
```
Call site (`servers.rs:4579-4582`):
```rust
let mut cmd = Command::new(&program);
cmd.kill_on_drop(true).args(&spawn_args);
apply_stdio_env(&mut cmd, &env, ctx.session_id);
xai_grok_tools::util::detach_command(&mut cmd);
```
`ctx.session_id` comes from `McpSpawnCtx` (`servers.rs:4513-4518`,
`McpSpawnCtx::for_session`).

HTTP/SSE MCP servers (`servers.rs:4620` onward) carry the session id as an
HTTP header substitution (`expand_session_id_headers`, `servers.rs:
4364-4379`, `{{session_id}}`/`${session_id}`), not an OS env var — no child
process is spawned for those transports.

**NOT FOUND**: no `std::env::set_var` call in `crates/codegen/xai-grok-mcp/src/`
on the MCP spawn path (only `Command::env`).

### Hook-scoped vs. MCP-scoped vs. ambient — the crux distinction

Shared, but still scoped: `GROK_SESSION_ID` is set identically (via
`Command::env`) at both hook and MCP spawn sites (`runner/command.rs:199`,
`servers.rs:4508`; also `terminal/pty_session.rs:256-262` for the Bash/PTY
tool and `xai-grok-pager/src/notifications/hooks.rs:24` for pager
notification hooks). A repo-wide grep for `"GROK_SESSION_ID"` confirmed
every site uses `.env(...)`/`.envs(...)`, never `std::env::set_var` —
**`GROK_SESSION_ID` is never ambient.**

True ambient (process-wide) env vars, inherited by both hook and MCP
children because neither spawn path calls `env_clear()`:

| Var | Set via `std::env::set_var` at | Notes |
|---|---|---|
| `XAI_API_KEY` | `agent/mvp_agent/acp_agent.rs:235` (loaded from `auth.json` at session `initialize()`) | `unsafe { std::env::set_var("XAI_API_KEY", &api_key) };` |
| `XAI_API_KEY` | `extensions/auth.rs:89` (ACP `x.ai/setApiKey` handler) | `unsafe { std::env::set_var("XAI_API_KEY", k) };` |
| `XAI_API_KEY` | `sampling/error.rs:670,679` | recovery-path key refresh |
| `GROK_CODE_XAI_API_KEY` | `sampling/error.rs:682` | legacy alias |
| `GROK_LEADER_SOCKET` | `crates/codegen/xai-grok-pager-bin/src/main.rs:1985` (`--leader-socket` flag) | `unsafe { std::env::set_var(xai_grok_shell::leader::LEADER_SOCKET_ENV, socket) };` — constant `LEADER_SOCKET_ENV = "GROK_LEADER_SOCKET"` defined at `leader/lock.rs:44` |
| `GROK_AUTO_PERMISSION_MODE` | `util/config/permissions.rs:714,761,828` | non-secret UI flag, still ambient |
| assorted `GROK_*` UI/telemetry toggles (`GROK_TELEMETRY_ENABLED`, `GROK_WORKFLOWS`, `GROK_GOAL_*`, etc.) | `util/config/resolve/*.rs`, `agent/config_tests.rs` | mostly test-only/non-sensitive |

Because none of these `set_var` calls are ever undone before spawning a hook
or MCP child, any of these values present in the process environment at
spawn time flow into every hook and every MCP stdio server child by
ordinary env inheritance — no explicit `.env()` call required. This is
architecturally different from the deliberately per-spawn-injected vars
above.

### Session id / leader socket / auth material — ambient leak check

- **`GROK_SESSION_ID`**: always `Command`-scoped (confirmed above) — not
  ambient.
- **`GROK_LEADER_SOCKET`**: **is ambient.** Set process-wide via
  `std::env::set_var` at `main.rs:1985` whenever `--leader-socket <path>` is
  passed. From then on it sits in the process environment and is inherited
  by every child, including hook scripts and MCP stdio servers, with no
  scrubbing. (It's also explicitly re-propagated, `Command`-scoped, when
  spawning the leader subprocess itself — `leader/mod.rs:1693-1694` — that
  call is intentional; the ambient leak is the unintentional side channel
  to *unrelated* children.)
- **First-party auth/token material (`XAI_API_KEY` etc.)**: **is ambient**,
  and the codebase demonstrates it already recognizes this as a problem —
  but only mitigates it for one child-process type. `agent/config.rs:
  667-677` defines:
  ```rust
  pub(crate) const FIRST_PARTY_CREDENTIAL_ENV_VARS: &[&str] = &[
      crate::agent::auth_method::XAI_API_KEY_ENV_VAR,        // "XAI_API_KEY"
      crate::agent::auth_method::LEGACY_XAI_API_KEY_ENV_VAR, // "GROK_CODE_XAI_API_KEY"
      "GROK_AUTH",
      "GROK_AUTH_PATH",
      "GROK_DEPLOYMENT_KEY",
      "GROK_EXTRA_AUTH_KEY",
      "GROK_TRACE_UPLOAD_CREDENTIALS_FILE",
      "OTEL_EXPORTER_OTLP_HEADERS",
      "GROK_INTERNAL_OTLP_HEADERS",
  ];
  ```
  Consumed by `scrub_first_party_credentials`
  (`auth/auth_provider.rs:265-269`, `cmd.env_remove(var)` per name), called
  **only** on the external "auth provider" credential-helper child
  (`auth_provider.rs:416`; doc comment at `:415`: "Scrub last so nothing
  above can reintroduce a first-party credential"; comment at `:276-279`
  explicitly worries about "the `GROK_AUTH_PROVIDER_*` credentials in
  [grandchildren's] env"). A repo-wide grep for
  `scrub_first_party_credentials`/`FIRST_PARTY_CREDENTIAL_ENV_VARS` across
  `xai-grok-shell`, `xai-grok-mcp`, and `xai-grok-hooks` found **exactly one
  call site**. Neither the MCP stdio spawn path
  (`crates/codegen/xai-grok-mcp/src/servers.rs`) nor the hook spawn path
  (`crates/codegen/xai-grok-hooks/src/runner/command.rs`) calls it, calls
  `env_clear()`, or calls any `env_remove()` for these names.
  - Net effect (INFERRED from the above, combining confirmed facts): an MCP
    stdio server declared by a project's `.mcp.json`, or an arbitrary hook
    script from `.grok/hooks`, inherits `XAI_API_KEY` (and any other
    `FIRST_PARTY_CREDENTIAL_ENV_VARS` entry) whenever it's set in the
    running `grok` process's environment — e.g. after `acp_agent.rs:235`
    loads it at session start, or after the `x.ai/setApiKey` handler runs.
    Nothing blocks a third-party MCP server or hook script from reading
    `os.environ["XAI_API_KEY"]`, or from reading `GROK_LEADER_SOCKET` and
    dialing the leader's (unauthenticated, per §1) control socket. This is
    exactly the pattern the task description flagged — an ambient variable
    leaking into an unrelated child — and the codebase's own credential-
    scrub allowlist shows the risk was recognized, just not applied on
    these two spawn paths.

### Summary table

| Mechanism | Scope | Example vars |
|---|---|---|
| `Command::env`/`envs` on the hook's own `Command` | Hook-scoped (this spawn only) | `GROK_HOOK_EVENT`, `GROK_HOOK_NAME`, `GROK_SESSION_ID`, `GROK_WORKSPACE_ROOT`, `CLAUDE_PROJECT_DIR`, hook's own `extra_env` |
| `Command::env` on the MCP server's own `Command` | MCP-scoped (this spawn only) | server's configured `env` map, `GROK_SESSION_ID` |
| `std::env::set_var` on the whole `grok`/pager process | Ambient — inherited by hooks, MCP servers, and every other child (neither spawn path calls `env_clear()`) | `XAI_API_KEY`, `GROK_CODE_XAI_API_KEY`, `GROK_LEADER_SOCKET`, `GROK_AUTO_PERMISSION_MODE`, misc. `GROK_*` toggles |
| `Command::env_remove` scrub of `FIRST_PARTY_CREDENTIAL_ENV_VARS` | Applied only to the external auth-provider helper child — not to hooks or MCP servers | n/a |

**Confidence: high** on what's set and where (direct `.env`/`set_var` call
sites cited); **high** on the ambient-leak conclusion for `GROK_LEADER_SOCKET`
and `XAI_API_KEY` (grep-confirmed absence of scrubbing on the hook/MCP
paths); the final "net effect" sentence is explicitly marked INFERRED since
it describes the consequence of the confirmed code paths rather than a
directly-stated behavior.

---

## Overall confidence and caveats

- All six sections are grounded in direct `file:line` citations gathered
  from reading the actual source in this checkout (HEAD `07b2f71`).
- Three sections end in a **firm negative**: no session-to-session
  addressing/messaging (§3), no discoverable `active_sessions.json`-style
  file (§2, though adjacent mechanisms exist), no leader-socket
  authentication (§1). Each negative is backed by exhaustive grep across the
  relevant crates, not merely an unexamined gap.
- Confidence is lower only where a conclusion required combining multiple
  confirmed facts into a security implication (the "net effect" paragraph
  in §6) — those spots are explicitly marked INFERRED rather than stated as
  fact.
- This document does not cover crates outside `xai-grok-shell`/
  `xai-grok-hooks`/`xai-grok-mcp`/`xai-grok-pager*` in exhaustive depth; a
  few citations reach into `xai-grok-shared`, `xai-grok-home`, and
  `xai-grok-telemetry` where the primary crate re-exports or calls into
  them, and those citations were spot-verified but not exhaustively swept.
