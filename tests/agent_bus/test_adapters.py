"""Adapter tests use synthetic fixtures, never live files."""
import json
import os

import pytest

from agent_bus.adapters.discovery import claude, omp

CLAUDE_FIXTURE = {
    "pid": 12345,
    "sessionId": "sess-abc-123",
    "cwd": "/tmp/test",
    "startedAt": 1724000000000,
    "procStart": "Fri Aug 21 12:00:00 2026",
    "version": "2.1.239",
    "peerProtocol": 1,
    "peerFeatures": ["notify_idle"],
    "kind": "interactive",
    "entrypoint": "cli",
    "messagingSocketPath": "/tmp/cc-socks/12345.sock",
    "name": "test-claude",
    "nameSince": 1724000000000,
    "updatedAt": 1724000001000,
    "status": "idle",
    "statusUpdatedAt": 1724000001000,
}

GROK_FIXTURE_ACTIVE = [
    {"session_id": "g-uuid-1", "pid": 23456, "cwd": "/tmp/g", "opened_at": "2026-..."}
]

OMP_CLIENT_FIXTURE = {"pid": 34567, "id": "omp-daemon-1", "projectDir": "/tmp/omp"}

CODEX_PM_FIXTURE = {"processes": [{"pid": 45678, "cwd": "/tmp/cx"}]}


def test_claude_adapter(tmp_path, monkeypatch):
    sdir = str(tmp_path / "claude-sess")
    os.makedirs(sdir)
    # write a fixture but with our real current pid so is_pid_alive passes
    live_pid = os.getpid()
    data = dict(CLAUDE_FIXTURE)
    data["pid"] = live_pid
    data["name"] = "live-claude"
    with open(os.path.join(sdir, f"{live_pid}.json"), "w") as f:
        json.dump(data, f)

    monkeypatch.setenv("AGENT_BUS_SESSIONS_DIR", sdir)
    found = claude.discover()
    assert len(found) == 1
    a = found[0]
    assert a["kind"] == "claude"
    assert a["name"] == "live-claude"
    assert a["pid"] == live_pid
    assert "claude:" in a["id"]
    assert a["native"]["messagingSocketPath"]


def _write_omp_daemon_client_and_session(
    base, pid, session_id, source, title, *,
    project_dir="/tmp/omp-project", client_id=None, timestamp="2026-09-01T00-00-00-000Z",
):
    """The on-disk shape discover() reads: a live daemon client record plus
    the session file that names it -- shared by the tests below so each one
    only has to say what varies (the title's source, mainly).

    Real shape, not a convenient stand-in: the client record's own `id` is a
    client-connection id, unrelated to any session id (a bug once matched
    them anyway -- see test_omp_adapter_matches_by_project_dir_not_client_id).
    The session file is named `<timestamp>_<session-id>.jsonl`, filed under a
    directory OMP derives from `project_dir` -- `omp._encode_project_dir`,
    verified against a real `~/.omp` capture, computes the same name OMP
    does, so the fixture and the code under test agree without the fixture
    hardcoding the encoding itself.
    """
    encoded_dir = omp._encode_project_dir(project_dir)
    cdir = base / "run" / "daemons" / "d1" / "clients"
    sdir = base / "agent" / "sessions" / encoded_dir
    cdir.mkdir(parents=True)
    sdir.mkdir(parents=True)
    (cdir / "c1.json").write_text(
        json.dumps({"pid": pid, "id": client_id or f"{pid}-conn-id", "projectDir": project_dir})
    )
    (sdir / f"{timestamp}_{session_id}.jsonl").write_text(
        json.dumps({"type": "title",
                    "v": "1",
                    "source": source,
                    "updatedAt": "2026-09-01T00:00:00Z",
                    "title": title})
    )


