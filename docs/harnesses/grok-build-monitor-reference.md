<!-- Provenance: external read-only source review of danbarua/grok-build,
     branch claude/grok-socket-messaging-docs-7mpde7. Filed here unmodified.
     Companion to grok-build-ipc-reference.md. Claims carry file:line
     citations against that checkout and will drift as it moves.
     See grok.md for what this means for agent-bus. -->

# Grok Build: MCP Notifications & "Monitor" Tool — Source Reference

Scope: `crates/codegen/xai-grok-mcp/src/`,
`crates/codegen/xai-grok-tools/src/implementations/grok_build/monitor/`,
`crates/codegen/xai-grok-tools/src/computer/`, `crates/codegen/xai-grok-tools/src/notification/`,
`crates/codegen/xai-grok-tools/src/reminders/`, `crates/codegen/xai-grok-shell/src/tools/`,
`crates/codegen/xai-grok-shell/src/session/acp_session_impl/`,
`crates/codegen/xai-grok-shell/src/terminal/background_task.rs`,
`crates/codegen/xai-grok-pager/src/{app,views}/`, plus `rmcp` 2.1.0 (the
external MCP client crate Grok Build depends on, verified against its
published source) where cited. Repo: `danbarua/grok-build`,
branch `claude/grok-socket-messaging-docs-7mpde7`.

Methodology note: same as the companion IPC reference doc — every claim below
traces to a `file:line` citation gathered by reading the actual source in
this checkout. Anything not directly stated in a comment/doc but concluded
from code behavior is marked `INFERRED`. Anything searched for and not found
is stated as **NOT FOUND** rather than described speculatively.

---

## MCP Server-Initiated Notifications

Question: does Grok Build handle streaming/asynchronous notifications sent
*by* an MCP server (as opposed to responses to requests Grok itself made)?

**Short answer: partially.** Grok depends on the official `rmcp` crate
(`crates/codegen/xai-grok-mcp/Cargo.toml:9-15`, pinned to `2.1.0` in
`Cargo.lock`), whose `ClientHandler` trait defines one dedicated method per
MCP server-initiated notification type
(`rmcp-2.1.0/src/handler/client.rs:42-81`, the sole demux point from raw
JSON-RPC to handler code). Grok's one and only `ClientHandler` impl,
`GrokClientHandler`
(`crates/codegen/xai-grok-mcp/src/servers.rs:4851-4923`), overrides just two
of them:

| Notification | Status | Evidence |
|---|---|---|
| `notifications/tools/list_changed` | **Handled** | `on_tool_list_changed` overridden, `servers.rs:4859-4863` — emits `McpClientEvent::ToolsChanged` |
| `notifications/resources/list_changed` | **Handled** | `on_resource_list_changed` overridden, `servers.rs:4865-4868` — emits `McpClientEvent::ResourcesChanged` |
| `notifications/resources/updated` | **NOT FOUND** | no override; falls to rmcp's default no-op (`client.rs:219-225`) |
| `notifications/prompts/list_changed` | **NOT FOUND** | no override; default no-op (`client.rs:238-243`) |
| `notifications/progress` | **NOT FOUND** | no override; default no-op (`client.rs:205-211`); no code anywhere sends an outgoing `progressToken` |
| `notifications/message` (server logging) | **NOT FOUND** | no override; default no-op (`client.rs:212-218`); never reaches Grok's own `tracing` or any file/UI |
| `notifications/cancelled` | **Partial** | no `on_cancelled` override, but rmcp's per-request `CancellationToken` is separately consumed inside `create_elicitation` (`servers.rs:4886-4896`) — covers only a server cancelling its own outstanding `elicitation/create`, not general tool-call cancellation |
| any unrecognized notification | **NOT FOUND** | `on_custom_notification` (rmcp's catch-all) is not overridden; its default is a bare `std::future::ready(())` with no trace at all (`client.rs:259-266`) — swallowed silently, not even debug-logged |

Two specifics worth calling out:

- **`tools/list_changed` doesn't trigger a re-fetch.** The event only flips
  an ACP `x.ai/mcp/server_status` UI badge to "Ready/ConfigChanged"
  (`session/mcp_dispatcher.rs:410-424,512-513`, coalesced over a 50ms
  window, `mcp_dispatcher.rs:11-15,279-300,820`). No call to `list_tools()`
  happens on this path — the cached tool definitions stay stale until an
  explicit `x.ai/internal/reload_all_mcp_servers`/
  `reload_project_mcp_servers` (`extensions/session_admin.rs:65-67,651,707`)
  or a model-switch rebuild (`session/acp_session_impl/model_switch.rs:279`
  → `mcp_snapshot.rs:372-432`).
- **No progress streaming during long tool calls.** Zero grep hits for
  `progressToken`/`progress_token` anywhere in the outgoing `tools/call`
  path — Grok just blocks for the final response. For in-process SDK MCP
  servers bridged over the ACP reverse channel this is an explicit
  documented v1 limitation: `xai-grok-mcp/src/acp_transport.rs:10-16` states
  the bridge is "half-duplex" and server→client traffic — notifications
  included — "is NOT bridged."

**Boundary of verification**: whether Grok overrides a handler, and the
tool-refresh/coalescing logic, are checked directly against Grok's own
source. The "default no-op" claims for unoverridden methods are checked
against `rmcp` 2.1.0's actual published source (matching the version pinned
in `Cargo.lock`), not assumed. Since `handle_notification` is rmcp's sole
demux point into handler code and every unlisted branch resolves to a
documented no-op, this is a low-risk gap rather than an open question about
Grok's own behavior.

