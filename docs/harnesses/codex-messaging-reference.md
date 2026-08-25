<!-- Provenance: external read-only source review of openai/codex, main,
     HEAD c9b19deb09c1841ce7acc33ddb96276030936a29. Filed here unmodified.
     Produced from docs/harnesses/prompts/codex-ipc-review.md. Claims carry file:line
     citations against that commit and will drift as it moves.
     See comparison-note.md for what this means for agent-bus. -->

# Codex Inter-Agent Messaging: Source Reference

**Repo:** `openai/codex`
**Branch:** `main`
**HEAD reviewed:** `c9b19deb09c1841ce7acc33ddb96276030936a29` (2026-08-23, "Distinguish Guardian review threads from subagents (#40221)")
**Method:** Read-only static analysis of the checked-out source. No build, no run. Every behavioral claim below is cited `file:line` against this commit. Claims labeled `INFERRED` combine directly-observed facts into a consequence not literally stated in the code; everything else is a direct reading of source or test fixtures.

**Scope boundary:** this document was produced by five parallel research passes, each sampling a cluster of crates rather than exhaustively reading the ~100-crate `codex-rs` workspace. Each section below states what was read in full vs. sampled.

---

## Summary vs. the PR #39092 description

The task description said, of PR #39092 ("queue messages for existing sessions"): it adds `codex queue --thread <THREAD> --message <TEXT>`, submits via a `thread/queue/add` app-server API, resolves sessions by UUID or exact name across interactive/exec/custom sources, **rejects ambiguous or duplicate names**, supports local and explicit remote app servers, and rejects empty messages and image attachments. Checked against the code:

| Claim | Verdict |
|---|---|
| `codex queue --thread <THREAD> --message <TEXT>` exists, submits via `thread/queue/add` | **Confirmed** — `cli/src/queue_cmd.rs:12-27` |
| Resolves by UUID or exact name, across interactive/exec/custom sources | **Confirmed** — `tui/src/session_queue_commands.rs:87-101`, `SessionNameMatch::FirstIncludingNonInteractive` |
| **Rejects ambiguous or duplicate names** | **FALSE.** No such rejection exists anywhere in the resolution path. Duplicates are permitted; the resolver silently picks the most-recently-updated match (`tui/src/session_archive_commands.rs:256-265`). No `UNIQUE` constraint on the `name` column (`state/migrations/0041_threads_name.sql:1`), no "ambiguous"/"duplicate name" error type anywhere in the tree. |
| Supports local and explicit remote app servers | **Confirmed** — `--remote`/`--remote-auth-token-env` flags, `cli/src/main.rs:972-983` |
| Rejects empty messages and image attachments | **Confirmed, but CLI-side only, not protocol-side.** The wire protocol (`ThreadQueueAddParams`/`UserInput`) supports image content; the server only rejects *remote* image URLs. The blanket "no images" and "no empty message" rules are enforced exclusively by the `codex queue` CLI subcommand (`cli/src/queue_cmd.rs:19,45-47`), not by `thread/queue/add` itself. |

---

## 1. The app-server

**What it is.** `codex-app-server` is a standalone binary (`app-server/src/main.rs`, entry at line 65, driven by `run_main_with_transport_options`, `app-server/src/lib.rs:457`). It is not one-process-per-session: a single process serves whatever transport it's bound to and multiplexes many client connections at once (`connections: HashMap<ConnectionId, ConnectionState>`, `app-server/src/lib.rs:932`). There is also an **embedded/in-process library mode** (`app-server/src/in_process.rs:1-40`) that runs the same `MessageProcessor` over in-memory channels with no process boundary, used by `codex-app-server-client`'s `InProcessAppServerClient` (`app-server-client/src/lib.rs:300-318`) — so depending on the caller, "the app-server" is either a persistent background daemon or an in-process library, never a per-turn subprocess.

**Addressing.** `AppServerTransport` (`app-server-transport/src/transport/mod.rs:74-80`) has four variants:
- `Stdio` (default, `mod.rs:114`)
- `UnixSocket { socket_path }` (`unix://`, `mod.rs:121-144`), default path `$CODEX_HOME/app-server-control/app-server-control.sock` (`mod.rs:54-64`)
- `WebSocket { bind_address }` — real TCP (`ws://IP:PORT`, `mod.rs:150-155`)
- `Off`

No named pipes, no HTTP RPC surface (the WebSocket listener does expose plain-HTTP `/healthz`/`/readyz` probes, `app-server-transport/src/transport/websocket.rs:148-150`, but the RPC channel itself is WebSocket, not request/response HTTP).

**Framing/encoding.** Every message is a JSON-RPC-2.0-*shaped* object serialized with `serde_json` — the crate's own doc comment states: *"We do not do true JSON-RPC 2.0, as we neither send nor expect the `"jsonrpc": "2.0"` field"* (`app-server-protocol/src/rpc.rs:1-2`). Framing differs by transport:
- **Stdio**: newline-delimited JSON, one object per line (`app-server-transport/src/transport/stdio.rs:45-50,84-89`).
- **UnixSocket and TCP**: both are upgraded to the **WebSocket protocol**, one JSON-RPC message per WebSocket text frame. For the Unix socket, the server calls `tokio_tungstenite::accept_async(stream)` directly on the accepted UDS stream (`unix_socket.rs:79-87`) — i.e. WebSocket-over-UDS, not raw JSON-RPC-over-UDS.

**Handshake.** Yes — `initialize` is a real request (`common.rs:488-491`) carrying `ClientInfo` and `InitializeCapabilities` (experimental-API opt-in, attestation opt-in, notification opt-outs, MCP extensions — `v1.rs:29-66`), answered with `InitializeResponse { user_agent, codex_home, platform_family, platform_os }` (`v1.rs:70-80`). Every other request is gated on it: `dispatch_initialized_client_request` returns `invalid_request("Not initialized")` until `initialize` has completed on that connection (`message_processor.rs:887-888`).

**Lifecycle.**
- *Spawning*: `codex app-server` runs the server in the foreground directly (`cli/src/main.rs:160`, struct `AppServerCommand` at `547-596`). Separately, `codex app-server daemon {start,restart,stop,bootstrap,...}` (`cli/src/main.rs:700-726`) manages a *detached* background instance via `codex-app-server-daemon`: `Daemon::start`/`bootstrap_locked` spawn the managed `codex` binary with `["app-server", "--listen", "unix://"]`, detached via `libc::setsid()` in a `pre_exec` hook (`app-server-daemon/src/backend/pid.rs:173-180,413-421`), tracked by a PID file recording pid + process start time (`PidRecord`, `pid.rs:37-42`). The remote-control feature auto-starts the daemon on demand (`ensure_remote_control_ready`, `app-server-daemon/src/lib.rs:201-206,494-511`).
- *Leader/singleton election*: two independent mechanisms. (1) A startup lock file `app-server-startup.lock` acquired via `std::fs::File::lock()` before binding the socket (`unix_socket.rs:134-156`; call site `app-server/src/lib.rs:609-617`). (2) The daemon layer has its own `daemon.lock` operation lock (`libc::flock(..., LOCK_EX|LOCK_NB)`, `app-server-daemon/src/lib.rs:830-848,713-726`), plus active liveness probing — it connects to the control socket (`client::probe`) and cross-checks the PID-file record before starting a new instance.
- *Stale socket handling*: `prepare_control_socket_path` (`unix_socket.rs:93-132`) first tries `UnixStream::connect(socket_path)`; a live answer means "in use" and it errors `AddrInUse` (`96-108`). A `ConnectionRefused` means nothing is listening; it then verifies the path is actually a socket file (`codex_uds::is_stale_socket_path`, `uds/src/lib.rs:24-26,154-159`) and calls `tokio::fs::remove_file(socket_path)` before rebinding (`unix_socket.rs:131`). On clean shutdown, `ControlSocketFileGuard::drop` also removes the socket file (`unix_socket.rs:174-190`).

