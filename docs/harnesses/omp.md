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