**Confidence: high.**

---

## What "monitor" is

`monitor` is a first-class tool the model can call — architecturally
parallel to, but independent of, the backgrounded-bash mechanism — for
watching a long-running shell command's stdout as a stream of discrete
events fed back into the conversation, with built-in rate limiting and an
optional unbounded ("persistent") mode.

`TaskKind::Monitor` (the tag distinguishing a monitor's background process
from an ordinary backgrounded bash command) is defined at
`crates/codegen/xai-grok-tools/src/computer/types.rs:146-151`:
```rust
pub enum TaskKind {
    ...
    /// Monitor tool — streams stdout events with rate limiting.
    Monitor,
    ...
}
```

## 1. Tool definition and schema

Defined in `crates/codegen/xai-grok-tools/src/implementations/grok_build/monitor/`:

- `MonitorInput` (`monitor/types.rs:36-69`):
  ```rust
  pub struct MonitorInput {
      pub command: String,          // shell command/script; each stdout line is an event; exit ends the watch
      pub description: String,      // short human-readable description shown in every notification
      pub timeout_ms: Option<u64>,  // default 36_000_000 (10h), max 36_000_000
      pub persistent: bool,         // run for the session's lifetime (no timeout); stop with the kill tool
  }
  ```
- `MonitorOutput` (`monitor/types.rs:71-82`): returns `task_id`, `timeout_ms`
  (reported as `0` when persistent), `persistent: bool`.
- Tool registration (`xai_tool_runtime::Tool` impl, `monitor/tool.rs:46-70`):
  id `"monitor"` (`tool.rs:51`), namespace `ToolNamespace::GrokBuild`,
  `capabilities()` marks it `is_read_only: false`, `tool_scope: Write`.
- Model-facing description template (`tool.rs:29-35`): explains "Each stdout
  line is an event"; instructs the model to print only
  `DONE`/`FAILED`/`CANCELLED` and to use `grep --line-buffered`; documents
  `persistent: true` as "session-length watches ... runs until you call
  [kill tool] or until the session ends. Otherwise it stops at `timeout_ms`
  (default 10h)."
- Declared emitted notifications (`tool.rs:37-39`): `["BashExecutionBackgrounded",
  "MonitorEvent", "TaskCompleted"]`.
- Registered/exported: `implementations/grok_build/mod.rs:27`
  (`pub mod monitor;`) and `mod.rs:56`
  (`pub use monitor::tool::MonitorTool;`).

**Parameters, confirmed**: `command`, `description`, `timeout_ms` (default/max
10h), `persistent` (bool). There is **no PID or regex/pattern parameter** —
filtering is entirely the model's responsibility via the shell `command`
itself (e.g. piping through `grep --line-buffered`); rate limiting and
polling cadence are handled internally, not exposed as tool inputs.

## 2. Execution / dispatch

Self-contained — monitor does **not** attach to a task started by a separate
`bash` tool call. `MonitorTool::run` (`monitor/tool.rs:73-226`):

- Reads shared resources (`Terminal`, notification handle, cwd, session
  folder, owner session) — `tool.rs:88-107`.
