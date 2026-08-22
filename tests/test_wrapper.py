"""Plugin CLI wrapper: run packaged src via PYTHONPATH, else PATH."""
import json
import os
import subprocess

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WRAPPER = os.path.join(REPO, "scripts", "agent-bus")


def test_wrapper_runs_plugin_src(tmp_path):
    home = str(tmp_path / "bus")
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["GROK_PLUGIN_ROOT"] = REPO
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [WRAPPER, "list", "--json"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert isinstance(data, list)


def test_wrapper_missing_src_explains_package_name(tmp_path):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    env = os.environ.copy()
    env["GROK_PLUGIN_ROOT"] = str(plugin)
    env["PATH"] = "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [WRAPPER, "list"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "agent-bus-team" in (r.stderr + r.stdout)