def test_omp_adapter(tmp_path, monkeypatch):
    """A daemon client record carries a pid, and a user-assigned session
    title supplies its name -- together they become exactly one row."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, session_name)
    ]
    assert found[0]["native"]["sessionId"] == session_id
    assert found[0]["id"] == f"omp:{session_id}"


def test_omp_adapter_id_is_stable_across_a_reconnect(tmp_path, monkeypatch):
    """A reconnect gets a new daemon connection id every time (a fresh
    client record, a new `id` field) but the same session id. The
    discovered row's own id must track the session, not the connection --
    store.discover_agents derives a discovered-only entry's inbox path from
    this id, so a per-connection value would mint a second inbox on every
    reconnect and strand the first one's unread mail."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    project_dir = "/tmp/omp-project"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name,
        project_dir=project_dir, client_id="first-connection-id",
    )
    first = omp.discover()

    # Reconnect: the daemon client record is replaced with a new connection
    # id, same pid, same project, same session file.
    cdir = base / "run" / "daemons" / "d1" / "clients"
    (cdir / "c1.json").write_text(json.dumps(
        {"pid": live_pid, "id": "second-connection-id", "projectDir": project_dir}
    ))
    second = omp.discover()

    assert first[0]["id"] == second[0]["id"] == f"omp:{session_id}"


@pytest.mark.parametrize("source", ["system", "auto", "assistant", ""])
def test_omp_adapter_only_discovers_user_assigned_names(tmp_path, monkeypatch, source):
    """Complements test_omp_adapter_uses_user_assigned_session_title: same
    shape, source flipped. A title OMP assigned itself (or any source other
    than "user") must not surface a roster row -- discover() has no way to
    tell a real handle from session-log noise, so it skips the session
    entirely rather than register it under a name nobody chose."""
    session_id = "__OMP_SESSION_ID__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source=source, title="__OMP_AUTO_TITLE__"
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.discover() == []


