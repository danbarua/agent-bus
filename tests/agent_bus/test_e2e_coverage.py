"""scripts/e2e_coverage.py against synthesized evidence, not a copy of a real
run -- the shape matters (surface/verb/kind grouping, tool-over-method for
MCP, malformed-line tolerance), the exact fields captured on 2026-08-31 do
not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "e2e_coverage.py"

spec = importlib.util.spec_from_file_location("e2e_coverage", SCRIPT)
assert spec and spec.loader
e2e_coverage = importlib.util.module_from_spec(spec)
sys.modules["e2e_coverage"] = e2e_coverage
spec.loader.exec_module(e2e_coverage)


def _write(path: Path, *records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_groups_by_surface_verb_kind(tmp_path):
    _write(
        tmp_path / "one" / "test-log.jsonl",
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
        {"surface": "mcp", "verb": "register", "kind": "grok", "ok": True},
    )
    cells = e2e_coverage.scan(tmp_path)
    assert len(cells[("cli", "send", "other")]) == 2
    assert len(cells[("mcp", "register", "grok")]) == 1


def test_a_tools_call_record_is_grouped_by_its_tool_not_the_rpc_method():
    """The MCP surface logs `tools/call` for every tool it dispatches; the
    interesting fact is which tool, in `tool`, not the four characters every
    one of those records shares in `method`."""
    rec = {"surface": "mcp", "method": "tools/call", "tool": "send_message", "kind": "omp"}
    assert e2e_coverage._verb_of(rec) == "send_message"


def test_a_non_mcp_record_falls_back_through_verb_then_message():
    assert e2e_coverage._verb_of({"verb": "register"}) == "register"
    assert e2e_coverage._verb_of({"method": "initialize"}) == "initialize"
    assert e2e_coverage._verb_of({"message": "frame delivered"}) == "frame delivered"
    assert e2e_coverage._verb_of({}) is None


def test_a_truncated_trailing_line_is_skipped_not_fatal(tmp_path):
    """A killed process can leave a partial last line -- the same shape
    watch.py's own `_read_records` already tolerates on the write side."""
    path = tmp_path / "sub" / "test-log.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"surface": "cli", "verb": "send", "kind": "other"}\n'
        '{"surface": "cli", "verb": "inbox", "kind": "other", "ok": tru'
    )
    cells = e2e_coverage.scan(tmp_path)
    assert ("cli", "send", "other") in cells
    assert ("cli", "inbox", "other") not in cells


def test_a_record_with_no_verb_signal_is_skipped(tmp_path):
    _write(tmp_path / "test-log.jsonl", {"surface": "cli", "kind": "other"})
    assert e2e_coverage.scan(tmp_path) == {}


def test_failed_calls_are_counted_and_shown_in_the_table(tmp_path):
    _write(
        tmp_path / "test-log.jsonl",
        {"surface": "cli", "verb": "send", "kind": "other", "ok": False},
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
    )
    cells = e2e_coverage.scan(tmp_path)
    table = e2e_coverage.render_table(cells)
    assert "2 (1 failed)" in table


def test_json_output_is_one_row_per_cell_with_its_sources(tmp_path):
    _write(
        tmp_path / "a" / "test-log.jsonl",
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
    )
    cells = e2e_coverage.scan(tmp_path)
    rows = json.loads(e2e_coverage.render_json(cells))
    assert len(rows) == 1
    row = rows[0]
    assert row["surface"] == "cli"
    assert row["verb"] == "send"
    assert row["kind"] == "other"
    assert row["count"] == 1
    assert row["failed"] == 0
    assert str(tmp_path / "a" / "test-log.jsonl") in row["sources"]


def test_main_refuses_a_missing_directory(tmp_path, capsys):
    rc = e2e_coverage.main(["--dir", str(tmp_path / "nope")])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_main_refuses_a_directory_with_no_verb_records(tmp_path, capsys):
    (tmp_path / "empty-log.jsonl").write_text("")
    rc = e2e_coverage.main(["--dir", str(tmp_path)])
    assert rc == 1
    assert "no verb records" in capsys.readouterr().err


def test_main_prints_a_table_by_default(tmp_path, capsys):
    _write(
        tmp_path / "test-log.jsonl",
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
    )
    rc = e2e_coverage.main(["--dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "## cli" in out
    assert "| send |" in out


def test_main_json_flag_prints_valid_json(tmp_path, capsys):
    _write(
        tmp_path / "test-log.jsonl",
        {"surface": "cli", "verb": "send", "kind": "other", "ok": True},
    )
    rc = e2e_coverage.main(["--dir", str(tmp_path), "--json"])
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["verb"] == "send"
