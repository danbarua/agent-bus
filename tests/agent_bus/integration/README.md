# Integration tests

Most of these spawn a **real coding agent** or a **live Claude Code session**.
Those are marked `spendy`: they cost money and minutes, and an ordinary
`pytest` run skips them.

```sh
./spendy_tests.sh                 # all of them
./spendy_tests.sh roster          # only tests/**/test_*roster*.py
./spendy_tests.sh -k listener     # anything starting with - goes to pytest
```

The argument matches filenames. Drop a new `test_*.py` in this directory and it
is runnable by name immediately — there is nothing to register it in.

Locally they use whatever auth your installed harnesses already have,
subscriptions included. In the container they use the keys in `.env`:

```sh
docker compose run --rm e2e       # everything, spendy included
docker compose run --rm test      # the cheap tests only, no keys needed
docker compose run --rm shell     # a prompt, with all five agents on PATH
```

## Prefer the container

Developing agent-bus on the machine that *runs* agent-bus is self-interfering.
The tests that message a real Claude session must switch the
`AGENT_BUS_*_DIR` overrides **off** to find one, so they cannot be isolated by
environment variable — only by kernel. The container has its own `HOME`,
`~/.agent-bus`, `/tmp/cc-socks` and PID namespace. Worth confirming once: run
`agent-bus list` on the host before and after, and watch it not change.

It also does the setup: five harnesses installed, codex logged in, grok's
folder trust granted. That is why there is no list of things to do first.

## Where the logs go

`--rm` throws the container away, so the `e2e` service points pytest's
`--basetemp` at `.e2e/` in the bind mount. One directory per spendy test,
holding its `*-log.jsonl`, the Claude peer's stream and any evidence files.

It does not accumulate: pytest empties an explicit basetemp at the start of
every run, so `.e2e/` always holds exactly the last one. Gitignored.

The service runs at `AGENT_BUS_LOG_LEVEL=INFO`. Override it from your shell.

## Prompts

Every prompt sent to a model lives in `tests/support/prompts/`, one file each.
Substitution is `{{name}}` — `$name` and `{name}` both occur in these prompts
for real. A token nobody supplies is an error, and so is a value nothing uses:
a model told to run `listen --name {{driver}}` does not fail, it registers an
agent called `{{driver}}`.

## Models

Each harness runs a pinned model, declared in `tests/support/models.py` and
nowhere else. They are cheap on purpose — the suite is testing the harness, not
the model, and left to their own defaults the five agents each reached for
their vendor's frontier model.

Override one for a single run:

```sh
AGENT_BUS_OMP_MODEL=openai-codex/gpt-5.6-sol ./spendy_tests.sh joins
docker compose run -e AGENT_BUS_OMP_MODEL=openai-codex/gpt-5.6-sol --rm e2e
```

## When one fails

Read the test. Each file says what it covers and why, and the assertions carry
the driver's output.

Two failures are worth knowing in advance because they look like agent-bus
bugs and are not:

- **`send.txt was not written`** — the driver never ran the step. The only
  genuinely model-dependent failure left.
- **an MCP server fails with `ENOENT`** — a stale config. The command is
  `agent-bus mcp`, or `uv run --project <repo> agent-bus mcp`.

Per-harness quirks — codex's login, grok's folder trust, omp's wedged stdin —
are in `docs/harnesses/<harness>.md`.