- Calls `terminal.run_background(TerminalRunRequest { command: input.command,
  ..., kind: TaskKind::Monitor, ... })` (`tool.rs:113-137`) — the exact same
  `TerminalBackend::run_background` trait method
  (`computer/types.rs:333-336`) that ordinary backgrounded bash commands
  use, just tagged `kind: TaskKind::Monitor` instead of the default
  `TaskKind::Bash` (`computer/types.rs:146-149`).
- Output file: `session_folder/terminal/monitor-{call_id}.log`
  (`tool.rs:110-112`) — same directory bash logs use.
- Sends a `BashExecutionBackgrounded` notification (`tool.rs:145-162`) — the
  same notification bash background tasks send — carrying
  `monitor_description` so the pager tags the row "Monitor" instead of
  syntax-highlighting a raw command string.
- Spawns a Tokio task running `run_monitor_pipeline` (`tool.rs:184-196`, body
  at `tool.rs:241-331`), which polls the output file, feeds new bytes through
  a `LineProcessor` → rate limiter → XML wrap →
  `notification_handle.send_monitor_event(...)`.

The local terminal backend
(`crates/codegen/xai-grok-tools/src/computer/local/terminal.rs`) treats
`TaskKind::Monitor` processes almost identically to bash processes for
spawn/kill/get_task; the one monitor-specific branch is
`reparent_notifications` (`terminal.rs:2080-2245`, see §6).

**Conclusion**: monitor is a standalone tool that starts and owns its own
background process; it is not a "watch an existing bash task" operator.

## 3. Rate limiting

`crates/codegen/xai-grok-tools/src/implementations/grok_build/monitor/rate_limiter.rs`:

- `TokenBucket` (`rate_limiter.rs:9-42`): classic token bucket — `capacity`
  tokens, refills 1 token per `refill_interval_ms`; `try_consume()` returns
  whether a token was available.
- Constants (`monitor/types.rs:13-20`):
  - `RATE_LIMIT_CAPACITY: u32 = 10`
  - `RATE_LIMIT_REFILL_MS: u64 = 2_000` (1 token / 2s)
  - `AUTO_KILL_THRESHOLD_MS: u64 = 30_000` — auto-kill after 30s of
    continuous suppression
- `SuppressionTracker` (`rate_limiter.rs:49-143`): tracks `suppressed_count`
  and suppression timestamps; `process()` (`:80-142`) returns
  `RateLimitOutcome::{Allowed{catch_up_notice}, Suppressed, AutoKill{message}}`.
  On recovery after suppression it emits a catch-up notice: `"[{n} events
  suppressed -- output rate too high. Consider using {kill_tool} to restart
  this monitor with a more selective filter.]"` (`rate_limiter.rs:92-97`). If
  suppression persists past 30s it sets `killed = true` and returns
  `AutoKill` with `"[Monitor stopped -- your script produced too much output
  ... Write a new monitor command that filters more aggressively ...]"`
  (`rate_limiter.rs:124-137`).
- `MonitorRateLimiter` (`rate_limiter.rs:146-173`) combines the bucket +
  tracker; `process_event()` / `is_killed()`.
- Applied per event inside `run_monitor_pipeline`'s `process_event` helper
  (`monitor/tool.rs:363-408`); the pipeline loop checks
  `rate_limiter.lock().await.is_killed()` each tick and breaks out if true
  (`tool.rs:323-325`).
- Additional volume controls (`monitor/event.rs`, constants at
  `monitor/types.rs:1-11`): `LINE_TRUNCATION_LIMIT = 500` chars/line,
  `BATCH_TRUNCATION_LIMIT = 3_000` chars/batch, `BUFFER_CAP_BYTES = 1 MiB`
  raw buffer cap, `DEBOUNCE_MS = 200` polling/batching interval (used at
  `tool.rs:329`).

## 4. Persistent vs. timed behavior

Full verbatim comment block,
`crates/codegen/xai-grok-pager/src/app/cli.rs:702-715`:
```rust
    /// Exit as soon as the first agent turn ends, without waiting for pending
    /// background bash/monitor tasks or background subagents (headless only).
    /// Default for all `grok -p` runs is to wait (up to `--background-wait-timeout`)
    /// so eval harnesses see full task completion. Use this for fast scripts that
    /// only need the first turn's text. Does not wait for server-side auto-wake
    /// output or persistent monitors (those hit the timeout).
    #[arg(long = "no-wait-for-background", hide = true)]
    pub no_wait_for_background: bool,
    /// Max seconds to wait for background work after the first turn ends
    /// (headless only). Applies to bash/monitor `task_completed`, background
    /// subagents (`SubagentFinished`), and any still-running non-persistent
    /// work. Persistent `monitor(persistent:true)` never completes and always
    /// waits the full timeout — use `--no-wait-for-background` or a lower
    /// timeout for throughput. Conflicts with `--no-wait-for-background`.
```
followed by `--background-wait-timeout SECS` (default `600`,
`cli.rs:716-724`).

