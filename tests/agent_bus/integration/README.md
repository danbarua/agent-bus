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
| [`test_a_harness_joins_the_bus.py`](test_a_harness_joins_the_bus.md) | a harness joins, per harness -- pi, grok, codex, omp |
| [`test_both_views_of_the_roster_agree.py`](test_both_views_of_the_roster_agree.md) | our listing and Claude's `ListAgents` agree |
| [`test_messaging_a_claude_session.py`](test_messaging_a_claude_session.md) | a peer messages a live Claude session over UDS |
| [`test_watch_wakes_a_peer.py`](test_watch_wakes_a_peer.md) | `watch` wakes a peer |
| [`test_two_agents_hold_a_conversation.py`](test_two_agents_hold_a_conversation.md) | two agents hold a conversation |
| [`test_leave_stops_a_listener.py`](test_leave_stops_a_listener.md) | `leave` stops the listener it unregisters |
| [`test_join_reaches_a_claude_session.py`](test_join_reaches_a_claude_session.md) | `join` is reachable the instant it returns |
| [`test_read_and_ack_close_the_loop.py`](test_read_and_ack_close_the_loop.md) | `read` and `ack` close the ordinary loop |
| [`test_mcp_inbox_and_ack_close_the_loop.py`](test_mcp_inbox_and_ack_close_the_loop.md) | the same loop over a real MCP call |
| [`test_self_reflects_a_status_it_just_set.py`](test_self_reflects_a_status_it_just_set.md) | `self` and `list_agents` agree after `set_status` |
| [`test_a_joined_peer_is_named_in_claudes_list_agents_tool.py`](test_a_joined_peer_is_named_in_claudes_list_agents_tool.md) | a joined peer's real name reaches the `ListAgents` tool (#200's tool-path regression guard) |

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

It does not accumulate: pytest empties an explicit basetemp at the start of
every run, so `.e2e/` always holds exactly the last one. Gitignored.

The service runs at `AGENT_BUS_LOG_LEVEL=INFO`. Override it from your shell.

To capture one test on its own, for a diagram or a coverage read:

```sh
AGENT_BUS_LOG_LEVEL=INFO uv run pytest tests/agent_bus/integration/test_the_file_bus.py \
    -q --basetemp=/tmp/capture
find /tmp/capture -name '*-log.jsonl'
```

`AGENT_BUS_LOG_LEVEL=TRACE` is what
[`test_messaging_a_claude_session.py`](test_messaging_a_claude_session.md)
actually needed -- `frame in`/`frame parsed`/`frame delivered` records emit at
DEBUG severity and TRACE is the level that turns them on
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
AGENT_BUS_OMP_MODEL=openai-codex/gpt-5.6-sol ./spendy_tests.sh joins
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
   [`test_both_views_of_the_roster_agree.py`](test_both_views_of_the_roster_agree.md)
   (Claude's own `ListAgents`) and
   [`test_watch_wakes_a_peer.py`](test_watch_wakes_a_peer.md) (`watch`'s stdout
   line) both have a real mechanism the JSONL log cannot show at all -- a
   transcript or a stdout stream is the only record.
3. **A test proving delivery is not a test proving a protocol receipt.**
   [`test_messaging_a_claude_session.py`](test_messaging_a_claude_session.md)'s
   `frame delivered` is real; a wire-level confirmation *of our send* is not
   something this protocol has, measured directly rather than assumed.
4. **A verb with no e2e coverage at all is a real, different kind of gap from a
   CI-shaped test.** The first six files are all about a test's *shape*
   misleading a reader;
   [`test_leave_stops_a_listener.py`](test_leave_stops_a_listener.md) is what
   happens when there is no test's shape to be misled by in the first place --
   the bug lived entirely in the space `scripts/e2e_coverage.py` would have
   shown as empty.
5. **Closing a coverage gap does not owe you a bug.** `join` and `leave` were
   the same size of gap, checked with the same discipline; one had a real
   defect and one didn't.
   [`test_join_reaches_a_claude_session.py`](test_join_reaches_a_claude_session.md)
   is the null result, kept rather than left unwritten -- the value of driving a
   verb for real is confirming the design holds, not only catching it when it
   doesn't.
6. **The coverage tool can be blind to a verb that works.** `leave`'s bug lived
   in empty space `scripts/e2e_coverage.py` would have shown honestly.
   [`test_read_and_ack_close_the_loop.py`](test_read_and_ack_close_the_loop.md)'s
   was quieter: `ack` worked, was unit-tested, and was called by real harnesses
   all along -- it just never appeared in the log the coverage tool reads,
   because `@logged` was missing from the one function in its module that didn't
   carry it. A verb can pass every test that calls it directly and still be
   invisible to the tool built to say what gets called.
7. **A real capture is not always a mutation to check independently.** Every
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