def test_omp_adapter_uses_user_assigned_session_title(tmp_path, monkeypatch):
    """When a session file carries a user-assigned title, use it as the name."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, session_name)
    ]


def test_omp_adapter_matches_by_project_dir_not_client_id(tmp_path, monkeypatch):
    """The bug this guards: discover() once matched a title by the daemon
    client's own connection id, which real OMP output never lines up with
    (the client record carries no session id at all). Give the client an id
    that collides with the session id, on a client whose projectDir matches
    no session directory, and confirm nothing is found -- if discover() were
    still matching on the id it would find this one anyway.
    """
    session_id = "__OMP_SESSION_ID__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title="__SHOULD_NOT_MATCH__",
        project_dir="/tmp/omp-project", client_id=session_id,
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))
    # A second client, same session_id-as-client-id trick, but pointed at a
    # project dir with no session directory at all.
    cdir = base / "run" / "daemons" / "d1" / "clients"
    (cdir / "c2.json").write_text(
        json.dumps({"pid": live_pid, "id": session_id, "projectDir": "/tmp/no-such-project"})
    )

    found = omp.discover()
    # The first client's projectDir does resolve, so it is found; the point
    # is *why* -- by directory, confirmed by the second client (same id,
    # unmatched dir) finding nothing.
    assert [a["cwd"] for a in found] == ["/tmp/omp-project"]


def test_session_id_of_survives_an_underscore_inside_the_id(tmp_path):
    """Splitting on the first "_" would cut inside a session id that itself
    contains one, returning a wrong id rather than a missing one -- and a
    wrong id silently owns a mailbox. Match the timestamp's own fixed shape
    instead of guessing from the first underscore."""
    path = str(tmp_path / "2026-09-01T00-00-00-000Z_08_overlap_bench.jsonl")
    assert omp._session_id_of(path) == "08_overlap_bench"


@pytest.mark.parametrize(("path", "encoded"), [
    ("/Users/dan/Code/AI/labkit", "-Code-AI-labkit"),
    ("/Users/dan/.omp/wt/labkit-assistant-5cbb478", "-.omp-wt-labkit-assistant-5cbb478"),
    ("/Users/dan/Code/AI/08_overlap_bench", "-Code-AI-08_overlap_bench"),
    # A sibling directory that merely starts with the same characters as
    # home is not a descendant of it -- home "/Users/dan" must not strip a
    # prefix off "/Users/dan2/proj".
    ("/Users/dan2/proj", "-Users-dan2-proj"),
    # A path outside home entirely: no prefix to strip, only slashes swapped.
    ("/tmp/some-project", "-tmp-some-project"),
])
def test_encode_project_dir_matches_real_omp_output(monkeypatch, path, encoded):
    """Locks the encoding scheme to three directory names read off a real
    `~/.omp/agent/sessions/` capture, home "/Users/dan", plus the boundary
    cases a bare `str.startswith` gets wrong."""
    monkeypatch.setattr(os.path, "expanduser", lambda p: "/Users/dan" if p == "~" else p)
    assert omp._encode_project_dir(path) == encoded


def test_omp_adapter_skips_a_project_dir_two_live_pids_share(tmp_path, monkeypatch):
    """A daemon client record carries no session id, only a projectDir -- so
    two *different* live processes in the same project are indistinguishable
    from here. Rather than hand both the same name, discover() must skip the
    directory entirely. (Two connections from the *same* pid are a different,
    unambiguous case -- see test_omp_adapter_merges_two_connections_from_one_pid.)
    """
    session_id = "__OMP_SESSION_ID__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    other_live_pid = os.getppid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title="__SHOULD_NOT_MATCH__",
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))
    # A second, genuinely different live process in the same project directory.
    cdir = base / "run" / "daemons" / "d1" / "clients"
    (cdir / "c2.json").write_text(
        json.dumps({"pid": other_live_pid, "id": "other-conn-id", "projectDir": "/tmp/omp-project"})
    )

    assert omp.discover() == []


def test_omp_adapter_merges_two_connections_from_one_pid(tmp_path, monkeypatch):
    """Two daemon client records with the *same* pid are one omp process
    holding two connections, not two processes -- unambiguous, and must
    still produce exactly one row rather than vanishing."""
    session_id = "__OMP_SESSION_ID__"
    session_name = "__OMP_SESSION_NAME__"
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, session_id, source="user", title=session_name,
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))
    # A second connection from the same process.
    cdir = base / "run" / "daemons" / "d1" / "clients"
    (cdir / "c2.json").write_text(
        json.dumps({"pid": live_pid, "id": "second-conn-id", "projectDir": "/tmp/omp-project"})
    )

    found = omp.discover()
    assert [(a["kind"], a["pid"], a["name"]) for a in found] == [
        ("omp", live_pid, session_name)
    ]


def test_omp_adapter_picks_the_most_recently_modified_title(tmp_path, monkeypatch):
    """A project directory holds one session file per session ever run
    there, not just the live one -- so two user-assigned titles (a rename,
    or a second session started later) must not turn discovery off for
    that project. The most recently *modified* one wins: mtime is a
    filesystem fact, unlike the caller-supplied `updatedAt` field."""
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__SESSION_ONE__", source="user", title="__OLD_TITLE__",
        timestamp="2026-09-01T00-00-00-000Z",
    )
    sdir = base / "agent" / "sessions" / omp._encode_project_dir("/tmp/omp-project")
    newer = sdir / "2026-09-02T00-00-00-000Z___SESSION_TWO__.jsonl"
    newer.write_text(
        json.dumps({"type": "title", "v": "1", "source": "user",
                    "updatedAt": "2026-09-02T00:00:00Z", "title": "__NEW_TITLE__"})
    )
    older = sdir / "2026-09-01T00-00-00-000Z___SESSION_ONE__.jsonl"
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    found = omp.discover()
    assert [(a["name"], a["native"]["sessionId"]) for a in found] == [
        ("__NEW_TITLE__", "__SESSION_TWO__")
    ]


def test_omp_adapter_skips_a_project_dir_with_a_genuine_mtime_tie(tmp_path, monkeypatch):
    """Two user-assigned titles modified in the same instant are a real
    ambiguity mtime cannot resolve either -- dropped rather than guessed."""
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__SESSION_ONE__", source="user", title="__TITLE_ONE__",
        timestamp="2026-09-01T00-00-00-000Z",
    )
    sdir = base / "agent" / "sessions" / omp._encode_project_dir("/tmp/omp-project")
    other = sdir / "2026-09-02T00-00-00-000Z___SESSION_TWO__.jsonl"
    other.write_text(
        json.dumps({"type": "title", "v": "1", "source": "user",
                    "updatedAt": "2026-09-02T00:00:00Z", "title": "__TITLE_TWO__"})
    )
    tie = 1_500_000
    os.utime(sdir / "2026-09-01T00-00-00-000Z___SESSION_ONE__.jsonl", (tie, tie))
    os.utime(other, (tie, tie))
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.get_session_header_rows() == {}
    assert omp.discover() == []


def test_omp_adapter_a_live_untitled_session_does_not_inherit_an_older_title(
    tmp_path, monkeypatch
):
    """Titling is the consent signal that gates a name being surfaced at
    all. A directory's newest *titled* session only counts if it is also
    the newest session in the directory overall -- otherwise something more
    recent and untitled is the one actually live, and it must not silently
    wear a dead, titled session's name."""
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__OLD_SESSION__", source="user", title="__OLD_TITLE__",
        timestamp="2026-09-01T00-00-00-000Z",
    )
    sdir = base / "agent" / "sessions" / omp._encode_project_dir("/tmp/omp-project")
    untitled = sdir / "2026-09-02T00-00-00-000Z___NEW_SESSION__.jsonl"
    untitled.write_text(json.dumps({
        "type": "session", "version": 3, "id": "__NEW_SESSION__",
        "timestamp": "2026-09-02T00:00:00.000Z", "cwd": "/tmp/omp-project",
    }))
    os.utime(sdir / "2026-09-01T00-00-00-000Z___OLD_SESSION__.jsonl", (1_000_000, 1_000_000))
    os.utime(untitled, (2_000_000, 2_000_000))
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.get_session_header_rows() == {}
    assert omp.discover() == []


