#!/usr/bin/env python3
"""What a spendy run actually exercised, read back from its own evidence.

`spendy_tests.sh` and the `e2e` docker service both leave one directory per
test under `.e2e/`, each holding a `*-log.jsonl` at whatever
`AGENT_BUS_LOG_LEVEL` was set to -- INFO by default now, which is every verb
call: who, what, which harness, whether it worked. That is a coverage matrix
already sitting on disk, one line per cell, and this reads it rather than
re-deriving it from source.

Usage:
    scripts/e2e_coverage.py                 # reads .e2e/, prints a table
    scripts/e2e_coverage.py --dir DIR
    scripts/e2e_coverage.py --json

A cell is (surface, verb, kind). `surface` and `kind` are the same fields
`docs/structured-logging.md` already documents; `verb` is the CLI/MCP verb
name -- for an MCP `tools/call` record that is the `tool` field, not the
JSON-RPC method, since "tools/call" is not the interesting fact and "which
tool" is.

Deliberately not: latency between records (a different question -- where a
conversation test spends wall-clock waiting on a model, not what got called;
the `ms` field here is each call's own duration, already in the data if that
is what is wanted next) and diagrams (a rendering choice on top of this
table, not a reason to widen what this script reads).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# The axes docs/harness-compatibility.md's hand-maintained matrix already
# uses, so a generated table sorts the same way a reader expects.
KIND_ORDER = ("claude", "grok", "omp", "codex", "pi", "desktop", "pending", "other")
SURFACE_ORDER = ("cli", "mcp", "listen")


def _verb_of(rec: dict[str, Any]) -> str | None:
    """The thing actually being measured, not the transport that carried it.

    An MCP `tools/call` record's `method` is always the same four characters
    -- the tool name is in `tool`. Everything else names itself directly:
    `verb` on the CLI surface, `method` for other JSON-RPC calls
    (`initialize`, `tools/list`, ...), `message` as the last resort, which is
    what the UDS listener's frame-lifecycle records use.
    """
    return rec.get("tool") or rec.get("verb") or rec.get("method") or rec.get("message")


def _sort_key(seq: tuple[str, ...]):
    order = {v: i for i, v in enumerate(seq)}
    return lambda v: (order.get(v, len(order)), v)


def scan(root: Path) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    """Every `*-log.jsonl` under `root`, grouped by (surface, verb, kind).

    Malformed lines are skipped rather than failing the whole scan -- a
    truncated last line from a killed process is expected, not corruption.
    """
    cells: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(root.rglob("*-log.jsonl")):
        with open(path, encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                verb = _verb_of(rec)
                if not verb:
                    continue
                surface = rec.get("surface") or "?"
                kind = rec.get("kind") or "-"
                rec["_source"] = path
                cells[(surface, verb, kind)].append(rec)
    return cells


def render_table(cells: dict[tuple[str, str, str], list[dict[str, Any]]]) -> str:
    surfaces = sorted({k[0] for k in cells}, key=_sort_key(SURFACE_ORDER))
    out = []
    for surface in surfaces:
        verbs = sorted({k[1] for k in cells if k[0] == surface})
        kinds = sorted(
            {k[2] for k in cells if k[0] == surface}, key=_sort_key(KIND_ORDER)
        )
        out.append(f"## {surface}\n")
        header = "| verb | " + " | ".join(kinds) + " |"
        out.append(header)
        out.append("|" + "---|" * (len(kinds) + 1))
        for verb in verbs:
            row = [verb]
            for kind in kinds:
                recs = cells.get((surface, verb, kind), [])
                if not recs:
                    row.append("")
                    continue
                failed = sum(1 for r in recs if r.get("ok") is False)
                mark = f"{len(recs)}" + (f" ({failed} failed)" if failed else "")
                row.append(mark)
            out.append("| " + " | ".join(row) + " |")
        out.append("")
    return "\n".join(out)


def render_json(cells: dict[tuple[str, str, str], list[dict[str, Any]]]) -> str:
    rows = []
    for (surface, verb, kind), recs in sorted(cells.items()):
        rows.append({
            "surface": surface,
            "verb": verb,
            "kind": kind,
            "count": len(recs),
            "failed": sum(1 for r in recs if r.get("ok") is False),
            "sources": sorted({str(r["_source"]) for r in recs}),
        })
    return json.dumps(rows, indent=2)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--dir", type=Path, default=Path(".e2e"),
                    help="root to scan for *-log.jsonl (default: .e2e)")
    p.add_argument("--json", action="store_true", help="machine-readable rows instead of a table")
    args = p.parse_args(argv)

    if not args.dir.is_dir():
        print(f"{args.dir} does not exist -- run spendy_tests.sh first", file=sys.stderr)
        return 1

    cells = scan(args.dir)
    if not cells:
        print(f"no verb records found under {args.dir} -- was AGENT_BUS_LOG_LEVEL "
              "set to INFO or higher for that run?", file=sys.stderr)
        return 1

    print(render_json(cells) if args.json else render_table(cells))
    return 0


if __name__ == "__main__":
    sys.exit(main())