**Confidence: High.** Entry points, transport enum, framing, handshake, and daemon PID/lock logic were all read in full. Sampled rather than exhaustively read: `message_processor.rs`, `request_processors/`, and the `remote_control/` submodule (cloud pairing/enrollment — cited in §2 where touched, not read end-to-end).

---

## 2. Authentication and trust

**Local (Unix-domain-socket) transport: no application-level client authentication.** A repo-wide search of the app-server crate family (`app-server`, `app-server-daemon`, `app-server-protocol`, `app-server-transport`, `app-server-client`, `app-server-test-client`, `uds`, `stdio-to-uds`) for `peercred|SO_PEERCRED|getsockopt|ucred|peer_cred` returns **NOT FOUND** — no `SO_PEERCRED`/peer-credential inspection anywhere in that call path (the repo's only such code, `tui/src/ide_context/ipc.rs:423-581`, is an unrelated IDE-context channel). No bearer-token file, no TLS, no per-connection identity check on accept. Trust reduces entirely to filesystem permissions:
- Socket directory mode **`0700`**: `SOCKET_DIR_MODE: u32 = 0o700` (`uds/src/lib.rs:100`), enforced/repaired by `prepare_private_socket_directory` (`uds/src/lib.rs:107-138`).
- Socket file mode **`0600`**: `CONTROL_SOCKET_MODE: u32 = 0o600` (`app-server-transport/src/transport/unix_socket.rs:22`), applied right after bind by `set_control_socket_permissions` (`unix_socket.rs:158-167`).

Stated plainly, matching the framing requested: **local app-server auth is filesystem permissions only, not credential inspection.** Any process able to open that socket path (same user, or root) can speak the full protocol once past `initialize` — and `initialize` negotiates *capabilities*, not *identity*. The `run_control_socket_acceptor` accept loop (`unix_socket.rs:46-91`) contains no auth step at all.

**Stdio transport**: trust is inherited from whoever owns the child's stdio pipes (the parent process that spawned `codex app-server`) — no additional auth layer (`stdio.rs:24-101` has no auth logic).

**Remote/TCP transport does exist and does have real auth — but it's optional for loopback binds.** `AppServerTransport::WebSocket { bind_address }` binds a real TCP listener (`websocket.rs:129-170`). Auth modes, via `--ws-auth` (`app-server-transport/src/transport/auth.rs:27-56`):
- `CapabilityToken`: a shared secret from `--ws-token-file`/`--ws-token-sha256`, checked as a `Bearer` header against a SHA-256 digest with constant-time compare (`auth.rs:71-86,226-238,283-290`).
- `SignedBearerToken`: HS256-signed JWT against a ≥32-byte shared secret file, with optional issuer/audience/clock-skew checks (`auth.rs:24,306-366,388-398`).

**Auth is not mandatory.** `is_unauthenticated_non_loopback_listener` (`auth.rs:266-271`) only refuses to start when the bind address is **non-loopback and no auth mode is configured** (`websocket.rs:135-142`). A `127.0.0.1` listener is permitted to run with **zero bearer-token auth**, protected only by being bound to localhost — the startup banner itself distinguishes "websocket auth is required for non-localhost listeners" from plain "binds localhost only" (`websocket.rs:70-76`). No TLS/`rustls` termination exists on this inbound listener (the `rustls` usage in these crates is for the *outbound* remote-control client connecting to a cloud pairing service, `remote_control/websocket.rs:42,1334` — unrelated to inbound auth).

**Local vs. remote, summarized:**
| | Local (Unix socket) | Remote (TCP `ws://`/`wss://`) |
|---|---|---|
| Auth mechanism | Directory mode `0700` + socket mode `0600`; no peer-cred check | Optional bearer token (static-hash or signed JWT); mandatory only for non-loopback binds |
| TLS | N/A (local IPC) | None inside the app-server itself for this listener |
| Client-side enforcement | — | `websocket_url_supports_auth_token` only sends the token over `wss://` or loopback `ws://` (`app-server-client/src/remote.rs:117-125`) |

A separate "remote control" feature (`app-server-transport/src/transport/remote_control/*`) authenticates the app-server *outbound* to a cloud pairing service using the user's ChatGPT auth headers (`remote_control/auth.rs:20-79`) — this is not inbound client authentication and is architecturally distinct from the `--listen` transports above.

**Confidence: High** for Unix-socket permissions and WebSocket auth modes (both read in full, plus a repo-wide grep confirming no peer-credential mechanism exists anywhere in the app-server family). **Medium** on the full security model of the "remote control" pairing subsystem — only `remote_control/mod.rs` (partially) and `auth.rs` were read; `enroll.rs`, `protocol.rs`, `server_api.rs`, and `websocket.rs` in that submodule were not.

---

## 3. `thread/queue/add` and the wider API

**Method surface (exhaustive count, not a sample — verified by grepping the generating macro blocks in full):**

All wire methods are declared in one file, `app-server-protocol/src/protocol/common.rs`, via four macro invocations:

- **`ClientRequest`** (`client_request_definitions!`, `common.rs:487-1393`) — **155 distinct method strings**, e.g. `"initialize"` (488), `"thread/start"` (505), `"thread/resume"` (511), `"thread/fork"` (517), `"thread/archive"` (523), `"thread/delete"` (528), `"thread/list"` (692), `"thread/read"` (778), `"thread/queue/add"` (578, marked `#[experimental("thread/queue/add")]`), `"thread/queue/list"` (584), `"thread/queue/update"` (590), `"thread/queue/delete"` (596), `"thread/queue/reorder"` (602), `"thread/queue/start"` (608), `"thread/name/set"` (557-561), `"turn/start"` (961), `"turn/steer"` (967), `"turn/interrupt"` (973), `"project/list"` (699), `"fs/readFile"` (901), `"fs/watch"` (936), `"command/exec"` (1248), `"process/spawn"` (1274), `"account/login/start"` (1178), plus deprecated legacy methods (`"getConversationSummary"`, `"gitDiffToRemote"`, `"getAuthStatus"`, `"fuzzyFileSearch"`).
- **`ServerRequest`** (`server_request_definitions!`, `common.rs:1663-1734`) — **11 methods**: `"item/commandExecution/requestApproval"` (1667), `"item/fileChange/requestApproval"` (1674), `"item/tool/requestUserInput"` (1680), `"mcpServer/elicitation/request"` (1686), `"item/permissions/requestApproval"` (1692), `"item/tool/call"` (1698), `"account/chatgptAuthTokens/refresh"` (1703), `"attestation/generate"` (1709), `"currentTime/read"` (1716), plus deprecated `applyPatchApproval`/`execCommandApproval` (1724-1730).
- **`ServerNotification`** (`server_notification_definitions!`, `common.rs:1818-1933`) — **77 methods**, e.g. `"error"` (1820), `"thread/started"` (1821), `"thread/queue/changed"` (1834, experimental), `"thread/status/changed"` (see §7), `"turn/started"` (1846), `"turn/completed"` (1848), `"item/started"` (1852), `"item/completed"` (1857), `"item/agentMessage/delta"` (1862), `"command/exec/outputDelta"` (1866).
- **`ClientNotification`** (`client_notification_definitions!`, `common.rs:1954-1956`) — **1 method**: `Initialized` → wire string `"initialized"` (confirmed on the wire, `cli/tests/queue.rs:60`).

**`thread/queue/add` — exact structs.** Declared in `app-server-protocol/src/protocol/v2/thread.rs`.

```rust
// thread.rs:875-882
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, JsonSchema, TS)]
#[serde(rename_all = "camelCase")]
#[ts(export_to = "v2/")]
pub struct ThreadQueueAddParams {
    pub thread_id: String,
    pub input: Vec<UserInput>,
    pub client_user_message_id: String,
}

// thread.rs:884-889
pub struct ThreadQueueAddResponse {
    pub queued_submission: QueuedSubmission,
}

// thread.rs:866-873
pub struct QueuedSubmission {
    pub id: String,
    pub input: Vec<UserInput>,
    pub client_user_message_id: String,
}
```

There is no separate uuid/name field split — `thread_id: String` is the sole reference, validated server-side via `ThreadId::from_string` (`app-server/src/request_processors/thread_queue_processor.rs:241`). `input: Vec<UserInput>` uses the shared enum (`protocol/v2/turn.rs:288-325`), which *does* have `Image`/`LocalImage` variants at the protocol level — the server handler only rejects **remote** image URLs (`validate_user_input_image_urls`, `turn_processor.rs:33-45`), not local ones.

**CLI resolution (`codex queue --thread <THREAD> --message <TEXT>`).** Struct at `cli/src/queue_cmd.rs:12-27`, `message` uses `value_parser = NonEmptyStringValueParser::new()`. Resolution, `tui/src/session_queue_commands.rs:80-101`:
```rust
let thread_id = if let Ok(thread_id) = ThreadId::from_string(target) {
    thread_id
} else {
    let thread = lookup_session_by_exact_name(
        app_server, codex_home, target, /*archived*/ false,
        SessionNameMatch::FirstIncludingNonInteractive,
    ).await?.ok_or_else(|| eyre!("No active session found matching '{target}'."))?;
    ThreadId::from_string(&thread.id)...
};
```
UUID first, then exact name (`FirstIncludingNonInteractive` covers interactive, exec, and non-interactive/Atlas sources). As detailed in the "Corrections" table above, **duplicate names are not rejected** — `lookup_session_by_exact_name` (`tui/src/session_archive_commands.rs:187-275`) keeps the most-recently-updated match and silently discards the rest; no "Ambiguous"/duplicate-name error exists anywhere in `tui/src/`, `cli/src/`, `app-server-client/src/`.

**Local vs. remote app-server.** `InteractiveRemoteOptions` (`cli/src/main.rs:972-983`): `--remote <ADDR>` (accepts `ws://`, `wss://`, `unix://`) and `--remote-auth-token-env <ENV_VAR>`. Without `--remote`, the CLI prefers an already-running local daemon and explicitly refuses to silently fall back to an embedded server if one is running (`tui/src/session_queue_commands.rs:37-46`).

**Empty-message / image rejection — CLI-side only.** `cli/src/queue_cmd.rs:19` (clap non-empty parser) and `queue_cmd.rs:45-47`:
```rust
if !cli.images.is_empty() {
    anyhow::bail!("`codex queue` does not support image attachments");
}
```
Confirmed by tests `queue_rejects_empty_message` and `queue_rejects_image_attachments` (`cli/tests/queue.rs:126-151`).

**Real example wire frames**, reconstructed verbatim from `cli/tests/queue.rs` and `app-server/tests/suite/v2/thread_queue.rs` (fields, not invented):
```rust
// queue.rs:38-43
assert_eq!(initialize["method"], "initialize");
assert_eq!(initialize["params"]["capabilities"]["experimentalApi"], true);
// queue.rs:59-60
assert_eq!(initialized["method"], "initialized");
// queue.rs:62-64
assert_eq!(request["method"], "thread/queue/add");
// queue.rs:105-106
assert_eq!(request["params"]["threadId"], THREAD_ID);         // THREAD_ID = "123e4567-e89b-12d3-a456-426614174000" (queue.rs:16)
assert_eq!(request["params"]["input"][0]["text"], "do the thing");
// queue.rs:66-76 (fake server's success response)
json!({
    "id": request["id"],
    "result": {
        "queuedSubmission": {
            "id": "queued-submission-id",
            "input": request["params"]["input"],
            "clientUserMessageId": request["params"]["clientUserMessageId"],
        },
    },
})
```
Reconstructed request/response pair:
```json
{"id": 1, "method": "thread/queue/add", "params": {"threadId": "123e4567-e89b-12d3-a456-426614174000", "input": [{"type": "text", "text": "do the thing", "textElements": []}], "clientUserMessageId": "<uuid-v7>"}}
{"id": 1, "result": {"queuedSubmission": {"id": "queued-submission-id", "input": [...], "clientUserMessageId": "<uuid-v7>"}}}
```
Capacity-limit error, `app-server/tests/suite/v2/thread_queue.rs:337-354`: `error.error.message == "queue cannot contain more than 100 submissions"`.

**Acknowledgement and error shape.** Every request is correlated by `id` (`app-server-protocol/src/rpc.rs:46-88`):
```rust
pub struct JSONRPCRequest { pub id: RequestId, pub method: String, pub params: Option<serde_json::Value>, pub trace: Option<W3cTraceContext> }
pub struct JSONRPCResponse { pub id: RequestId, pub result: Result }
pub struct JSONRPCError { pub error: JSONRPCErrorError, pub id: RequestId }
pub struct JSONRPCErrorError { pub code: i64, pub data: Option<serde_json::Value>, pub message: String }
```
Numeric codes (`app-server/src/error_code.rs:3-8`): `INVALID_REQUEST_ERROR_CODE = -32600`, `METHOD_NOT_FOUND_ERROR_CODE = -32601`, `INVALID_PARAMS_ERROR_CODE = -32602`, `INTERNAL_ERROR_CODE = -32603`, `OVERLOADED_ERROR_CODE = -32001`, plus a string code `INPUT_TOO_LARGE_ERROR_CODE = "input_too_large"`. `thread/queue/add` maps its internal `QueueServiceError` (`ext/queue/src/service.rs:46-62`: `Storage`, `InvalidPayload`, `InvalidAttachment`, `CoreSubmissionError`, `InvalidInput`, `InputTooLarge{actual_chars}`) to this shape via `queue_error()` (`thread_queue_processor.rs:309-322`).

**Confidence: High.** Method-name counts came from reading the four macro-invocation blocks in full and cross-checking counts programmatically, not sampling. CLI/protocol struct definitions and test fixtures were read directly, not summarized secondhand.

---

## 4. Delivery semantics

This is the load-bearing question for the agent-bus comparison, so the finding is stated first: **queued messages are persisted to a SQLite database on disk, not held only in an in-process channel/HashMap.**

**Persistence path.** `thread_queue_processor.rs::add()` → `QueuedItemService::enqueue` (`ext/queue/src/service.rs:264-279`, serializes to JSON) → storage trait `QueueStore` → concrete `LocalQueueStore` (`thread-store/src/queue_store.rs:97-117`) → `SqliteQueueStore::enqueue`, a real `INSERT INTO queued_items (...) RETURNING id, thread_id, payload_json` (`state/src/runtime/queued_items.rs:83-104`). Schema (`state/queue_migrations/0001_queued_items.sql:1-8`):
```sql
CREATE TABLE queued_items (
  id TEXT PRIMARY KEY NOT NULL, thread_id TEXT NOT NULL, payload_json TEXT NOT NULL,
  queue_order INTEGER NOT NULL, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
);
```
DB file: `queue_1.sqlite` under Codex home (`QUEUE_DB_FILENAME`, `state/src/sqlite.rs:32`, resolved by `queue_db_path()`, `sqlite.rs:154-156`). **Queue persistence requires the SQLite-backed thread store** — for an `InMemory` thread store config, there is no queue backend at all (`queue_service` is `None`, with the code comment "Queue persistence requires SQLite, so in-memory thread stores ... do not have a queue backend," `app-server/src/message_processor.rs:292-299`); every `thread/queue/*` call then fails synchronously with `"user message queue is unavailable"` (`thread_queue_processor.rs:231-235`). The DB write happens *before* any in-process wake attempt (`enqueue()` calls `wake_if_loaded` only after the store call returns, `service.rs:277`).

**Behavior by target state:**
- **Busy/working.** The write still succeeds unconditionally — no busy/idle branch gates `enqueue` itself (`thread_queue_processor.rs:79-89`). After the write, `wake_if_loaded` → `emit_thread_idle_lifecycle_if_idle` immediately no-ops if a turn is active (`core/src/tasks/lifecycle.rs:43-48`): the item just sits in the DB until the active turn finishes and core's own idle-lifecycle hook fires. An explicit `thread/queue/start` call while busy is turned into a synchronous error, `"thread already has an active or pending turn"` (`thread_queue_processor.rs:199-213`; test `queue_start_while_active_returns_busy_and_preserves_the_queue`, `thread_queue.rs:632-704`, confirms error code `-32600` and that the item survives).
- **Idle (loaded in-process).** `wake_if_loaded` finds the thread and, not being busy, dispatches: lists the head queue item, calls `thread.start_turn_if_idle(...)`, and on success deletes the row (`ext/queue/src/service.rs:399-461`, delete at line 438). Confirmed by `idle_queue_dispatch_preserves_client_id` (`thread_queue.rs:358-416`).
- **"Stale"/"already exited."** Two distinguishable cases in the code (the code itself doesn't use these exact words):
  - *Thread not currently loaded in this process* ("cold"): `require_thread` falls back to `thread_store.read_thread(...)` (`thread_queue_processor.rs:243-274`). Enqueue still succeeds as a pure DB write **unless the thread is archived**: `if stored.archived_at.is_some() { return Err(invalid_request(...)) }` (lines 268-272) — `"session {thread_id} is archived. Run \`codex unarchive {thread_id}\` to unarchive it first."` There is no separate "exited but not archived" block — only archival gates the write.
  - *Thread loaded but the agent itself is gone*: the background poller refuses to wake it when `agent_status()` is `Running | Interrupted | Shutdown | NotFound` (`ext/queue/src/service.rs:217-225`) — `Shutdown` fires on `EventMsg::ShutdownComplete` (`core/src/agent/status.rs:18`), `NotFound` when a sub-agent lookup fails (`core/src/agent/control.rs:335,338`). The item stays in the DB and is simply skipped that pass.
  - *Cold-thread recovery*: `on_thread_resume` marks the thread for a rescan (`service.rs:529-538`); a 10-second background poller `watch_external_messages` (`service.rs:91-244`, interval `service.rs:95`) picks up queue changes for resumed/never-loaded threads and dispatches them. Confirmed end-to-end by `cold_thread_resume_dispatches_a_persisted_queued_submission` (`thread_queue.rs:418-489`), which drops one `TestAppServer`, constructs a brand-new one against the same Codex home, and shows the still-queued item dispatch after resume.

**Delivery guarantee: `INFERRED` — at-least-once in principle (retried on every idle/resume/poll trigger until dispatch succeeds), effectively exactly-once in the non-crash case, with no built-in dedup.** Directly observed: mutating queue operations serialize through a per-thread `dispatch_lock`/`tokio::sync::Mutex` within one process (`service.rs:246-262`, used at 272/306/339/360/374/552), preventing intra-process double-dispatch races. `INFERRED` risk: `start()`/`dispatch_if_idle()` call `start_turn_if_idle(...)` and only *afterward* delete the row (`service.rs:390-397,433-440`) — this is not one atomic transaction, so a crash in that narrow window could in principle leave a dispatched item to be redelivered. This was not confirmed against a test or comment; flagged as inference, not a proven bug. No idempotency key is checked before insert — `client_user_message_id` is only for client-side correlation and is auto-filled with `Uuid::now_v7()` if absent (`service.rs:501`); grepped `dedup`/`idempot` across the queue-related crates — **NOT FOUND**.

**Redelivery / TTL / dead-letter: NOT FOUND.** Grepped case-insensitively for `expir`, `dead.?letter`, `\bttl\b`, `redeliver`, `dedup`, `idempot` across `ext/queue/src`, `state/src/runtime/queued_items.rs`, `thread-store/src/queue_store.rs`, `app-server/src/request_processors/thread_queue_processor.rs` — no matches. The only things that remove a never-dispatched item are successful dispatch, an explicit `thread/queue/delete`/`reorder` call, or full **thread deletion** (`delete_threads_strict` purges `queued_items` rows, `state/src/runtime/threads.rs:1108-1133`, backed by `DELETE FROM queued_items WHERE thread_id = ?`, `queued_items.rs:203-210`). Archiving a thread does **not** purge its queue, only blocks new adds. There is a capacity cap, not a TTL: `pub const MAX_QUEUE_ITEMS: usize = 100` (`state/src/lib.rs:94`), enforced in the enqueue SQL's `WHERE (SELECT COUNT(*) ...) < ?` (`queued_items.rs:91`), surfaced as `QueueServiceError::InputTooLarge` (test: `queue_rejects_messages_after_reaching_its_capacity`, `thread_queue.rs:317-357`).

**What the sender learns.** The `thread/queue/add` response confirms **persistence only** — `ThreadQueueAddResponse { queued_submission: QueuedSubmission { id, input, client_user_message_id } }` (`thread.rs:869-889`) has no delivery/status field, and is returned synchronously right after the DB write (`thread_queue_processor.rs:79-89`). The `codex queue` CLI prints only `"Queued message {id} for thread {thread_id}."` (`tui/src/session_queue_commands.rs:74-77`) and does not wait for delivery. Actual dispatch is observable only asynchronously, and only to currently-subscribed live RPC connections: a lightweight `ThreadQueueChangedNotification { thread_id }` fires on every mutation (`thread.rs:1903-1905`, dispatched via `AppServerExtensionEventSink::emit`, `app-server/src/extensions.rs:177-213` — an unsubscribed/disconnected client never gets it, and it is not itself retried), plus the normal turn-lifecycle notifications (`item/started` with a `UserMessage` item whose `client_id` matches `client_user_message_id`, then `turn/completed`; e.g. `thread_queue.rs:377-390`). "Queued" and "delivered/started" are explicitly different signals — one is the RPC return value, the other is an async event stream the caller must separately be listening on.

**Survives app-server restart / target-session restart: yes, directly demonstrated.** `cold_thread_resume_dispatches_a_persisted_queued_submission` (`thread_queue.rs:418-489`) drops the original `TestAppServer` process object entirely and re-adds/resumes against a brand-new one pointed at the same Codex home, showing the queued item dispatches — because the queue table is keyed only by `thread_id`, a stable persisted identifier, with no reference to any live process/session handle.

**Confidence: High** for persistence mechanism, response/notification separation, capacity cap, and busy/idle dispatch (backed by source + integration tests read in full). **Medium** for "stale"/"exited" as distinct code paths — those are my mapping of `AgentStatus` variants and thread-load/archival state onto the requested vocabulary, not literal code terminology. **Medium-low** on the at-least-once/crash-window claim — inferred from call ordering, not confirmed by a test.

---

## 5. Session registry, discovery and liveness

**Thread resolution for `codex queue`.** UUID first via `ThreadId::from_string`, else exact-name lookup — see §3/§6. The RPC handler itself (`thread_queue_processor.rs:237-277`, `require_thread`) only ever accepts a UUID; name→UUID resolution happens client-side in the TUI/CLI layer (`tui/src/named_session_lookup.rs`, `tui/src/session_archive_commands.rs`), which calls the `thread/list` RPC to page through candidates (`named_session_lookup.rs:221-297`).

**Where the registry lives — three layers, no separate JSON index file:**
1. **In-memory, per-process (authoritative for "is this thread loaded"):** `ThreadManagerState { threads: Arc<RwLock<HashMap<ThreadId, Arc<CodexThread>>>>, ... }` (`core/src/thread_manager.rs:336-337`); lookup via `get_thread` (`thread_manager.rs:1408-1414`), which errors `ThreadNotFound` for anything not currently loaded.
2. **Persisted registry: SQLite, not JSON.** `$CODEX_HOME/state_5.sqlite` (`STATE_DB_FILENAME`, `state/src/sqlite.rs:33`). Schema: a `threads` table (`state/migrations/0001_threads.sql:1-18`: `id, rollout_path, created_at, updated_at, source, model_provider, cwd, title, sandbox_policy, approval_mode, tokens_used, has_user_event, archived, archived_at, git_sha, git_branch, git_origin_url`), with `name TEXT` added later (`state/migrations/0041_threads_name.sql:1`).
3. **Rollout JSONL files** under `$CODEX_HOME/sessions/` and `.../archived_sessions/` (`rollout/src/lib.rs:67-68`) — the durable transcript of record; the SQLite DB is a queryable index over these, with an explicit "scan and repair" fallback (`SessionNameLookupMode::ScanAndRepair`, `named_session_lookup.rs:20-23`) that rebuilds stale SQLite rows from JSONL headers when a lookup misses.
4. **No socket-based live registry** — the only Unix socket is the app-server's single RPC transport socket (§1), not a per-thread registry.

**Registry entry fields.** Persisted metadata, `ThreadMetadata` (`state/src/model/thread_metadata.rs:122-187`) — key fields: `id: ThreadId`, `rollout_path`, `created_at`, `updated_at`, `recency_at`, `source: String`, `thread_source: Option<ThreadSource>`, `cwd: PathBuf`, `title: String`, `name: Option<String>`, `sandbox_policy`, `approval_mode`, `tokens_used`, `archived_at`, `project_id`, `git_sha/branch/origin_url`. **No `pid` field and no `socket path` field** exist anywhere in this struct or the rollout-header `SessionMeta` (`protocol/src/protocol.rs:2884-2924`) — grepped `\bpid\b`/`socket` across both, no relevant hits.

**Liveness — OS advisory locks and socket-connect probes, not PIDs or heartbeats:**
- **Per-thread writer liveness**: each active writer holds an advisory lock file at `$CODEX_HOME/thread-writer-locks/<thread_id>.lock` (`thread-store/src/local/writer_lock.rs:17,39-87`). A previous writer's liveness is tested by trying to `try_lock()` its file (`remove_stale_thread_locks`, lines 118-165): success means the earlier holder died without releasing it, and the file is deleted; `WouldBlock` means a live competitor exists, producing `ThreadStoreError::Conflict{"thread {thread_id} already has an active writer"}` (lines 64-70). Reaping is lazy, on first acquire per process (`cleanup_attempted: AtomicBool`, lines 44-48), not timer-driven.
- **Daemon liveness** (used by the CLI to decide local-daemon vs. embedded): a live `UnixStream::connect(socket_path)` probe (`tui/src/lib.rs:437-459`, `maybe_probe_default_daemon_socket`); inversely, the daemon itself treats a successful connect to its own intended socket path as "already running" (`unix_socket.rs:96-108`).
- **Runtime turn/status liveness** is separate and purely in-memory — see §7.

**`interactive`/`exec`/`custom` — the enum is `SessionSource`** (`protocol/src/protocol.rs:2584-2598`):
```rust
#[serde(rename_all = "lowercase")]
pub enum SessionSource {
    Cli,
    #[default]
    VSCode,
    Exec,
    Mcp,
    Custom(String),
    Internal(InternalSessionSource),
    SubAgent(SubAgentSource),
    #[serde(other)]
    Unknown,
}
```
No doc comments on the variants; meaning is `INFERRED` from construction sites: `Cli` — interactive TUI session (`tui/src/lib.rs:1976`); `Exec` — the one-shot `codex exec` CLI (`exec/src/lib.rs:553`) and the SDK sample host (`thread-manager-sample/src/main.rs:147`); `Custom(String)` — embedded/SDK-hosted surfaces distinct from OSS CLI/exec, e.g. `Custom("atlas")`/`Custom("chatgpt")`, both listed alongside `Cli`/`VSCode` in `INTERACTIVE_SESSION_SOURCES` (`rollout/src/lib.rs:69-76`). A related but distinct pair of enums exists for other purposes: `ThreadSource` (`protocol.rs:2604-2610`: `User, Subagent, GuardianReview, Feature(String), MemoryConsolidation` — analytics classification) and `ThreadSourceKind` (`app-server-protocol/src/protocol/v2/thread.rs:1466-1479`: `Cli, VsCode, Exec, AppServer, SubAgent, SubAgentReview, SubAgentCompact, SubAgentThreadSpawn, SubAgentOther, Unknown` — used to filter `thread/list`).

**Confidence: High.** Full resolution chain (CLI → app-server → in-memory registry → SQLite → rollout files), daemon liveness probe, and writer-lock staleness mechanism read directly. Sampled: the ~30k-line thread-store crate and the ~5900-line `thread_processor.rs` — only the functions reachable from the queue/resolution/liveness call paths were read.

---

## 6. Identity and naming

**Immutable ID vs. mutable name — clearly distinguished.**
```rust
// protocol/src/thread_id.rs:11-18
/// Codex-generated thread IDs are UUIDv7, and some use cases rely on that.
pub struct ThreadId { pub(crate) uuid: Uuid }
```
```rust
// state/src/model/thread_metadata.rs:158-160
/// A best-effort thread title.
pub title: String,
/// Explicit user-facing thread name, if one was set.
pub name: Option<String>,
```
(The wire `Thread` type has the same split — `id: String` at `app-server-protocol/src/protocol/v2/thread_data.rs:201`, `name: Option<String>` at `thread_data.rs:266`; note the doc comment on that field literally says `/// Optional user-facing thread title.` despite the field being the *mutable name*, not the auto title — a naming quirk in the source, reported verbatim.)

**How a name is assigned.** `title` is auto-derived (best-effort, adjacent to `first_user_message`/`preview` fields — `INFERRED`, the generation algorithm itself was not traced). `name` starts as `None` (`ThreadMetadataBuilder::new`, `thread_metadata.rs:236-264`, has no `name` field at construction) and is only ever set via an explicit rename call.

**Renaming exists: `thread/name/set`.**
```rust
// app-server-protocol/src/protocol/common.rs:557-561
ThreadSetName => "thread/name/set"
```
```rust
// protocol/v2/thread.rs:750-753,765
pub struct ThreadSetNameParams { pub thread_id: String, pub name: String }
pub struct ThreadSetNameResponse {}
```
Handler (`app-server/src/request_processors/thread_processor.rs:1720-1752`):
```rust
let ThreadSetNameParams { thread_id, name } = params;
let thread_id = ThreadId::from_string(&thread_id)...
let Some(name) = codex_core::util::normalize_thread_name(&name) else {
    return Err(invalid_request("thread name must not be empty"));
};
...
self.thread_manager.update_thread_metadata(
    thread_id,
    StoreThreadMetadataPatch { name: Some(Some(name.clone())), ..Default::default() },
    /*include_archived*/ false,
).await...
```
`normalize_thread_name` (`core/src/util.rs:102-109`) only trims whitespace and rejects an empty result — **no uniqueness check is performed at rename time.**

**On rename, is the old name retained/aliased? NOT FOUND — discarded.** The patch overwrites the single `name` column in place; there is no alias or name-history table in any of the `state/migrations/*.sql` files inspected (`0001_threads.sql`, `0041_threads_name.sql`). Grepped `alias|previous_name|name_history|old_name` across `state/src` and `thread-store/src` — only unrelated SQL-query-builder aliasing (`thread-store/src/local/thread_history/segment_paging.rs:177-195`) turned up.

**"Rejects ambiguous or duplicate names" — NOT FOUND; false as stated.** No `UNIQUE` constraint or index exists on the `name`/`title` columns (`0001_threads.sql`, `0041_threads_name.sql:1` — a bare nullable `ALTER TABLE ... ADD COLUMN name TEXT`). A repo-wide grep for `ambiguous|DuplicateName|duplicate name|NameConflict|already in use|name is taken|unique name` finds no thread/session-naming error type (the only "already in use" hit is the unrelated control-socket bind error, `unix_socket.rs:103`). What actually happens instead — quoted verbatim, `tui/src/session_archive_commands.rs:256-265`:
```rust
if first_match.as_ref().is_none_or(|existing| {
    if sort_by_recency {
        thread.recency_at.unwrap_or(thread.updated_at) > existing.recency_at.unwrap_or(existing.updated_at)
    } else {
        thread.updated_at > existing.updated_at
    }
}) {
    first_match = Some(thread);
}
```
The enclosing type is literally named `SessionNameMatch::First`/`FirstIncludingNonInteractive` (`session_archive_commands.rs:59-63`) — i.e. "pick the most recent match" is the documented policy, not an error path. Matching itself is exact, case-sensitive string equality on `name` (`named_session_lookup.rs:227`), with candidate eligibility (not uniqueness) scoped by the caller-supplied source-kind filter.

**Confidence: High** for the ID/name distinction and rename path (both read and exercised directly). **Medium-high** for the "no ambiguity rejection" finding — this is a well-evidenced absence (schema + full grep + the explicit "pick first" naming of the resolution policy), but as with any negative result there's a residual chance of an ambiguity check living in a file outside the sampled set.

---

## 7. Presence and status

**The enum is `ThreadStatus`** (`app-server-protocol/src/protocol/v2/thread.rs:1625-1634`):
```rust
pub enum ThreadStatus {
    NotLoaded,
    Idle,
    SystemError,
    #[serde(rename_all = "camelCase")]
    Active { active_flags: Vec<ThreadActiveFlag> },
}
```
```rust
// thread.rs:1636-1642
pub enum ThreadActiveFlag { WaitingOnApproval, WaitingOnUserInput }
```
There is no separate literal "working"/"needs-input"/"stale"/"exited" enum; those map (directly observed in `loaded_thread_status`, `app-server/src/thread_status.rs:438-460`) as: `NotLoaded` ≈ not currently loaded in any process (closest to "exited"/cold); `Idle` = loaded, no active turn; `Active{active_flags:[]}` = a turn is running ("working"); `Active{active_flags:[WaitingOnApproval|WaitingOnUserInput]}` = needs input; `SystemError` = last turn ended in an unrecovered error.

**Who updates it, and how.** Event-driven, not polled. `ThreadWatchManager` (`thread_status.rs:18-21`, in-memory `Arc<Mutex<ThreadWatchState>>`) is mutated directly from the core event loop as `EventMsg` variants arrive, in `app-server/src/bespoke_event_handling.rs::apply_bespoke_event_handling`:
- `note_turn_started`/`note_turn_completed`/`note_turn_interrupted` on `EventMsg::TurnStarted`/`TurnComplete` (`thread_status.rs:147-162`; call sites `bespoke_event_handling.rs:162-164,194-196,1121`).
- `note_permission_requested`/`note_user_input_requested` (`thread_status.rs:193-207`) return a `ThreadWatchActiveGuard` whose `Drop` impl (`thread_status.rs:45-56`) auto-clears the flag when the approval/input request is answered or dropped.
- `note_system_error` and `note_thread_shutdown` (`thread_status.rs:174-182,164-172`) on error/teardown events.
- A race-window correction helper, `resolve_thread_status` (`thread_status.rs:294-308`), prefers `Active` over `Idle`/`NotLoaded` if a turn is known in-progress but the watch state hasn't caught up yet — a comment explains this is to cover events arriving before the watch runtime observes them.

No polling timer exists in `thread_status.rs`; subscribers await changes via a `tokio::sync::watch` channel (`subscribe`, lines 255-260).

**Persistence: in-memory only, per app-server process.** The `threads` SQLite table has no status column — status does not survive a process restart and defaults back to `NotLoaded` on a fresh process (`ThreadWatchState::default()`, `thread_status.rs:310-314`); no explicit reaping is needed.

**Readable by another process.** Yes, two ways:
- `Thread.status: ThreadStatus` is a field on the wire `Thread` type returned by `thread/get`, `thread/list`, `thread/resume`, `thread/start`, etc. (`thread_data.rs:243-244`), populated at each response site via `resolve_thread_status(...)` (multiple call sites in `thread_processor.rs`, e.g. lines 1489-1499, 4956-4962, 5448).
- A live push notification, `ThreadStatusChangedNotification { thread_id, status }` (`thread_data.rs:1832-1838`), broadcast to subscribed RPC clients on change (`thread_status.rs:223-253`).

This is visible cross-process only through the one app-server process that has the thread loaded, over its RPC transport — not independently queryable from disk by an unrelated process; a thread not loaded anywhere simply reads back `NotLoaded`.

**Confidence: High.** The full 873-line `thread_status.rs` state machine and its call sites in `bespoke_event_handling.rs` were read directly, not sampled.

---

## 8. Inbound surfacing

**Storage → injection path.** `ThreadQueueRequestProcessor::add` (`thread_queue_processor.rs:72-90`) persists via `QueuedItemService::enqueue` — the message is **not** in the live turn state at this point (see §4). When the item is started (auto-wake or explicit `thread/queue/start`), `QueuedItemService::start`/`dispatch_if_idle` call `CodexThread::start_turn_if_idle(TurnInputRequest::new(input))` (`ext/queue/src/service.rs:390-397,433-436`), which flows into core's `codex_thread.rs:350-368` → `session/turn_input.rs::start_if_idle`.

**Injected as a plain, unwrapped new user turn — no interjection type, no system-reminder framing.**
```rust
// core/src/session/turn_input.rs:342-367
let mut task_input = merge_additional_context_input(session, additional_context).await;
if has_user_input {
    ...
    task_input.push(pending_turn_input(input));   // no prefix/wrapper applied
}
...
session.start_task(turn_context, task_input, RegularTask::new(), MailboxParentProvenance::Ignore).await;
```
A grep for `"[queued message]"`/`"[Queued"`/`QUEUED_MESSAGE`-style constants across `core/src`, `app-server/src`, `tui/src`, `ext/queue/src` returns **NOT FOUND** — the queued text becomes a normal `TurnInput::UserInput`, indistinguishable in kind from something the user just typed. (For comparison, mid-turn "steering" input — a distinct mechanism from queuing — goes through `session/turn_input.rs::steer_input`, lines 477-564, and is also injected as plain `TurnInput::UserInput`, merged into the *currently active* turn rather than starting a new one.)

**Idle-gated; cannot interrupt an in-progress turn.**
```rust
// core/src/session/turn_input.rs:281-290
let turn_state = {
    let mut active_turn = session.active_turn.lock().await;
    if active_turn.is_some() {
        return Ok(TurnInputSubmission::NotSubmitted { reason: NotSubmittedReason::NotIdle });
    }
    ...
};
```
The app-server surfaces this as an explicit error on `thread/queue/start` while busy (`thread_queue_processor.rs:199-213`, see §4).

**Wakes an idle session automatically — yes.** `enqueue()` calls `self.wake_if_loaded(thread_id).await` (`service.rs:277`) which, unless the thread is `Interrupted`, calls `thread.emit_thread_idle_lifecycle_if_idle(...)` (`service.rs:463-474`); if genuinely idle, this fires the queue extension's `on_thread_idle` → `dispatch_if_idle` → `start_turn_if_idle` — i.e. **no user action is required** to start the queued turn if the thread was idle at enqueue time. A 10-second background poller (`watch_external_messages`, `service.rs:91-244`) is a backstop for crash/restart/missed-wake races (see §4).

**Confidence: High.** The full call chain (enqueue → idle-wake → `start_turn_if_idle` → plain user turn) was traced and every function in the path read directly.

---

## 9. A monitor/watch equivalent

**NOT FOUND** — there is no built-in tool or mechanism that pushes a live stream of external events (process stdout, file changes, MCP notifications) into the *model's own conversation context* as they occur, comparable to Grok Build's rate-limited stdout-to-conversation-event monitor tool. What exists are three UI-facing streaming channels that terminate at the human/client boundary, not the model, plus one unrelated agent-to-agent mechanism:

1. **`unified_exec` background-process streaming — UI-only.** `core/src/unified_exec/async_watcher.rs::start_streaming_output` (lines 59-160) streams a PTY's output as `EventMsg::ExecCommandOutputDelta` events (`session.send_event`, lines 317-325), rate-limited to `MAX_EXEC_OUTPUT_DELTAS_PER_CALL = 10_000` (`core/src/exec.rs:83`) with an 8192-byte cap per delta (`UNIFIED_EXEC_OUTPUT_DELTA_MAX_BYTES`, `async_watcher.rs:41`) — structurally the closest analog to Grok's monitor. But these events are consumed only by the app-server/TUI client for live rendering (`tui/src/app/thread_events.rs:159-160` matches `ServerNotification::CommandExecOutputDelta`) — they are not added to the model's own `ResponseItem` context. The model sees only the aggregated final output once the tool call returns (`emit_exec_end_for_unified_exec`, `async_watcher.rs:335-379`); it can otherwise only poll a background process via explicit one-shot `exec_command`/`write_stdin` tool calls, which is exactly the pattern the question excludes.
2. **`codex-file-watcher` — client-facing, not conversation-facing.** Its only consumers are `app-server/src/fs_watch.rs` (implements `fs/watch`, debounced 200ms, forwards `ServerNotification::FsChanged` to the app-server *client*, e.g. an IDE extension) and `app-server/src/skills_watcher.rs` (reloads skill definitions). Neither injects into a model turn.
3. **MCP notifications/sampling — logged, not surfaced.** `rmcp-client/src/logging_client_handler.rs` implements rmcp's `ClientHandler` callbacks (`on_progress`, `on_logging_message`, `on_resource_updated`, lines 63-140) by calling `tracing::info!/warn!/error!` only — never `session.send_event` or anything reaching model context. MCP `sampling/createMessage` is not implemented at all — grepped `sampling|create_message|CreateMessageRequest` across `rmcp-client/src`, `core/src`, `mcp-server/src` — **NOT FOUND**.
4. **No dedicated "watch"/"monitor" tool exposed to the model.** Grepped `core/src`, `tools/src` for `"watch"`, `"monitor"`, `MonitorTool`, `WatchTool` (case-insensitive, outside tests) — **NOT FOUND**.
5. For completeness, not as an answer to this question: the inter-agent mailbox (`TurnInput::InterAgentCommunication`, `core/src/session/input_queue.rs`) lets a sub-agent's message get injected into a parent/peer agent's next turn — this is agent-to-agent orchestration, unrelated to streaming process/file/event data into a conversation.

**Confidence: High** on the negative result. Search was systematic (grep across the named candidate crates plus repo-wide tracing of the one plausible positive's event names), and the `unified_exec` streaming path was followed far enough to confirm it terminates at the client/UI boundary rather than the model's context.

---

## 10. Environment variables and child-process scoping

**Headline finding, stated plainly: hooks and MCP servers use opposite strategies, and hooks are the more exposed side.** Hook child processes receive a raw snapshot of the *entire* Codex process environment (minus a narrow 5-name denylist that does not include general credential patterns); MCP stdio-server child processes receive an *allowlist* of a handful of vars plus explicitly configured passthroughs. Neither path leaks the app-server's Unix socket path via env (no evidence it's ever set that way in the first place).

### Hooks (`codex-rs/hooks`)

Full-environment snapshot captured once, process-wide, at session start:
```rust
// hooks/src/registry.rs:73-78
let environment = Arc::new(std::env::vars_os().collect());
let hooks = Self::from_config(config, mcp_executor, Arc::clone(&environment), |shell| {
    CommandHookRuntime::new(shell, environment, thread_id, result_sender)
});
```
This is `std::env::vars_os()` — every variable visible to the Codex process itself, including anything the user's shell exported before launching Codex (e.g. a stray `OPENAI_API_KEY`, `GITHUB_TOKEN`, `AWS_SECRET_ACCESS_KEY`).

Per-invocation, this snapshot is replayed onto a fresh `Command` via `env_clear()` + `envs()` (scoped to that one spawn, not a further ambient `set_var`):
```rust
// hooks/src/engine/command_runner.rs:390-426
command.env_clear();
command.envs(environment.iter().cloned());
command.envs(env);                              // per-hook config overrides
scrub_non_inheritable_env_vars(command.as_std_mut());
```
The scrub is a **5-name denylist**, not a pattern-based redaction (`protocol/src/shell_environment.rs:14-20`):
```rust
pub const NON_INHERITABLE_ENV_VARS: &[&str] = &[
    CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN_ENV_VAR,   // "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN"
    "NODE_REPL_AUTH_TOKEN",
    OPENAI_FEDERATION_RULE_ID_ENV_VAR,
    OPENAI_IDENTITY_TOKEN_FILE_ENV_VAR,
    OPENAI_WORKLOAD_IDENTITY_CONTEXT_ENV_VAR,
];
```
The function's own doc comment concedes the scope: *"This prevents accidental propagation of Codex launch context; it is not a filesystem security boundary..."* — `OPENAI_API_KEY` itself (read via `env::var(OPENAI_API_KEY_ENV_VAR)`, `login/src/auth/manager.rs:890-895`) is **not** in this list, and no `*KEY*`/`*SECRET*`/`*TOKEN*` pattern filter is applied on the hooks path.

By contrast, the model's own shell-exec tool calls go through `create_env`/`populate_env` (`core/src/exec_env.rs:30-36` → `protocol/src/shell_environment.rs:54-158`), which *does* apply pattern-based default excludes:
```rust
// protocol/src/shell_environment.rs:123-131
let default_excludes = vec![
    EnvironmentVariablePattern::new_case_insensitive("*KEY*"),
    EnvironmentVariablePattern::new_case_insensitive("*SECRET*"),
    EnvironmentVariablePattern::new_case_insensitive("*TOKEN*"),
];
env_map.retain(|k, _| !matches_any(k, &default_excludes));
```
**The hooks path never calls `create_env`/`populate_env`** — it bypasses this `*KEY*/*SECRET*/*TOKEN*` filtering entirely. This is the concrete instance of the Grok-comparable finding: for hook commands specifically, ambient credential-shaped env vars present in Codex's own process environment are not filtered before being handed to a (potentially less-trusted, user-configured) child.

Hook context (thread id, turn id, cwd, event data) is passed via a JSON blob on the child's **stdin** (`hooks/src/engine/command_runner.rs:267-270`), not via dedicated env vars — no `CODEX_THREAD_ID`/`CODEX_SESSION_ID` env-var call was found in `hooks/src`.

### MCP stdio server child processes (`codex-rs/rmcp-client`)

Allowlist model, also `env_clear()` + explicit `envs()`:
```rust
// rmcp-client/src/stdio_server_launcher.rs:280-293
command.kill_on_drop(true).stdin(Stdio::piped()).stdout(Stdio::piped())
    .current_dir(&cwd).env_clear().envs(&envs).args(&args);
```
`envs` is built by `create_env_for_mcp_server` (`rmcp-client/src/utils.rs:16-59`) from, and only from:
1. `DEFAULT_ENV_VARS` (`utils.rs:162-175`, unix): `HOME, LOGNAME, PATH, SHELL, USER, __CF_USER_TEXT_ENCODING, LANG, LC_ALL, TERM, TMPDIR, TZ` — read individually, not bulk-copied.
2. Var names the MCP server's own config explicitly opts into passing through (`local_stdio_env_var_names`, `utils.rs:90-101`).
3. Custom CA cert env vars if configured.
4. Literal `env = {...}` key/value overrides from the server's config (explicit, not inherited).
5. The same 5-name denylist stripped again at the end (`env.retain(|name, _| !is_non_inheritable_env_var(name))`, `utils.rs:54-57`).

Remote/executor-backed MCP stdio servers are stricter still (`create_env_overlay_for_remote_mcp_server`, `utils.rs:61-79`) — only explicitly-named `env_vars` (source `local`) plus literal config overrides, no `DEFAULT_ENV_VARS` baseline at all. **There is no full-environment copy anywhere on the MCP-server-launch path** — the opposite pattern from hooks.

### Socket paths and process-wide `set_var`

- No evidence the app-server passes its Unix socket path to hook or MCP-server children via an environment variable — searched `app-server-daemon/src`, `uds/src`, `app-server/src` for such a call: **NOT FOUND** (medium confidence — not every daemon-launch path in `app-server-daemon` was audited).
- `CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN` (a real credential, read in `exec-server/src/environment.rs:606-644`) **is** in the 5-name denylist stripped by both hooks and MCP servers.
- Repo-wide grep (excluding tests) for `std::env::set_var`/`env::set_var` on the child-spawn path: only `arg0/src/lib.rs:170` (rewrites the process's own `PATH`, for arg0 dispatch — unrelated) and `linux-sandbox/src/proxy_routing.rs:193` (sets a routing var before exec'ing into a sandboxed helper — unrelated to hooks/MCP). Neither hook nor MCP env construction uses ambient `set_var` mutation of the whole process — scoping is via `.env_clear()` + `.envs()`/`.env()` per-`Command` in both cases. **The scoping mechanism is sound in both paths; it is the content of what's copied that diverges (raw snapshot vs. allowlist).**

**Confidence: High** for the hooks-vs-MCP contrast — both `env_clear()` call sites and their upstream environment-construction functions were read in full, and the raw-snapshot line (`hooks/src/registry.rs:75`) is unambiguous. **Medium** on the "socket path never passed via env" negative, since not every daemon-launch code path was audited.