def test_omp_adapter_skips_when_an_untitled_file_ties_the_titled_one(tmp_path, monkeypatch):
    """A same-second tie between a titled file and an untitled one is just
    as ambiguous as a tie between two titled files -- "at least as new",
    not "newer", so a tie doesn't quietly favour the title."""
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__SESSION_ONE__", source="user", title="__TITLE__",
        timestamp="2026-09-01T00-00-00-000Z",
    )
    sdir = base / "agent" / "sessions" / omp._encode_project_dir("/tmp/omp-project")
    untitled = sdir / "2026-09-02T00-00-00-000Z___SESSION_TWO__.jsonl"
    untitled.write_text(json.dumps({
        "type": "session", "version": 3, "id": "__SESSION_TWO__",
        "timestamp": "2026-09-02T00:00:00.000Z", "cwd": "/tmp/omp-project",
    }))
    tie = 1_500_000
    os.utime(sdir / "2026-09-01T00-00-00-000Z___SESSION_ONE__.jsonl", (tie, tie))
    os.utime(untitled, (tie, tie))
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.get_session_header_rows() == {}
    assert omp.discover() == []


def test_omp_adapter_survives_a_directory_shaped_like_a_jsonl_file(tmp_path, monkeypatch):
    """glob matches directories too -- a session dir containing a directory
    literally named "*.jsonl" must not abort the whole scan and silently
    truncate every project directory glob hadn't reached yet.

    Real `glob.glob` order isn't controlled by this test, and the bug this
    guards depends entirely on order (the bogus path aborting the scan
    before or after the good one is read) -- so `glob.glob` is stubbed to
    guarantee the bogus path is seen first, the case that actually exposes
    a truncated scan.
    """
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__GOOD_SESSION__", source="user", title="__GOOD_TITLE__",
        project_dir="/tmp/omp-project-z",
    )
    bogus_dir = base / "agent" / "sessions" / "-tmp-omp-project-a" / "weird.jsonl"
    bogus_dir.mkdir(parents=True)
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    real_glob = omp.glob.glob

    def _bogus_first(pattern):
        results = real_glob(pattern)
        return sorted(results, key=lambda p: "project-a" not in p)

    monkeypatch.setattr(omp.glob, "glob", _bogus_first)

    found = omp.discover()
    assert [a["name"] for a in found] == ["__GOOD_TITLE__"]