Operational meaning:
- `persistent: true` ⇒ `resolved_timeout_ms()` returns `0`
  (`monitor/types.rs:104-110`), which `MonitorTool::run` translates to
  `Duration::from_secs(86400*365)` — effectively unbounded — as the
  `TerminalRunRequest.timeout` (`tool.rs:121-123`). The tool's returned
  message tells the model: `"Monitor started (task {id}, persistent -- runs
  until {kill_tool} or session end)."` (`tool.rs:200-206`). Persistent mode
  never self-terminates on a timer; it runs until the model calls the kill
  tool, or the session ends (backend drop reaps the process — see the
  leak-prevention test in §6).
- Non-persistent monitors default to `DEFAULT_TIMEOUT_MS = 36_000_000` (10h),
  capped at `MAX_TIMEOUT_MS = 36_000_000` (`monitor/types.rs:22-27`);
  `MonitorInput::validate()` rejects an explicit `timeout_ms` above the max
  unless `persistent` is set (`types.rs:92-101`).
- Headless-only relevance: `no_wait_for_background` /
  `background_wait_timeout_secs` govern whether `grok -p` (headless/eval
  mode) waits for outstanding background bash/monitor tasks before exiting;
  both are `(headless only)` because the interactive TUI simply keeps
  running regardless. A persistent monitor "never completes" from the
  headless waiter's perspective, so it always consumes the full
  `--background-wait-timeout` window rather than resolving early.

## 5. Event delivery back into the conversation

Path: `run_monitor_pipeline` → `ToolNotificationHandle::send_monitor_event`
→ shell notification bridge → session command channel → injected as a
synthetic turn.

- `MonitorEvent` notification type
  (`crates/codegen/xai-grok-tools/src/notification/types.rs:397-413`):
  fields `task_id`, `description`, `event_text` (XML-wrapped, for the LLM),
  `raw_text` (unwrapped, for pager display), `owner_session_id`. One variant
  of the crate-wide `ToolNotification` enum (`types.rs:436-496`,
  `MonitorEvent` at `:495`).
- XML wrapping: `wrap_monitor_event()` (`monitor/event.rs:83-90`) produces
  `<monitor-event description="..." task_id="...">\n{event_text}\n</monitor-event>`.
- Sending: `ToolNotificationHandle::send_monitor_event`
  (`notification/handle.rs:373`, macro-generated convenience method) fans
  out to configured targets.
- Bridge consumption —
  `crates/codegen/xai-grok-shell/src/tools/notification_bridge.rs`, arm
  `ToolNotification::MonitorEvent(event) => { ... }` at
  **`notification_bridge.rs:737-797`**:
  - Drops the event if `event.owner_session_id` doesn't match this bridge's
    own session (cross-session guard, `:739-750`).
  - Forwards it to the ACP gateway as an `x.ai/monitor_event` ext
    notification for the TUI (`:756-775`).
  - If the task already auto-woke via `TaskCompleted` (tracked in
    `task_completion_reservations`), skips injecting into the model
    (`:776-782`).
  - Otherwise sends `SessionCommand::InjectNotification` with
    `priority: NotificationPriority::Next` and
    `source: NotificationSource::MonitorEvent{task_id}` (`:783-796`), queued
    by the session actor.
- Injection into the conversation:
  `crates/codegen/xai-grok-shell/src/session/acp_session_impl/notification_drain.rs`.
  Pending notifications accumulate (`PendingNotification`, `:16-25`) via
  `push_pending_notification`, drained either:
  - Idle-gated batch drain: `maybe_drain_notifications`
    (`notification_drain.rs:437-530`) merges everything into one new queued
    turn (`InputItem` with `PromptOrigin::NotificationDrain`) via
    `drain_notifications_into_turn` (`:692-740`).
  - A dedicated `MonitorEventBuffer` (`monitor/types.rs:142-158`, a shared
    `xai_interjection_core::EventQueue<MonitorEventNotification>`) is swept
    into `pending_notifications` at multiple checkpoints — turn end
    (`drain_monitor_buffer_to_pending`, `notification_drain.rs:747-750`),
    cancel (`tasks_cancel.rs:413,591`), and idle drain
    (`sweep_monitor_buffer_into_pending`, `notification_drain.rs:567-593`).
  - Multi-event batch formatting via `format_monitor_events()`
    (`crates/codegen/xai-grok-tools/src/reminders/task_completion.rs:270-...`)
    — either a lean single `<monitor-event task_id="...">\n[label]
    text\n</monitor-event>` or a grouped/batched summary across monitors.
