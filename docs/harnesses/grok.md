# grok

What to know when grok is the harness that is misbehaving. Its leader socket
and IPC are in `grok-build-ipc-reference.md`; its watch mechanism is in
`grok-build-monitor-reference.md`.

**It will not start an MCP server in an untrusted folder.** Discovery is not
start: an untrusted directory still *lists* the server and then never launches
it, which looks like the server failing rather than never being asked. Granting
trust is a manual step on a host; in the container it is a Dockerfile layer.

Those are not the same act, which is why one is automated and the other is not:
a disposable sandbox holding a checkout at a path that exists nowhere else
grants nothing on your machine.

**Its session variables are hook-scoped.** They are not in the environment an
MCP child inherits, so a grok peer running our MCP server has no session id and
registers as `pending-<pid>` until the `initialize` handshake names it.

**Its default model depends on how it is authed**, so an unpinned grok is not
the same agent locally as in the container. `grok models` under a grok.com
login offers two and defaults to `grok-4.6`; under `XAI_API_KEY` it offers
seven and defaults to `grok-4.20-0309-non-reasoning` — a different, and dearer,
model than the one a host run picks. `-m` / `--model` pins it, and `grok models`
prints which catalog you are looking at along with the list.

**Its `monitor` tool is the wake mechanism.** It runs a command and turns each
stdout line into a conversation event, so `agent-bus watch` is what a grok peer
starts once at session start. The limits are the monitor's, not ours: a token
bucket of 10 refilling one per 2s, 30s of continuous suppression kills the
watch outright, 500 chars per line. Hence one compact line per message, and
**start from now** — replaying a backlog is the fastest way to be killed in the
first second.

**Source names are not wire names.** Grok's source calls the roster method
`x.ai/sessions/list` while the wire wants `_x.ai/sessions/list`, and the
documented name answers `-32601`. Read the source to know where to look, then
probe the running binary to know what it sends.
