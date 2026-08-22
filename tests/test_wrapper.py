"""Plugin CLI wrapper: run packaged src via PYTHONPATH, else PATH."""
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WRAPPER = os.path.join(REPO, "scripts", "agent-bus")


def test_wrapper_runs_plugin_src(tmp_path):
    home = str(tmp_path / "bus")
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["GROK_PLUGIN_ROOT"] = REPO
    env["PYTHON"] = sys.executable
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
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shutil.copy(WRAPPER, shim_dir / "agent-bus")
    os.chmod(shim_dir / "agent-bus", 0o755)
    env = os.environ.copy()
    env["GROK_PLUGIN_ROOT"] = str(tmp_path / "empty-plugin")
    env["GROK_HOME"] = str(tmp_path / "empty-grok")
    env["PYTHON"] = sys.executable
    env["PATH"] = "/usr/bin:/bin:/opt/homebrew/bin"
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [str(shim_dir / "agent-bus"), "list"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "agent-bus-team" in (r.stderr + r.stdout) or "Install the plugin" in (r.stderr + r.stdout)


def test_wrapper_runs_from_script_dir_without_plugin_root(tmp_path):
    home = str(tmp_path / "bus")
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["PYTHON"] = sys.executable
    env.pop("GROK_PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [WRAPPER, "list", "--json"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert isinstance(json.loads(r.stdout), list)


def test_wrapper_discovers_installed_plugin_via_grok_home(tmp_path):
    """A PATH shim must still run the installed plugin src, not a workspace."""
    home = str(tmp_path / "bus")
    grok_home = tmp_path / "grok"
    installed = grok_home / "installed-plugins" / "abc123"
    (installed / "src").mkdir(parents=True)
    os.symlink(os.path.join(REPO, "src", "agent_bus"), installed / "src" / "agent_bus")
    (installed / "plugin.json").write_text('{"name": "agent-bus", "version": "0.0.0"}\n')
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shutil.copy(WRAPPER, shim_dir / "agent-bus")
    os.chmod(shim_dir / "agent-bus", 0o755)
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["GROK_HOME"] = str(grok_home)
    env["PYTHON"] = sys.executable
    env["PATH"] = "/usr/bin:/bin:/opt/homebrew/bin"
    env.pop("GROK_PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [str(shim_dir / "agent-bus"), "list", "--json"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert isinstance(json.loads(r.stdout), list)


def test_wrapper_follows_symlink_to_plugin_tree(tmp_path):
    home = str(tmp_path / "bus")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(WRAPPER, bindir / "agent-bus")
    env = os.environ.copy()
    env["AGENT_BUS_HOME"] = home
    env["PYTHON"] = sys.executable
    env["GROK_HOME"] = str(tmp_path / "empty-grok")
    env.pop("GROK_PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        [str(bindir / "agent-bus"), "list", "--json"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert isinstance(json.loads(r.stdout), list)
