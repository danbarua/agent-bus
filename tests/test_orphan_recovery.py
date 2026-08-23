"""Mail that outlived every pointer to it.

Four inboxes on the author's machine held seven unread messages that nothing
could address: the peers were discovered, persisted, written to, then pruned
when their processes exited. Asking for one by its exact id answered "inbox
empty" -- and quietly read the caller's own mailbox instead.

Fixtures are synthesized rather than copied from a real home, so this is
hermetic; the shape is the one found on disk.
"""
import json
import os

import pytest

from agent_bus import store
from agent_bus.protocol import now_iso


@pytest.fixture
def bus(tmp_path, monkeypatch):
    home = str(tmp_path)
    monkeypatch.setenv("AGENT_BUS_HOME", home)
    store.ensure_dirs(home)
    return home


def _strand(home, entry_id, name, texts):
    """An inbox file with no roster entry pointing at it."""
    path = store._inbox_path_for(entry_id, home)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps({
                "id": f"m-{t}", "ts": now_iso(),
                "from": {"id": "s", "name": "sender", "kind": "other"},
                "to": {"id": entry_id, "name": name},
                "summary": "", "text": t, "replyTo": None, "read": False,
            }) + "\n" for t in texts)
    return path


REAL_SHAPE = [
    ("claude:26bc255e-aee7-43dd-8c3b-ff7a84015756", "peer-a", ["one"]),
    ("claude:4684f35f-4f8b-407a-a56a-6b436cf2f000", "peer-b", ["one", "two"]),
    ("claude:871144d5-318f-4973-8fb6-50d10520a98a", "peer-c", ["one"]),
    ("claude:ec0b2c47-db9d-4912-905f-02a3d8adc297", "peer-d", ["one", "two", "three"]),
]


def _strand_all(home):
    for eid, name, texts in REAL_SHAPE:
        _strand(home, eid, name, texts)


def test_stranded_mail_is_readable_by_its_address_alone(bus):
    """The bug, inverted. Before, this answered "inbox empty" -- and quietly
    returned the caller's own mailbox instead of saying it did not know."""
    _strand_all(bus)
    got = [m["text"] for m in store.get_inbox(
        name_or_id="claude:4684f35f-4f8b-407a-a56a-6b436cf2f000", home=bus)]
    assert got == ["one", "two"]


def test_an_unknown_target_is_an_error_not_an_empty_inbox(bus):
    """The half of the bug that made it hard to notice."""
    with pytest.raises(ValueError, match="no such agent"):
        store.get_inbox(name_or_id="nobody-at-all", home=bus)


def test_recovered_mail_can_also_be_acked(bus):
    """Readable but unclearable would just be a different trap."""
    _strand(bus, "claude:sid-ack", "peer", ["only"])
    msgs = store.get_inbox(name_or_id="claude:sid-ack", home=bus)
    assert store.ack_message(msgs[0]["id"], name_or_id="claude:sid-ack", home=bus) is True
    assert store.get_inbox(
        name_or_id="claude:sid-ack", unread_only=True, home=bus) == []


def test_all_seven_messages_become_readable(bus):
    """The acceptance criterion."""
    _strand_all(bus)
    orphans = store.find_orphaned_inboxes(bus)
    assert sum(o["unread"] for o in orphans) == 7

    for o in orphans:
        store.adopt_orphan(o, home=bus)

    recovered = []
    for eid, _, texts in REAL_SHAPE:
        got = [m["text"] for m in store.get_inbox(name_or_id=eid, home=bus)]
        assert got == texts, (eid, got)
        recovered += got
    assert len(recovered) == 7


def test_the_id_is_recovered_from_the_message_not_the_filename(bus):
    """_safe_id_for_fs is one-way and was lossy; inverting a filename is
    guesswork, and guessing wrong hands one agent's mail to another."""
    weird = "claude:a b/c"           # would be mangled on the way to a filename
    _strand(bus, weird, "odd-peer", ["hello"])
    orphans = store.find_orphaned_inboxes(bus)
    assert [o["id"] for o in orphans] == [weird]
    assert os.path.basename(orphans[0]["path"]) != f"{weird}.jsonl"

    store.adopt_orphan(orphans[0], home=bus)
    assert [m["text"] for m in store.get_inbox(name_or_id=weird, home=bus)] == ["hello"]


def test_an_adopted_entry_stays_out_of_the_listing(bus):
    """Readable, not present. Its process is long gone."""
    _strand_all(bus)
    for o in store.find_orphaned_inboxes(bus):
        store.adopt_orphan(o, home=bus)
    names = {a.name for a in store.list_agents(home=bus)}
    assert not ({n for _, n, _ in REAL_SHAPE} & names)


def test_adopting_twice_is_a_no_op(bus):
    _strand_all(bus)
    for o in store.find_orphaned_inboxes(bus):
        store.adopt_orphan(o, home=bus)
    assert store.find_orphaned_inboxes(bus) == []
    before = sorted(os.listdir(store._roster_dir(bus)))
    for o in store.find_orphaned_inboxes(bus):
        store.adopt_orphan(o, home=bus)
    assert sorted(os.listdir(store._roster_dir(bus))) == before


def test_an_adopted_entry_is_pruned_once_its_mail_is_read(bus):
    """It exists to be readable; when there is nothing left to read it goes."""
    _strand(bus, "claude:sid-x", "peer-x", ["only"])
    o = store.find_orphaned_inboxes(bus)[0]
    store.adopt_orphan(o, home=bus)
    msgs = store.get_inbox(name_or_id="claude:sid-x", home=bus)
    store.ack_message(msgs[0]["id"], name_or_id="claude:sid-x", home=bus)
    assert store.prune_dead_roster(bus) == 1


def test_a_mailbox_with_a_live_owner_is_not_an_orphan(bus):
    import subprocess
    holder = subprocess.Popen(["sleep", "30"])
    try:
        entry = store.register("owned", "grok", pid=holder.pid, home=bus)
        store.send_message(to=entry.id, text="hi", from_name="s", home=bus)
        assert store.find_orphaned_inboxes(bus) == []
    finally:
        holder.kill(); holder.wait()


def test_an_empty_inbox_file_is_not_reported(bus):
    open(store._inbox_path_for("claude:empty", bus), "w").close()
    assert store.find_orphaned_inboxes(bus) == []


def test_the_cli_reports_an_unknown_target_instead_of_crashing(bus, capsys):
    """store raises where it used to return []; the edges must not hand a user
    a traceback for a typo."""
    from agent_bus.cli import main

    assert main(["inbox", "--name", "definitely-not-an-agent"]) == 1
    assert "no such agent" in capsys.readouterr().err
    assert main(["ack", "some-id", "--name", "definitely-not-an-agent"]) == 1


def test_the_mcp_surface_turns_it_into_a_jsonrpc_error(bus):
    from agent_bus.mcp_server import handle_rpc

    resp = handle_rpc({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_inbox", "arguments": {"name": "definitely-not-an-agent"}},
    })
    assert "error" in resp
    assert "no such agent" in resp["error"]["message"]
