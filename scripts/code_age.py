#!/usr/bin/env python3
"""How old the code actually is, read from git rather than guessed.

`git blame` already knows, one line at a time. This aggregates it so the
question "which parts of this codebase still date from the first days" has an
answer you can run instead of a feeling.

Why that question is worth asking here: agent-bus was ten days old when this
was written, and half of `src/` was still first-two-days code. That is normal
and not in itself a problem -- code is not wrong for being old. What makes it
worth surveying is the *kind* of defect it produced. #182's bug was an
assumption that was TRUE when written ("an AGENT_BUS_HOME belongs to one
peer" -- on a single-agent dev machine, in v0.1.0, it was) and was silently
falsified by adoption, in a file nobody had reason to revisit. No test failed,
no lint fired, and the comment stating it read as confident because it had
been earned.

So this does not find bugs. It finds *where to look* for assumptions that
have outlived the world they were written in: code that is old, untouched,
and load-bearing.

Usage:
    scripts/code_age.py                      # src/, oldest first
    scripts/code_age.py --path cloud --path src
    scripts/code_age.py --cutoff 2026-08-23  # what counts as "early"
    scripts/code_age.py --json

Deliberately not: churn (how often a file changed -- a different question,
about instability rather than staleness) and authorship (this project has one
human and several agents, so it would report nothing).
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess


def _blame_dates(path: str) -> list[datetime.date]:
    out = subprocess.run(
        ["git", "blame", "--line-porcelain", "--", path],
        capture_output=True, text=True, check=False,
    ).stdout
    return [
        datetime.date.fromtimestamp(int(line.split()[1]))
        for line in out.splitlines()
        if line.startswith("author-time ")
    ]


def _last_touched(path: str) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%ad", "--date=short", "--", path],
        capture_output=True, text=True, check=False,
    ).stdout.strip()


def survey(paths: list[str], cutoff: datetime.date) -> list[dict]:
    files = subprocess.run(
        ["git", "ls-files", *paths], capture_output=True, text=True, check=False
    ).stdout.split()
    rows = []
    for f in files:
        if not f.endswith((".py", ".ts", ".sh")):
            continue
        dates = _blame_dates(f)
        if not dates:
            continue
        early = sum(1 for d in dates if d <= cutoff)
        rows.append({
            "file": f,
            "lines": len(dates),
            "early_lines": early,
            "early_fraction": round(early / len(dates), 4),
            "oldest": min(dates).isoformat(),
            "newest": max(dates).isoformat(),
            "last_touched": _last_touched(f),
        })
    # Oldest and least-revisited first: a file that is entirely original AND
    # has not been touched since is where a stale assumption survives longest.
    rows.sort(key=lambda r: (-r["early_fraction"], r["last_touched"], -r["lines"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", action="append", default=None)
    ap.add_argument("--cutoff", default=None,
                    help="lines authored on or before this date count as early "
                         "(default: the repo's second day)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    paths = args.path or ["src"]
    if args.cutoff:
        cutoff = datetime.date.fromisoformat(args.cutoff)
    else:
        first = subprocess.run(
            ["git", "log", "--reverse", "--format=%ad", "--date=short"],
            capture_output=True, text=True, check=False,
        ).stdout.split()[0]
        cutoff = datetime.date.fromisoformat(first) + datetime.timedelta(days=1)

    rows = survey(paths, cutoff)
    if args.json:
        print(json.dumps({"cutoff": cutoff.isoformat(), "files": rows}, indent=2))
        return 0

    total = sum(r["lines"] for r in rows)
    early = sum(r["early_lines"] for r in rows)
    print(f"early = authored on or before {cutoff}")
    print(f"{'early':>7} {'lines':>13}  {'oldest':10} {'touched':10} file")
    for r in rows[: args.top]:
        print(f"{r['early_fraction']*100:6.1f}% {r['early_lines']:5d}/{r['lines']:<7d} "
              f"{r['oldest']:10} {r['last_touched']:10} {r['file']}")
    if total:
        print(f"\noverall: {early}/{total} lines ({early/total*100:.1f}%) authored by {cutoff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
