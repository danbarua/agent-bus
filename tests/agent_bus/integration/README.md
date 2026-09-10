# Integration tests

Most of these spawn a **real coding agent** or a **live Claude Code session**.
Those are marked `spendy`: they cost money and minutes, and an ordinary
`pytest` run skips them.

```sh
./e2e_tests.sh                 # all of them
./e2e_tests.sh roster          # only tests/**/test_*roster*.py
./e2e_tests.sh -k listener     # anything starting with - goes to pytest
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

## What each test shows

One `.md` beside each `test_*.py`, holding a sequence diagram built from a real
captured `AGENT_BUS_LOG_FILE` -- not from reading the test source.
`scripts/e2e_coverage.py` reads the same evidence for a coverage matrix across
every test; these read a handful of individual runs to show one mechanism each.

They sit here rather than under `docs/` deliberately. The reader who needs them
is the one already in this directory, opening the test next to them.

| | |
| --- | --- |
| [`test_the_file_bus.py`](test_the_file_bus.md) | the file bus, no harness |
| [`test_a_harness_joins_the_bus.py`](test_a_harness_joins_the_bus.md) | a harness joins, per harness -- grok, codex, omp |
| [`test_watch_wakes_a_peer.py`](test_watch_wakes_a_peer.md) | `watch` wakes a peer |
| [`test_two_agents_hold_a_conversation.py`](test_two_agents_hold_a_conversation.md) | two agents hold a conversation |
| [`test_mcp_inbox_and_ack_close_the_loop.py`](test_mcp_inbox_and_ack_close_the_loop.md) | the same loop over a real MCP call |
| [`test_self_reflects_a_status_it_just_set.py`](test_self_reflects_a_status_it_just_set.md) | `self` and `list_agents` agree after `set_status` |

A new test gets a new sibling `.md`. There is no index to update but the table
above.

**Read `docs/harness-compatibility.md`'s "CI-shaped and use-shaped are
different questions" first.** It says why these files exist in one paragraph: a
run nobody watches wants a blocking call and a known end; a person working
wants an agent that keeps going. The two want opposite things from the exact
same code paths, and a test built for the first is not a demonstration of the
second. Each file says, explicitly, which of the two it is a test of -- because
the tests are what a cold reader meets first, and CI's own shape (a
deterministic wait, a single round trip, a driver polling in a tight loop) is
the one that gets copied into "how agent-bus is used" by mistake.

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

One shape, so reading a second test's output needs no second mental model:

```
.e2e/<test id>/
  <test name>[-<variant>]-log.jsonl   the bus's own structured record
  bus/                                AGENT_BUS_HOME for this test
  bus-{sessions,socks,grok,omp}/      the native registries, isolated
  proj/                               working directory a driven harness is given
    .omp/                             omp config the test wrote for it
  peer-<name>/                        one per peer: its cwd, its .omp/, its streams
  spool/                              a bridge's own queue, where one runs
  evidence/                           files a driver, bridge or watch wrote
```

`tests/agent_bridge/`'s e2e tests write the same shape -- the `evidence`
fixture is in `tests/conftest.py` rather than this directory's, so both
suites get one definition of it.

Empty directories are pruned after a run (`e2e_tests.sh`), so what is left is
what something wrote -- which is only readable as signal if every test writes
to the same places. Two peers used to share one working directory while
keeping separate log directories, and a `watch.out` used to land beside the
directories rather than in `evidence/`.

It does not accumulate: pytest empties an explicit basetemp at the start of
every run, so `.e2e/` always holds exactly the last one. Gitignored.

The service runs at `AGENT_BUS_LOG_LEVEL=INFO`. Override it from your shell.

To capture one test on its own, for a diagram or a coverage read:

```sh
AGENT_BUS_LOG_LEVEL=INFO uv run pytest tests/agent_bus/integration/test_the_file_bus.py \
    -q --basetemp=/tmp/capture
find /tmp/capture -name '*-log.jsonl'
```

`AGENT_BUS_LOG_LEVEL=TRACE` is what a UDS round trip to a live Claude session
needs -- `frame in`/`frame parsed`/`frame delivered` records emit at DEBUG
severity and TRACE is the level that turns them on
(`docs/structured-logging.md`). INFO is enough for every other one.

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
AGENT_BUS_OMP_MODEL=openai-codex/gpt-5.6-sol ./e2e_tests.sh joins
docker compose run -e AGENT_BUS_OMP_MODEL=openai-codex/gpt-5.6-sol --rm e2e
```

## What recurs across all of them

Every diagram was built from a real `*-log.jsonl`, not from reading test source
-- `scripts/e2e_coverage.py` reads the same files for a coverage matrix rather
than one mechanism at a time. Seven things recur:

1. **CI needs a deterministic end; real use has none.** The roster, `watch` and
   conversation files are explicit about this, in their own module docstrings,
   before their `.md` restates it.
2. **The structured log is not the whole story.**
   [`test_watch_wakes_a_peer.py`](test_watch_wakes_a_peer.md) (`watch`'s stdout
   line) has a real mechanism the JSONL log cannot show at all -- a stdout
   stream is the only record.
3. **A real capture is not always a mutation to check independently.** Every
   other file ends with something to query back -- a roster entry, an inbox
   file, a read flag.
   [`test_mcp_inbox_and_ack_close_the_loop.py`](test_mcp_inbox_and_ack_close_the_loop.md)'s
   `list_agents` and `get_inbox` are read-only, so two of its three assertions
   rest on the model relaying one strict token rather than on a file changing
   state. That is a real, named weaker form of evidence, not a hidden one -- and
   it is why the one call in that test with a mutation to check (`ack_message`)
   is checked against the mutation and not the model's word for it.

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
