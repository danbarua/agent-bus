"""Plugin manifests, skills, and hooks are present and well-formed."""
import json
import os
import re
import shutil
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _frontmatter(path: str) -> dict[str, str]:
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"missing frontmatter: {path}"
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


def test_plugin_manifests_match():
    root = json.loads(open(os.path.join(REPO, "plugin.json"), encoding="utf-8").read())
    claude = json.loads(
        open(os.path.join(REPO, ".claude-plugin", "plugin.json"), encoding="utf-8").read()
    )
    assert root["name"] == "agent-bus"
    assert claude["name"] == "agent-bus"
    assert root["description"] == claude["description"]


def test_skills_have_name_and_description():
    expected = {
        "agent-bus",
        "agent-bus-inbox",
        "agent-bus-send",
        "agent-bus-list",
    }
    found = set()
    skills = os.path.join(REPO, "skills")
    for name in os.listdir(skills):
        skill_md = os.path.join(skills, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        fm = _frontmatter(skill_md)
        assert fm.get("name") == name
        assert fm.get("description")
        assert fm["description"].lower().startswith("use when")
        found.add(name)
    assert found == expected


def test_hooks_and_wrapper_are_executable():
    hooks = json.loads(open(os.path.join(REPO, "hooks", "hooks.json"), encoding="utf-8").read())
    events = hooks["hooks"]
    assert "SessionStart" in events
    assert "SessionEnd" in events
    for rel in ("scripts/agent-bus", "hooks/session-start", "hooks/session-end"):
        path = os.path.join(REPO, rel)
        assert os.access(path, os.X_OK), rel


def test_grok_plugin_validate():
    if not shutil.which("grok"):
        pytest.skip("grok CLI not available")
    grok = subprocess.run(["grok", "plugin", "validate", REPO], capture_output=True, text=True)
    assert grok.returncode == 0, grok.stdout + grok.stderr
