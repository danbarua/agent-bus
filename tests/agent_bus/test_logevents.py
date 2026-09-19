"""The shape of a record, and what a call site cannot get wrong.

Sixteen different key orders in 280 real records is what the previous
formatter produced: identity merged in as it was learned, `message` landing
wherever the merge left it. Every test here is one of those ways to drift,
made to fail.
"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import logging
import threading
import typing
from dataclasses import dataclass

import pytest

from agent_bus import log, logevents
from agent_bus.protocol import MessageId

ENVELOPE = list(logevents.ENVELOPE)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Sent(logevents.MessageEvent):
    level: typing.ClassVar[logevents.Level] = "info"
    message: typing.ClassVar[str] = "test_sent"
    # Declared out of column order on purpose: layout is the registry's, not
    # the class's.
    count: int
    to: str
    ok: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class _Started(logevents.Event):
    level: typing.ClassVar[logevents.Level] = "info"
    message: typing.ClassVar[str] = "test_started"
    url: str | None = None
    token_source: str | None = None


@pytest.fixture
def written(tmp_path, monkeypatch):
    dest = tmp_path / "out.jsonl"
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(dest))
    monkeypatch.delenv("AGENT_BUS_LOG_LEVEL", raising=False)
    logevents.reset_identity()
    # A real lookup would find whoever launched pytest and stamp `agent`/`kind`.
    monkeypatch.setattr(log, "_who", dict)
    log.configure(force=True)

    def read() -> list[dict]:
        return [json.loads(line) for line in dest.read_text().splitlines()]

    yield read
    for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
        h.close()
        logging.getLogger(log.LOGGER_NAME).removeHandler(h)
    logevents.reset_identity()


def _keys_in_envelope_order(rec: dict) -> list[str]:
    return [k for k in rec if k in ENVELOPE]


def test_the_envelope_is_the_same_order_however_much_identity_is_known(written):
    """`message` was position 3 in one record and position 9 in the next,
    because identity keys were merged in as they were learned."""
    log.emit(_Started())
    logevents.identify(adapter="bridge")
    log.emit(_Started())
    logevents.identify(address="desktop:claude", agent="desktop-claude", kind="desktop")
    log.emit(_Started())
    logevents.identify(client="omp-coding-agent")
    with logevents.bind_trace(MessageId("m-1")):
        log.emit(_Started())
    for rec in written():
        present = _keys_in_envelope_order(rec)
        assert present == [k for k in ENVELOPE if k in rec], rec
        assert list(rec)[: len(present)] == present, (
            "envelope keys must come first, together", list(rec))


def test_message_comes_after_trace_id_and_before_every_event_field(written):
    log.emit(_Sent(message_id=MessageId("m-9"), count=2, to="x", ok=True))
    (rec,) = written()
    keys = list(rec)
    assert keys.index("trace_id") + 1 == keys.index("message")
    assert keys.index("message") + 1 == keys.index("ok"), keys


def test_event_fields_are_in_registry_order_not_declaration_order(written):
    log.emit(_Sent(message_id=MessageId("m-9"), count=2, to="x", ok=True))
    (rec,) = written()
    tail = list(rec)[list(rec).index("message") + 1:]
    assert tail == sorted(tail, key=logevents.rank)
    assert tail == ["ok", "to", "count"], tail


def test_a_message_event_writes_its_id_as_trace_id_and_only_that(written):
    log.emit(_Sent(message_id=MessageId("m-9"), count=1, to="x", ok=True))
    (rec,) = written()
    assert rec["trace_id"] == "m-9"
    assert "message_id" not in rec


def test_a_message_event_cannot_be_built_without_its_id():
    with pytest.raises(TypeError):
        _Sent(count=1, to="x", ok=True)  # type: ignore[call-arg]


def test_an_event_cannot_carry_a_key_the_registry_does_not_own():
    with pytest.raises(TypeError):
        _Started(surprise="x")  # type: ignore[call-arg]


def test_an_unset_field_is_omitted_not_written_as_null(written):
    log.emit(_Started(url="https://x"))
    (rec,) = written()
    assert rec["url"] == "https://x"
    assert "token_source" not in rec
    assert all(v is not None for v in rec.values())


def test_the_ambient_trace_is_bound_for_the_block_and_gone_after(written):
    with logevents.bind_trace(MessageId("m-1")):
        log.emit(_Started())
        with logevents.bind_trace(MessageId("m-2")):
            log.emit(_Started())
        log.emit(_Started())
    log.emit(_Started())
    assert [r.get("trace_id") for r in written()] == ["m-1", "m-2", "m-1", None]


def test_the_ambient_trace_is_released_when_the_work_raises(written):
    with pytest.raises(RuntimeError), logevents.bind_trace(MessageId("m-1")):
        raise RuntimeError
    log.emit(_Started())
    assert "trace_id" not in written()[0]


def test_the_ambient_trace_stamps_a_legacy_record_too(written):
    with logevents.bind_trace(MessageId("m-1")):
        log.info("something happened", count=3)
    (rec,) = written()
    assert rec["trace_id"] == "m-1"
    keys = list(rec)
    assert keys.index("trace_id") + 1 == keys.index("message")


def test_a_legacy_trace_id_keyword_is_hoisted_into_the_envelope(written):
    log.warn("could not forward", trace_id="m-7", error="X")
    (rec,) = written()
    keys = list(rec)
    assert keys.index("trace_id") + 1 == keys.index("message") < keys.index("error")


def test_the_trace_does_not_reach_a_thread_that_did_not_copy_the_context(written):
    """Documented, and the reason identity is not a ContextVar: this is what a
    plain `threading.Thread` does."""
    seen: dict[str, object] = {}

    def probe():
        seen["plain"] = logevents.current_trace()

    def copied():
        seen["copied"] = logevents.current_trace()

    with logevents.bind_trace(MessageId("m-1")):
        t = threading.Thread(target=probe)
        t.start()
        t.join()
        ctx = contextvars.copy_context()
        t2 = threading.Thread(target=lambda: ctx.run(copied))
        t2.start()
        t2.join()
    assert seen == {"plain": None, "copied": "m-1"}


def test_process_identity_is_visible_to_every_thread(written):
    logevents.identify(adapter="bridge", address="desktop:claude")
    t = threading.Thread(target=lambda: log.emit(_Started()))
    t.start()
    t.join()
    (rec,) = written()
    assert rec["adapter"] == "bridge"
    assert rec["address"] == "desktop:claude"


def test_identify_refuses_an_unknown_field_and_an_unknown_adapter():
    with pytest.raises(TypeError):
        logevents.identify(colour="red")
    with pytest.raises(ValueError):
        logevents.identify(adapter="carrier-pigeon")


def test_surface_is_the_old_name_for_adapter(written):
    logevents.identify(surface="mcp")
    log.emit(_Started())
    assert written()[0]["adapter"] == "mcp"


def test_identify_ignores_none_so_a_later_call_cannot_erase_an_earlier_one(written):
    logevents.identify(address="desktop:claude")
    logevents.identify(address=None)
    log.emit(_Started())
    assert written()[0]["address"] == "desktop:claude"


def test_time_is_fixed_width_and_orders_within_one_second(written):
    for _ in range(5):
        log.emit(_Started())
    stamps = [r["time"] for r in written()]
    assert all(len(t) == 24 and t.endswith("Z") for t in stamps), stamps
    assert stamps == sorted(stamps)


def test_a_bound_identity_stops_the_per_record_roster_lookup(written, monkeypatch):
    """`_who()` walks ancestors to guess a name -- which labelled early bridge
    records with the Claude session that happened to launch them."""
    calls = []
    monkeypatch.setattr(
        log, "_who", lambda: calls.append(1) or {"agent": "guess", "kind": "claude"})
    logevents.identify(agent="desktop-claude", kind="desktop")
    log.emit(_Started())
    assert calls == []
    assert written()[0]["agent"] == "desktop-claude"


# -- the registry ------------------------------------------------------------


def _shipped_events():
    import agent_bridge.events  # noqa: F401  # register its events

    return [c for c in logevents.all_events()
            if c.__module__.split(".")[0] in ("agent_bus", "agent_bridge")]


def test_every_field_an_event_declares_is_owned_by_the_registry():
    for cls in _shipped_events():
        for f in dataclasses.fields(cls):
            name = "trace_id" if f.name == "message_id" else f.name
            spec = logevents.FIELDS.get(name)
            assert spec is not None, f"{cls.__name__}.{f.name} is not in logevents.FIELDS"
            assert not spec.envelope or name == "trace_id", (
                f"{cls.__name__}.{f.name} is written by the logger, not by an event")


def test_every_field_is_typed_as_the_registry_says():
    for cls in _shipped_events():
        hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            if f.name == "message_id":
                continue
            want = logevents.FIELDS[f.name].type
            hint = hints[f.name]
            args = [a for a in typing.get_args(hint) if a is not type(None)] or [hint]
            got = [typing.get_origin(a) or a for a in args]
            assert got == [want], f"{cls.__name__}.{f.name}: {hint} is not {want.__name__}"


def test_every_event_names_itself_and_no_two_share_a_name():
    seen: dict[str, str] = {}
    for cls in _shipped_events():
        assert isinstance(getattr(cls, "message", None), str), cls
        assert getattr(cls, "level", None) in typing.get_args(logevents.Level), cls
        assert cls.message not in seen, (
            f"{cls.__name__} and {seen[cls.message]} share {cls.message!r}")
        seen[cls.message] = cls.__name__


def test_no_event_outside_trace_may_declare_a_content_field():
    for cls in _shipped_events():
        if cls.level == "trace":
            continue
        for f in dataclasses.fields(cls):
            spec = logevents.FIELDS.get(f.name)
            assert spec is None or not spec.content, (
                f"{cls.__name__}.{f.name} may carry message content; only TRACE may")


def test_the_registry_has_no_dead_vocabulary():
    used = {f.name for c in _shipped_events() for f in dataclasses.fields(c)}
    unused = [n for n, s in logevents.FIELDS.items()
              if not s.envelope and n not in used]
    assert not unused, (
        f"registered but never emitted: {unused}. A field nobody writes is a "
        "column somebody will assume they can filter on.")


def test_every_event_is_emitted_somewhere():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    source = "\n".join(p.read_text() for p in root.rglob("*.py")
                       if p.name not in ("events.py", "logevents.py"))
    dead = [c.__name__ for c in _shipped_events()
            if not re.search(rf"\b{c.__name__}\(", source)]
    assert not dead, f"events nothing constructs: {dead}"