def test_omp_adapter_skips_a_bucket_where_raw_cwds_disagree(tmp_path, monkeypatch):
    """_encode_project_dir is not injective (its own docstring says so) --
    two genuinely different project directories can land in one bucket.
    One pid with connections opened from both is not the "two live pids"
    case discover() already guards, so the raw, unencoded cwd is checked
    too."""
    base = tmp_path / "omp"
    live_pid = os.getpid()
    _write_omp_daemon_client_and_session(
        base, live_pid, "__SESSION_ID__", source="user", title="__TITLE__",
        project_dir="/tmp/omp-project",
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))
    # A second connection, same pid, claiming a *different* real cwd that
    # happens to encode into the same bucket in the real scheme -- forced
    # here rather than found, since no two short paths actually collide.
    cdir = base / "run" / "daemons" / "d1" / "clients"
    (cdir / "c2.json").write_text(json.dumps(
        {"pid": live_pid, "id": "second-conn-id", "projectDir": "/tmp/omp-project-other"}
    ))
    monkeypatch.setattr(omp, "_encode_project_dir", lambda _path: "-tmp-omp-project")

    assert omp.discover() == []


def test_omp_adapter_skips_a_title_record_with_no_title(tmp_path, monkeypatch):
    """A `type: title`, `source: user` record with no `title` key is not a
    handle anyone chose -- it must not surface a roster row named `None`."""
    base = tmp_path / "omp"
    encoded_dir = omp._encode_project_dir("/tmp/omp-project")
    sdir = base / "agent" / "sessions" / encoded_dir
    sdir.mkdir(parents=True)
    (sdir / "2026-09-01T00-00-00-000Z___SESSION_ID__.jsonl").write_text(
        json.dumps({"type": "title", "v": "1", "source": "user",
                    "updatedAt": "2026-09-01T00:00:00Z"})
    )
    cdir = base / "run" / "daemons" / "d1" / "clients"
    cdir.mkdir(parents=True)
    (cdir / "c1.json").write_text(
        json.dumps({"pid": os.getpid(), "id": "c1", "projectDir": "/tmp/omp-project"})
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.get_session_header_rows() == {}
    assert omp.discover() == []


def test_a_terminal_session_file_is_not_an_agent(tmp_path, monkeypatch):
    """An omp agent is discovered from a daemon client record, which has a pid.

    Terminal-session files have none -- they hold a working directory and a
    path to a session log -- and a roster row without a live process is an
    address with nobody behind it. So these yield nothing.

    Real glob against a real file: the rule is about what a filename means,
    which is not something a stubbed glob can get wrong.
    """
    base = tmp_path / "omp"
    ts = base / "agent" / "terminal-sessions"
    ts.mkdir(parents=True)
    # The shape found on disk: a working directory and a session log. No pid.
    (ts / "ttys001").write_text(
        "/Users/someone/Code/project\n"
        "/Users/someone/.omp/agent/sessions/-Code-project/2026-08-17T14-35-16Z.jsonl\n"
    )
    monkeypatch.setattr(omp, "omp_dir", lambda: str(base))

    assert omp.discover() == []
