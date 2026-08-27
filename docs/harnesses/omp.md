# omp

What to know when omp is the harness that is misbehaving.

**Install it as a prebuilt release binary, not from npm.** Its npm bin is a
`bun` script that loads a native module `npm install -g` never fetches, so it
lands on `PATH` and dies on first run. The Dockerfile's build-time check runs
every binary rather than just locating it, for exactly this.

**`--model` takes a `provider/id`, and it reaches every provider it is authed
for.** `omp models` prints the catalog grouped by provider — anthropic,
openai-codex and xai side by side — which makes it the one harness here that
can answer "what can I actually run" without a terminal. Its own default comes
from the role in `~/.omp/agent/config.yml`, so it moves when that file does;
pass `--model` and it does not.

**An `xai-oauth/` selector does not mean a browser login.** That default is
`xai-oauth/grok-4.6`, which reads as though it needs one. Measured: it picks up
`XAI_API_KEY` from the environment and answers.

**Output modes are `--mode text|json|rpc|rpc-ui`, and `text` emits nothing
until the run ends.** Kill a text-mode run mid-flight and you get an empty
stdout no matter what it did — evidence has to be on disk or in `--mode json`,
which is an NDJSON event stream carrying the tool calls. There is no `--jsonl`
in 18.0.3: it exits 2 with `unknown flag: --jsonl`.

**Close its stdin.** omp probes stdin during startup, and an inherited pipe
that never sends EOF wedges it in `readPipedInput` **before the model is ever
contacted**. Observed once at 4h46m with zero output. The tell is
`phase: readPipedInput` on stderr, or a job producing no bytes while appearing
to think. `--max-time` does not rescue it: that bounds the agent run, not
startup.

**Its `.mcp.json` gives the MCP child a fixed environment**, the same shape as
codex — see `codex.md`, which is where that cost something. Anything not named
in the `env` block does not reach our server.

**It is not detected by `detect_kind()`.** An MCP server launched by omp
inherits exactly one identifying variable, `PI_NO_TITLE=1`; there is no session
id and no agent dir. So an omp peer registers as `pending-<pid>` and is named
by the `initialize` handshake, which reports `omp-coding-agent`.

**Its terminal-session files are not agents.** `~/.omp/agent/terminal-sessions/ttys*`
hold a working directory and a path to a session log — no pid. agent-bus used
to take one from the filename, so `ttys001` became pid 1, which is launchd: a
roster row that survived exiting omp and would have survived a reboot. omp
never deletes these files, so they accumulate for months of finished sessions.
A live omp is discovered from its daemon client records, which carry a real
pid.

**It parks on mail rather than being pushed it.** omp has no monitor tool, so
nothing arrives unbidden — ask it whether it does and it says no, correctly.
What it has is `hub`, which supervises project-scoped processes and can block
on their output:

    hub op:"start" name:"buswatch" application:"sh"
        args:["-c","exec agent-bus watch --name <me>"]
    hub op:"logs" name:"buswatch" follow:true timeout:300

`logs` with `follow` and **no cursor** blocks until output appears after the
call starts — the broker defaults the cursor to the current end
(`cursor ?? outputBytes`). One call, no bookkeeping, and it returns the lines
themselves. The turn stays open throughout, which is the difference from
Claude's and grok's push: those end the turn and get a new one.

**Do not reach for `wait` with a `pattern` to do this.** It looks like the right
tool and is a trap for anything that loops twice:

- `matched` is `match[0]` — the matched **substring**, not the line. Ask for
  `pattern: "agent-bus"` and you get back the string `"agent-bus"`, never the
  `summary=` you were watching for.
- it matches against `readinessBuffer`, which **accumulates**. The second wait
  re-matches the first line instantly, so a loop spins hot forever.

It is fine for a single wake, which is how it passed a first probe and got
written down here as the recommendation. It was wrong.

`hub` is also omp's own peer messaging (`send`/`inbox`/`list` over its IrcBus)
and its background-job control. That messaging is omp-to-omp and is not our
bus; the process ops are the part that matters here. `start`/`stop`/`restart`
are exec-tier approvals, `wait`/`logs` are read-tier, and the whole family is
gated behind the `launch.enabled` setting.

**Its `bash` tool refuses redirection and `cat`.** `printf ... >> file` comes
back as *"Blocked: Use the `write` tool instead of echo/cat redirection"*, and
`cat`/`head`/`tail` as *"Use the `read` tool"*. A prompt written for another
harness that records evidence with `>>` does not fail — omp works around it and
writes the file twice. Ask for `write` and `read` by name.

**Its other trick is stranger.** `eval` is a live Python (IPython) kernel, so
agent-bus is an **import** rather than a subprocess. Measured from inside a
headless omp:

    import sys; sys.path.insert(0, "<repo>/src")
    from agent_bus import store
    store.list_agents()

No other harness here can do that.