- Task completion (monitor process exit): when the underlying process
  exits, `TerminalBackend` emits `ToolNotification::TaskCompleted(TaskSnapshot)`;
  the bridge arm at `notification_bridge.rs:379-613` checks
  `task_snapshot.kind == TaskKind::Monitor` (`:380-381`) and, if auto-wake
  is enabled, formats via `format_monitor_completion()`
  (`reminders/task_completion.rs:178-204`) and requests an immediate
  synthetic prompt (`SessionCommand::Prompt{..., admission:
  Some(TaskWakeAdmission{...})}`, `:436-476`) instead of the deferred
  `InjectNotification` path. The pipeline itself deliberately does **not**
  emit a terminal `[monitor ended]` `MonitorEvent`
  (`monitor/tool.rs:312-319`) — `TaskCompleted` owns that signal, avoiding a
  duplicate wake.
- The reminder is wrapped via `xai_grok_tools::reminders::wrap_reminder` and
  pushed as a system-reminder-tagged user message via
  `push_system_reminder`/`push_system_reminder_with_tag`
  (`reminders.rs:552-565`).

## 6. Relationship to the "bash" background-task tool

Monitor is self-contained, not an operator on an existing bash-spawned task
(§2). Shared infrastructure and cancellation:

- Both bash-background and monitor tasks are tracked in the same
  `TerminalBackend`/process table; `TaskSnapshot.kind: TaskKind`
  (`computer/types.rs:219-221`) distinguishes them, and predicates like
  `is_outstanding()`, `is_outstanding_background()`
  (`computer/types.rs:260-277`) treat both kinds uniformly for
  TodoGate/backing-work counting (comment at `:260-263`).
- Killing a monitor goes through the same `kill_task`/
  `kill_command_or_subagent` tool
  (`implementations/grok_build/kill_task/mod.rs`), which calls
  `terminal.kill_task_with_source(&task_id, KillSource::ModelTool)`
  (`kill_task/mod.rs:205-208`) — no monitor-specific kill tool exists. The
  tool's description dynamically names the monitor tool when present
  (`kill_task/mod.rs:146-154`, `build_kill_task_description`).
- Reparenting on subagent death:
  `computer/local/terminal.rs::reparent_notifications`
  (`terminal.rs:2080-2245`) reparents both bash and monitor tasks'
  notification handles/owner session on subagent teardown; for monitors
  specifically it recovers the human description from the `"[monitor]
  {description}"` display-command prefix (`terminal.rs:2100-2124`) and
  **re-spawns** `run_monitor_pipeline` on the parent session's handle with a
  weak backend reference and a `start_offset` = current file size to avoid
  duplicate events (`terminal.rs:2148-2174`).
- `session/acp_session_impl/tasks_cancel.rs` sweeps the `MonitorEventBuffer`
  into pending notifications on cancel (`:413`, `:591-594`).
