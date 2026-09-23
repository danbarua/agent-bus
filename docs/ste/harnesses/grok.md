# grok

Grok's leader socket and IPC details are in `grok-build-ipc-reference.md`.
Its watch mechanism details are in `grok-build-monitor-reference.md`.

## MCP server start in an untrusted folder

Grok does not start an MCP server in an untrusted folder. Discovery lists
the server without starting it. An unstarted server looks like a failed
server.

Granting trust is a manual step on a host. In the container, a Dockerfile
layer grants trust. Host trust and container trust are separate grants. The
container holds a checkout at a path that exists only in the sandbox. This
setup grants no trust on the host machine.

## Hook-scoped session variables

Grok's session variables are scoped to its hooks. An MCP child process does
not inherit them. A grok peer running the MCP server has no session id at
launch. It registers on the roster as `pending-<pid>`. The `initialize`
handshake later names the peer.

## Default model selection

An unpinned grok's default model depends on how it authenticates. A
grok.com login and an `XAI_API_KEY` login can select different default
models.

Under a grok.com login, `grok models` offers two models and defaults to
`grok-4.6`. Under `XAI_API_KEY`, it offers seven models and defaults to
`grok-4.20-0309-non-reasoning`. This default model costs more than the one a
host run picks. `-m` / `--model` pins the model. `grok models` prints the
current catalog and its model list.

## Monitor tool and rate limits

Grok's `monitor` tool is the wake mechanism. It runs a command and turns
each line of output into a conversation event. A grok peer starts
`agent-bus watch` under `monitor` once, at session start.

`monitor` limits token-bucket capacity to 10. It refills the bucket at one
token per 2 seconds. 30 seconds of continuous suppression stops the watch.
Each line is capped at 500 characters. Each batch is capped at 3000
characters. Events are debounced into 200ms batches before the limiter sees
them.

The limits matter when a peer's `monitor` command is noisy. A peer running
`agent-bus watch` on its own inbox rarely reaches them.

## Roster method naming

Grok's source code names the roster method `x.ai/sessions/list`. The wire
expects `_x.ai/sessions/list`. The documented name returns `-32601`. Read
the source to find a method name. Probe the running binary to confirm what
it sends.

## Grok -p with an armed monitor

A monitor armed on `agent-bus watch` keeps a headless `grok -p` process
alive past its own turn. `-p` normally ends the turn when the model stops
emitting, matching `claude -p`. A monitored `grok -p` was measured still
running at 45 seconds. It acted on the incoming event once the event
arrived.

## Registration timing at grok start

Grok takes its prompt as an argv argument. No gap exists between process
start and prompt delivery in which to register the peer first. Registration
happens at the same time as grok's boot, as a race.

Registration takes about a second. Grok needs more time to boot and reach a
model, so the race is wide. If registration loses, `watch` exits with code
1 and the message "cannot resolve inbox". No watch process is left running.
