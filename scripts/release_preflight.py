#!/usr/bin/env python3
"""What would this tag ship, and does any of it break something installed?

Tagging is not the problem -- `git tag -a v0.4.0 && git push` is one command
taken deliberately, and automating it would put machinery between a person and
a decision. What goes wrong is judgement:

- `0.3.1` was proposed for a release containing two breaking changes (#218's
  CLI invocation, #224's MCP schema). Nothing said so.
- #211's version stamp only takes effect on the *next* cloud tag, so production
  kept reporting `0+unknown` after the merge that added it.

Both are mechanical questions, so this asks them.

**The surface is read out of each revision by its own code.** The extractor
below is passed to `python -c` as *source*, against a checkout of the old tag,
because a checker that imported today's modules could only ever describe today.
That is the whole trick, and it is why this file is not a library.

Two namespaces, deliberately independent: `v*` ships the package to PyPI, and
`cloud-v*` builds the server and deploys it to STAGING. The tag prefix picks
which surface matters.

    scripts/release_preflight.py v0.4.0
    scripts/release_preflight.py cloud-v0.0.4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Paths whose contents are actually shipped, per namespace. A tag that touches
#: nothing here is a docs release, and saying so is worth as much as the rest.
SHIPPED = {
    "pypi": ["src/", "pyproject.toml"],
    "cloud": ["cloud/"],
}

#: Which surfaces a namespace is allowed to report on. The two tags move
#: independently, so a `cloud-v*` tag calling itself breaking because the
#: *package's* MCP schema changed would be reporting someone else's news --
#: and the whole job here is to make the verdict trustworthy.
SURFACES = {
    "pypi": ("cli", "bridge_cli", "mcp_tools", "plist_argv", "entry_points"),
    "cloud": ("cloud_tools",),
}

# --------------------------------------------------------------- the extractor
#
# Runs inside a checkout of whatever revision is being described, with only
# stdlib available -- `agent-bus-team` declares `dependencies = []`, which is
# what makes that safe. Every failure is recorded rather than raised: an old
# revision that cannot answer a question is a fact about that revision, not a
# reason to abandon the comparison.

_EXTRACT = r'''
import json, os, re, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
out = {"cli": {}, "bridge_cli": {}, "mcp_tools": {}, "cloud_tools": {},
       "plist_argv": [], "entry_points": {}, "errors": []}

def _verbs(parser):
    """{verb: sorted flags} from an argparse parser with subcommands."""
    found = {}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for verb, sub in choices.items():
            flags = set()
            for a in sub._actions:
                flags.update(a.option_strings or [a.dest])
            found[verb] = sorted(flags)
    return found

try:
    from agent_bus.cli import build_parser
    out["cli"] = _verbs(build_parser())
except Exception as e:
    out["errors"].append("agent_bus cli: %s" % e)

try:
    from agent_bridge.cli import build_parser as bridge_parser
    out["bridge_cli"] = _verbs(bridge_parser())
except Exception as e:
    # Before #218 the bridge had no subcommands and no build_parser at all.
    out["errors"].append("agent_bridge cli: %s" % e)

try:
    from agent_bus.mcp_server import TOOLS
    out["mcp_tools"] = {
        t["name"]: {
            "properties": sorted((t.get("inputSchema") or {}).get("properties", {})),
            "required": sorted((t.get("inputSchema") or {}).get("required", []) or []),
        }
        for t in TOOLS
    }
except Exception as e:
    out["errors"].append("mcp tools: %s" % e)

try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_contract", os.path.join(os.getcwd(), "cloud", "contract.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out["cloud_tools"] = {
        t["name"]: {
            "properties": sorted((t.get("inputSchema") or {}).get("properties", {})),
            "required": sorted((t.get("inputSchema") or {}).get("required", []) or []),
        }
        for t in mod.TOOLS
    }
except Exception as e:
    out["errors"].append("cloud contract: %s" % e)

try:
    p = os.path.join(os.getcwd(), "packaging", "launchd",
                     "ai.framesift.agent-bridge.plist.template")
    with open(p, encoding="utf-8") as f:
        body = f.read()
    m = re.search(r"<key>ProgramArguments</key>\s*<array>(.*?)</array>", body, re.S)
    if m:
        out["plist_argv"] = re.findall(r"<string>(.*?)</string>", m.group(1))
except Exception as e:
    out["errors"].append("plist: %s" % e)

try:
    with open(os.path.join(os.getcwd(), "pyproject.toml"), encoding="utf-8") as f:
        body = f.read()
    block = re.search(r"\[project\.scripts\](.*?)(\n\[|\Z)", body, re.S)
    if block:
        out["entry_points"] = dict(
            re.findall(r'^\s*([\w-]+)\s*=\s*"([^"]+)"', block.group(1), re.M))
except Exception as e:
    out["errors"].append("entry points: %s" % e)

print(json.dumps(out))
'''


def _run(args: list[str], cwd: str | None = None) -> str:
    r = subprocess.run(args, cwd=cwd or REPO, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}...: {r.stderr.strip()[:300]}")
    return r.stdout


def _surface(ref: str | None) -> dict:
    """The shipped surface at `ref`, or the working tree when None.

    `git archive` rather than a worktree: a worktree is shared state in a repo
    that may have several checkouts, and this only ever needs to read.
    """
    if ref is None:
        raw = subprocess.run([sys.executable, "-c", _EXTRACT], cwd=REPO,
                             capture_output=True, text=True, check=False).stdout
        return json.loads(raw)
    tmp = tempfile.mkdtemp(prefix="preflight-")
    try:
        tar = subprocess.run(["git", "archive", ref], cwd=REPO,
                             capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", tmp], input=tar, check=True)
        raw = subprocess.run([sys.executable, "-c", _EXTRACT], cwd=tmp,
                             capture_output=True, text=True, check=False).stdout
        return json.loads(raw) if raw.strip() else {"errors": ["extractor produced nothing"]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: Which extractor failures matter per namespace, so a cloud tag is not told
#: about a package surface it does not ship.
_ERR_HINTS = {
    "pypi": ("cli", "mcp", "plist", "entry"),
    "cloud": ("cloud contract",),
}


def _namespace(tag: str) -> str:
    return "cloud" if tag.startswith("cloud-v") else "pypi"


def _previous_tag(namespace: str) -> str | None:
    """The newest tag in this namespace. The two move independently."""
    out = _run(["git", "tag", "--sort=-v:refname"]).split()
    tags = [t for t in out if (t.startswith("cloud-v") if namespace == "cloud"
                               else re.fullmatch(r"v\d.*", t))]
    return tags[0] if tags else None


def _breaking(old: dict, new: dict, namespace: str) -> list[str]:
    """Only removals and new obligations. Additions never break a caller.

    Each of these is something an *installed* thing depends on: a plist's argv,
    a connector's cached schema, a script's flags.
    """
    found = []
    allowed = SURFACES[namespace]

    for surface, label in (("cli", "agent-bus"), ("bridge_cli", "agent-bridge")):
        if surface not in allowed:
            continue
        before, after = old.get(surface) or {}, new.get(surface) or {}
        for verb in sorted(set(before) - set(after)):
            found.append(f"{label}: verb `{verb}` removed")
        for verb in sorted(set(before) & set(after)):
            gone = [f for f in before[verb] if f.startswith("-") and f not in after[verb]]
            for flag in gone:
                found.append(f"{label} {verb}: flag `{flag}` removed")
    # A bridge that had no subcommands and now requires one is the #218 shape,
    # and shows up as neither a removed verb nor a removed flag.
    if ("bridge_cli" in allowed
            and not (old.get("bridge_cli") or {}) and (new.get("bridge_cli") or {})):
        found.append("agent-bridge: now requires a verb; the bare-flag "
                     "invocation is gone (an installed plist would exit)")

    for surface, label in (("mcp_tools", "MCP"), ("cloud_tools", "connector")):
        if surface not in allowed:
            continue
        before, after = old.get(surface) or {}, new.get(surface) or {}
        for tool in sorted(set(before) - set(after)):
            found.append(f"{label}: tool `{tool}` removed")
        for tool in sorted(set(before) & set(after)):
            lost = set(before[tool]["properties"]) - set(after[tool]["properties"])
            for prop in sorted(lost):
                found.append(f"{label} {tool}: field `{prop}` removed")
            new_req = set(after[tool]["required"]) - set(before[tool]["required"])
            for prop in sorted(new_req):
                found.append(f"{label} {tool}: `{prop}` is now required")

    if ("plist_argv" in allowed and old.get("plist_argv")
            and old["plist_argv"] != new.get("plist_argv")):
        found.append("the launchd service invocation changed -- reinstall it "
                     f"({' '.join(old['plist_argv'])} -> {' '.join(new.get('plist_argv') or [])})")

    before_eps = old.get("entry_points") or {} if "entry_points" in allowed else {}
    after_eps = new.get("entry_points") or {}
    for name in sorted(set(before_eps) - set(after_eps)):
        found.append(f"entry point `{name}` removed")

    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="release_preflight.py",
        description="What a tag would ship, and what it breaks.")
    p.add_argument("tag", help="the tag about to be cut, e.g. v0.4.0 or cloud-v0.0.4")
    p.add_argument("--since", default=None,
                   help="compare against this tag instead of the newest in the namespace")
    args = p.parse_args(argv)

    namespace = _namespace(args.tag)
    since = args.since or _previous_tag(namespace)
    if since is None:
        print(f"no previous {namespace} tag: nothing to compare against")
        return 0

    print(f"{args.tag}  ({namespace}, since {since})\n")

    # 1. Is the tree in a state worth tagging?
    dirty = _run(["git", "status", "--porcelain"]).strip()
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    problems = []
    if dirty:
        problems.append(f"working tree is not clean ({len(dirty.splitlines())} paths)")
    if branch != "main":
        problems.append(f"on `{branch}`, not main")
    with_origin = _run(["git", "rev-list", "--left-right", "--count",
                        "HEAD...origin/main"]).split()
    if with_origin != ["0", "0"]:
        problems.append(f"HEAD and origin/main differ by {with_origin[0]}/{with_origin[1]} commits")

    # 2. What would it actually ship?
    paths = SHIPPED[namespace]
    commits = _run(["git", "log", "--oneline", f"{since}..HEAD", "--", *paths]).splitlines()
    print(f"  {len(commits)} commit(s) touch {', '.join(paths)}")
    for line in commits[:12]:
        print(f"    {line}")
    if len(commits) > 12:
        print(f"    ... and {len(commits) - 12} more")
    if not commits:
        print("    -- nothing shipped changed. A docs release does not need this tag.")

    # 3. What breaks?
    old, new = _surface(since), _surface(None)
    for where, s in (("previous", old), ("current", new)):
        for err in s.get("errors", []):
            if not any(k in err for k in _ERR_HINTS[namespace]):
                continue
            # Expected for old revisions that predate a surface -- reported, not
            # hidden, because "could not read it" and "it is unchanged" are
            # different answers and only one is safe.
            print(f"  note ({where}): {err}")
    breaks = _breaking(old, new, namespace)
    print()
    if breaks:
        print(f"  BREAKING ({len(breaks)}):")
        for b in breaks:
            print(f"    - {b}")
    else:
        print("  no breaking surface change detected")

    # 4. The number.
    print()
    if breaks:
        print("  -> a MINOR bump, not a patch. Something installed stops working.")
    elif commits:
        print("  -> a patch bump is honest: shipped code changed, nothing removed.")
    else:
        print("  -> nothing to ship in this namespace.")

    if problems:
        print("\n  refusing:")
        for x in problems:
            print(f"    - {x}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