- `session/acp_session_impl/stop_gate.rs` (`:19-29,438-444`): builds a
  `BackgroundTaskType::Monitor` stop-entry from `TaskKind::Monitor`,
  carrying the watch command in the description field rather than a shell
  command (comment at `:19`: "`command` is a shell-only field, so a
  monitor's watch command is carried in [description]").
- The `Weak<dyn TerminalBackend>` design in `run_monitor_pipeline` (comment
  `monitor/tool.rs:235-240`) and its regression test
  `persistent_monitor_released_when_session_drops_backend`
  (`monitor/tool.rs:428-504`) show a persistent monitor's pipeline must not
  keep the terminal backend (and hence the monitored process) alive past
  session end — the monitor process is reaped the same way orphaned bash
  background tasks are.

## 7. Pager/TUI surfacing

- `crates/codegen/xai-grok-pager/src/views/tasks_pane.rs`: monitors render
  with a distinct blue **"Monitor"** tag (`tasks_pane.rs:281-301`) instead of
  bash-syntax-highlighting the raw command:
  ```rust
  let (label, styled) = if task.is_monitor {
      ...
      const TAG: &str = "Monitor";
      ...
      let label = format!("{TAG} {text}");
      let styled = Line::from(vec![
          Span::styled(format!("{TAG} "), Style::default().fg(theme.accent_system)),
          Span::styled(text, desc_style),
      ]);
      (label, styled)
  } ...
  ```
  Monitors are grouped into their own contiguous block under a shared
  **"Watchers"** header together with `/loop` scheduled tasks, monitors
  sorted first (`tasks_pane.rs:182-183,684-696,970-983`), tested at
  `tasks_pane.rs:2723-2833`
  (`sync_groups_monitors_as_their_own_block`,
  `monitors_and_loops_share_one_watchers_section`).
- `crates/codegen/xai-grok-pager/src/views/turn_status.rs`: a `Watchers`
  struct (`:100-127`) counts `monitors: usize` separately from `commands`;
  the idle "still running" status cue composes them via
  `format_still_running` / `still_running_label` (`:129-169`), e.g. `"1
  command · 2 monitors · 1 loop · 1 subagent still running"`, with an
  animated pulse icon (`monitor_icon_frames()`, referenced at
  `turn_status.rs:323`, `MONITOR_PULSE_DIVISOR`) cycling across ticks (tests
  at `:1638-1648`).
- `crates/codegen/xai-grok-pager/src/app/dispatch/queue.rs`:
  `watchers.monitors += 1` when `task.is_monitor` (`:283-284`) — feeds the
  same `Watchers` aggregate used by the status line.
- `agent_view/queue.rs` uses a `TaskKind::Monitor`-derived `is_monitor: bool`
  field to drive the same sort/grouping logic client-side (mirrors
  `tasks_pane.rs`, `agent_view/queue.rs:224-356` region).
- The pager learns about a new monitor via the `x.ai/task_backgrounded` ext
  notification (from `BashExecutionBackgrounded`, forwarded in
  `notification_bridge.rs:315-352`) carrying `monitor_description`; about
  live stdout via `x.ai/monitor_event` (`notification_bridge.rs:756-775`);
  and about completion via the `task_completed_frame` ext notification
  (`notification_bridge.rs:582-613`).

## Not found / explicitly out of scope

- No PID-targeting or regex-`pattern` parameter on the `monitor` tool —
  filtering is entirely the model's responsibility via the shell `command`
  (e.g. piping through `grep --line-buffered`); the rate limiter's token
  bucket + debounce is the only built-in throttling mechanism.

## Key files

- `crates/codegen/xai-grok-tools/src/implementations/grok_build/monitor/{tool.rs,types.rs,rate_limiter.rs,event.rs}`
- `crates/codegen/xai-grok-tools/src/computer/types.rs` (`TaskKind`, `TerminalRunRequest`, `TerminalBackend` trait)
- `crates/codegen/xai-grok-tools/src/computer/local/terminal.rs` (`reparent_notifications`)
- `crates/codegen/xai-grok-tools/src/notification/{types.rs,handle.rs}`
- `crates/codegen/xai-grok-tools/src/reminders/task_completion.rs` (`format_monitor_completion`, `format_monitor_events`)
- `crates/codegen/xai-grok-tools/src/implementations/grok_build/kill_task/mod.rs`
- `crates/codegen/xai-grok-shell/src/tools/notification_bridge.rs`
- `crates/codegen/xai-grok-shell/src/session/acp_session_impl/{notification_drain.rs,tasks_cancel.rs,stop_gate.rs,reminders.rs}`
- `crates/codegen/xai-grok-shell/src/terminal/background_task.rs` (`" [monitor]"` label at line 330)
- `crates/codegen/xai-grok-pager/src/app/cli.rs` (lines 702–724, headless wait-for-background flags)
- `crates/codegen/xai-grok-pager/src/views/{tasks_pane.rs,turn_status.rs}`, `src/app/dispatch/queue.rs`, `src/app/agent_view/queue.rs`

## Confidence

**High.** Every behavioral claim traces to a specific struct, constant, or
function with a cited line number, cross-checked against named regression
tests (e.g. `persistent_monitor_released_when_session_drops_backend`,
`sync_groups_monitors_as_their_own_block`). The one explicit negative
finding (no pattern/PID parameter) is a straightforward reading of the
`MonitorInput` struct, not an inference.
