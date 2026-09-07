# omp

## Installing omp

Install omp as a prebuilt release binary. Do not install it with npm.

The npm bin is a `bun` script. It loads a native module that `npm install -g`
does not fetch. The script lands on `PATH` and fails on its first run.

The Dockerfile's build-time check runs each binary to confirm it actually
starts. This is how it catches the npm install failure.

## Model selection

`--model` takes a `provider/id` value. It reaches every provider omp is
authed for.

`omp models` prints the model catalog grouped by provider: anthropic,
openai-codex, and xai. It is the only harness documented here that answers
"what can I run" without a terminal.

The default model comes from the role set in `~/.omp/agent/config.yml`. This
default changes when that file changes. Pass `--model` to override it; the
override does not track the file.

## The xai-oauth selector

The default model is `xai-oauth/grok-4.6`. It authenticates using the
`XAI_API_KEY` environment variable.

## Output modes

`--mode` accepts `text`, `json`, `rpc`, or `rpc-ui`.

Text mode buffers all output and writes it only when the run ends. Killing a
text-mode run mid-flight leaves stdout empty, even when the run took action.
Find evidence on disk instead, or use `--mode json`.

`--mode json` prints an NDJSON event stream that carries the tool calls.

Version 18.0.3 has no `--jsonl` flag. It exits with code 2 and prints
`unknown flag: --jsonl`.

## Closing stdin

Close omp's stdin before you start it.

omp probes stdin during startup. An inherited pipe that never sends EOF
blocks omp in `readPipedInput`. This block happens before omp contacts the
model. One run stalled for 4h46m with no output under this condition.

The signal is `phase: readPipedInput` on stderr. A job that produces no
bytes while it appears to think shows the same signal.

`--max-time` bounds the agent's run after startup completes. It does not
stop the stdin block described above.

## MCP child environment

`.mcp.json` gives the MCP child process a fixed environment. Codex uses the
same shape. See `codex.md` for a case where this caused a problem.

The MCP server receives only the variables named in the `env` block.

## Kind detection

`detect_kind()` does not detect an MCP child process that omp launches.

The MCP child inherits one identifying variable, `PI_NO_TITLE=1`. It has no
session id and no agent directory.

An omp peer first appears in the roster as `pending-<pid>`. The `initialize`
handshake later names it, reporting `omp-coding-agent`.

## Terminal-session files

`~/.omp/agent/terminal-sessions/ttys*` files hold a working directory and a
path to a session log. They carry no pid.

omp never deletes these files. They accumulate for months after their
sessions finish.

agent-bus discovers a live omp from its daemon client records. Those records
carry a real pid.

## Using park for CI

`park` blocks omp on its mail. It gives a CI run a deterministic point to
stop and check.

This block-and-wait behavior is a CI technique. A useful agent behaves
non-deterministically, but a CI run needs an agent that reliably stops at a
known point.

Copying this pattern into a real integration produces an agent that stays
blocked and declines other work.

## Using hub for a test

`hub` supervises a project-scoped process and returns its output in one
call. This gives a test a deterministic point to assert on.

    hub op:"start" name:"buswatch" application:"sh"
        args:["-c","exec agent-bus watch --target <me>"]
    hub op:"logs" name:"buswatch" follow:true timeout:300

`logs` with `follow` and no `cursor` blocks until output appears after the
call starts. The broker defaults the cursor to the current end,
`cursor ?? outputBytes`.

This is one call with no bookkeeping. It returns the output lines directly.

The calling turn stays open for the whole wait. This makes the call
assertable in a test. The agent does no other work while that turn is open.

## Real-world use of the park loop

The park loop (start the watcher, block on `hub logs --follow`, ack, reply,
repeat) makes the CI test pass.

On 2026-08-28, a session used this loop as its entire brief, with no coding
task mentioned. A collaborator then offered real work. The agent replied,
"I'm currently parked on the bus; no LabKit-side work needed," and declined
the work. It engaged only after an explicit, unambiguous re-task arrived
three minutes later. The session transcript is a local
`~/.omp/agent/sessions/` artifact.
It is not checked into this repo.

A separate test ran `start` alone, with no follow-up `wait` or `logs` call.
This never woke the agent.

The loop is necessary to wake omp from `start` alone. It has not been shown
to work for a session that also has real work to do.

## Park loop latency measurements

These wait times come from direct measurement.

On 2026-08-28, the session's longest wait for a real message was 3m26s, from
`21:41:10` to `21:44:36`.

On 2026-08-30, a message sent to a live probe without delay got a reply
within 13s of landing.

On the same day as the 2026-08-28 session,
`test_two_agents_hold_a_conversation.py`'s `claude-to-omp` run took about
5m20s end to end. This run is a CI-shaped exchange of seven scripted
messages. It independently matches PR #49's original 5m51s measurement.

`hub logs --follow` returns the instant new output appears. Every wait time
above is the other side's own time to notice, think, and reply. This is
real round-trip latency between two independently-reasoning agents, repeated
over several turns. It is not overhead added by `hub` or agent-bus.

The claude and grok pairs receive pushed messages instead of polling for
them. This lets them finish the same test in under a minute.

## The hub start readiness check

`hub start` adds one real, fixable cost: a spurious readiness check.

In both traces, the model attaches its own
`ready: {log: "...agent-bus", timeout: 30}` clause to the `start` call,
unprompted. `agent-bus watch` prints no output until mail arrives, so this
readiness pattern never matches early. The check always costs ~30s.

In the 2026-08-28 session, the model read the resulting
"NOT ready... still running" message correctly and continued. In the
2026-08-30 probe, the same readiness timeout appeared. The model read it as
fatal. It aborted the session with `FAILED`. The process was healthy at the
time.

This difference is model interpretation variance on an identical tool
result. `hub` behaves correctly in both cases.

`conversation_peer_park.md` now tells omp not to attach a `ready` clause to
the `start` call. This instruction relies on the model's compliance. It is
not a code-enforced guarantee. It has not been re-tested to confirm the
model follows it.

## The wait tool's pattern option

Do not use `wait` with a `pattern` option to wait on more than one message.

`wait` works for a single wake. It fails for anything that loops twice.

`matched` returns `match[0]`, the matched substring, not the full line. A
`pattern: "agent-bus"` search returns the literal string `"agent-bus"`,
never the `summary=` content you are watching for.

`wait` matches against `readinessBuffer`, which accumulates every line. A
second `wait` call re-matches the first line instantly, so a loop built on
it spins without advancing.

## hub's internal messaging and process control

`hub` also carries omp's own messaging between omp instances: `send`,
`inbox`, and `list` over its own IrcBus. This messaging is separate from the
bus.

hub's process operations are what matter for agent-bus.

`start`, `stop`, and `restart` are exec-tier approvals. `wait` and `logs`
are read-tier. The `launch.enabled` setting gates all of these process
operations.

## The bash tool's restrictions

omp's `bash` tool refuses redirection and `cat`.

`printf ... >> file` returns *"Blocked: Use the `write` tool instead of
echo/cat redirection"*. `cat`, `head`, and `tail` return *"Use the `read`
tool"*.

omp works around a `>>` redirection call from a prompt written for another
harness. It writes the file twice in that case.

Use the `write` and `read` tools by name.

## The eval tool

`eval` runs a live Python (IPython) kernel.

Elsewhere, agent-bus runs as a subprocess. Inside `eval`, it runs as a
Python import instead.

    import sys; sys.path.insert(0, "<repo>/src")
    from agent_bus import store
    store.list_agents()

This was measured from inside a headless omp. Among the harnesses documented
here, only omp can run agent-bus this way.
