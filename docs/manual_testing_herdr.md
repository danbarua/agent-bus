# Manual testing with herdr

How a coding agent with the `herdr` skill drives a real harness, live, in an
adjacent terminal pane, against a dev checkout of agent-bus -- not mocks, not
a subprocess pipe in a pytest fixture, an actual second agent that actually
reacts. This is the recipe that confirmed #308's MCP resource-subscription
push against a real omp 18.1.6 session; it generalizes to anything herdr can
start (`--kind omp|codex|claude|grok`) and anything you need to watch happen
in real time rather than read about happening.

Requires `HERDR_ENV=1` (the calling session must itself be inside a herdr
pane) and the `herdr` binary on `PATH`. If `test HERDR_ENV=1` fails, this
whole recipe is unavailable -- say so and stop, per the herdr skill's own
rule.

## 1. A scratch project, pointed at your dev checkout

The harness needs its own project directory so its MCP config doesn't
collide with a real one, and that config needs to point at *your edited
source*, not whatever release is on `PATH` -- `PYTHONPATH` plus
`AGENT_BUS_HOME`, not `pip install -e`, so nothing has to be reinstalled
between edits.

```sh
D=/private/tmp/.../scratchpad/herdr-live-test   # your session's scratchpad
mkdir -p "$D/.omp"
cat > "$D/.omp/mcp.json" << EOF
{
  "mcpServers": {
    "agent-bus": {
      "command": "python3",
      "args": ["-m", "agent_bus", "mcp"],
      "env": {
        "PYTHONPATH": "$REPO/src",
        "AGENT_BUS_HOME": "$D/bus"
      }
    }
  }
}
EOF
```

Enable whatever setting the thing you're testing needs -- e.g. real-time
push needs omp's own `mcp.notifications`:

```sh
cat > "$D/.omp/settings.json" << 'EOF'
{"mcp.notifications": true, "mcp.notificationDebounceMs": 200}
EOF
```

Other harnesses take their MCP config differently (`.claude/config.json`,
codex's `config.toml`, grok's own format) -- same idea, same two knobs:
point `command`/`args`/`env` at the dev checkout, not the installed release.

## 2. Split a pane, start the harness there

```sh
herdr pane split --current --direction right --cwd "$D" --no-focus
# -> read .result.pane.pane_id from the JSON response
herdr agent start livetest --kind omp --pane <pane-id>
```

**A fresh pane's shell can eat the first keystroke.** A new terminal often
has its own startup noise (an oh-my-zsh update prompt, in practice) that
consumes part of whatever herdr types first, and `agent start` times out
waiting for a harness it never saw launch correctly. Symptom: `herdr agent
get <name>` comes back `agent_not_found` right after a `timeout` error, not
`agent_not_ready` (which would mean it saw *something*, just not enough).
Fix: just retry `herdr agent start` -- the shell has settled by the second
attempt.

## 3. One narrow prompt, not a wait loop

```sh
herdr agent prompt livetest \
  "Call the agent-bus MCP server's self tool and reply with exactly the name field it returns, nothing else." \
  --wait --timeout 60000
```

Ask it to do one small, checkable thing and stop -- never "wait for a
message and respond," which is the CI-shaped `park`/`hub` pattern this
recipe exists partly to make unnecessary (see
`docs/harnesses/omp.md`'s "A real push exists now" section). You want to
know its registered name before you can address it from outside.

`--wait` can report `agent_prompt_stalled` in the first ~5s even when the
turn is genuinely running (observed: the model was already mid-tool-call
when the stall check fired). Don't take that as failure -- check
`herdr agent get livetest`; if `agent_status` is `working`, it's fine, just
poll properly:

```sh
herdr agent wait livetest --timeout 60000
herdr agent read livetest --source recent-unwrapped --lines 100
```

Read the transcript for the name it reported (e.g. `omp-86898`) -- that's
the roster name everything in step 4 addresses.

## 4. Act on it from outside, and just watch

This is the actual test. Don't prompt the pane again -- do the thing you
want it to react to from a *separate* shell command, using the same
`AGENT_BUS_HOME`, then watch whether the pane moves on its own:

```sh
AGENT_BUS_HOME="$D/bus" PYTHONPATH="$REPO/src" \
  python3 -m agent_bus send omp-86898 -m "..." --summary "..." --json

sleep 3
herdr agent get livetest   # agent_status: "working" with nobody prompting it
                            # is the whole result
herdr agent wait livetest --timeout 60000
herdr agent read livetest --source recent-unwrapped --lines 120
```

**One surprise worth knowing in advance**: `agent-bus list`/discovery is not
scoped by `AGENT_BUS_HOME` -- it also reads real, machine-wide session
files (`~/.claude/sessions/*.json`, omp's own daemon-client records), so a
plain `list` from your scratch bus shows your test peer *alongside* every
real live session on the machine. That's expected, not a bug in your
isolation -- the roster write itself is correctly scoped; only discovery
is broader.

## 5. Tear down

The scratch dir often won't `rm -rf` cleanly while the harness process (or
its `agent-bus mcp` child) still has it open -- kill the agent first:

```sh
herdr agent send-keys livetest ctrl+c
herdr pane run <pane-id> "exit"
sleep 1
rm -rf "$D"
```

## What this catches that a pytest fixture can't

Everything up to the MCP protocol frame arriving on stdout was already
provable with `subprocess.Popen` in a test (see
`tests/agent_bus/mcp/test_mcp_stdio.py`). What only this recipe proves:
whether the *harness's own* client-side machinery -- its notification
listener, its update-injection setting, its turn-scheduling -- actually
does the thing its own docs say it does, with a real model genuinely
reacting rather than a test asserting on a frame. Use it whenever the
question is "does the other side actually behave the way its source says
it will," not "did my server send the right bytes."
