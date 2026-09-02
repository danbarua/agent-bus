"""Driving `agent-bus` from a test, the way an agent would.

Everything here shells out to the CLI. Nothing imports agent_bus: these tests
are outside-in, and a test that reaches into the package can pass while the
thing an agent actually invokes is broken.
"""

from __future__ import annotations

import json
import os
import subprocess

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# What a prompt tells an agent to type. `uv run --project` pins which checkout
# is under test rather than whatever is on PATH.
CLI = f"uv run --project {REPO} agent-bus"


def bus_env(home, *, isolate_native=True):
    """Environment for a bus CLI call.

    `isolate_native=False` leaves the real sessions and socket directories in
    place, which anything that must see -- or be seen by -- a live Claude
    session requires.

    Isolating means pointing every harness registry at an empty directory, not
    just the sockets: `list` unions the roster with whatever discovery finds,
    so without it an assertion sees your own live sessions, and a test that
    sends to a name could reach a real agent.
    """
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = str(home)
    registries = ("AGENT_BUS_SESSIONS_DIR", "AGENT_BUS_SOCK_DIR",
                  "AGENT_BUS_GROK_DIR", "AGENT_BUS_OMP_DIR")
    if isolate_native:
        for var, sub in zip(registries, ("-sessions", "-socks", "-grok", "-omp"), strict=True):
            env[var] = str(home) + sub
            os.makedirs(env[var], exist_ok=True)
    else:
        for var in registries:
            env.pop(var, None)
    return env


def bus(home, *args, isolate_native=True, timeout=60):
    return subprocess.run(
        ["uv", "run", "--project", REPO, "agent-bus", *args],
        env=bus_env(home, isolate_native=isolate_native),
        cwd=REPO, capture_output=True, text=True, timeout=timeout,
    )


def register(home, name, kind, *, pid=None, isolate_native=True):
    """Register under a pid that outlives the call.

    `register` defaults to the calling process, and `uv run agent-bus` exits
    at once -- the entry would be pruned as dead before the next command.
    """
    r = bus(home, "register", "--name", name, "--kind", kind,
            "--pid", str(pid or os.getpid()), isolate_native=isolate_native)
    assert r.returncode == 0, f"register {name} failed: {r.stderr}"
    return r


def inbox(home, name, *, isolate_native=True):
    """Messages for `name`. Raises if there is no such agent.

    Deliberately not returning [] on failure: "inbox empty" and "no such agent"
    are different answers, and a helper that cannot tell them apart cannot test
    either.
    """
    r = bus(home, "inbox", "--json", "--target", name, isolate_native=isolate_native)
    if r.returncode != 0:
        raise AssertionError(f"inbox --target {name} failed: {r.stderr.strip()}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        raise AssertionError(f"inbox --target {name} gave non-JSON: {r.stdout!r}") from e


def read_marker(path, step, r):
    """A file the driver's shell wrote, or a failure naming the step it skipped.

    The assertions read these rather than the driver's stdout. A run that had
    completed the whole round trip once failed because the driver wrote "The
    inbox contains a message." where the test grepped for `SEND_EXIT=0`. Asking
    a model to relay shell output verbatim is asking for the one thing it will
    not do reliably; the shell records the fact, the model only runs the
    command.
    """
    if not path.exists():
        raise AssertionError(
            f"the driver never ran {step}: {path.name} was not written.\n"
            f"driver stdout:\n{r.stdout[-2500:]}"
        )
    return path.read_text().strip()
