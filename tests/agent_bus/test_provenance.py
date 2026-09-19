"""`bridge_started` says what is running. These pin that it is the truth about
the process, on the branch that has a distribution and the one that has none."""

from __future__ import annotations

import json
import logging
import os
import sys
from importlib import metadata

import agent_bus
from agent_bus import log, provenance

_SITE = "/venv/lib/python3.14/site-packages/agent_bus/__init__.py"


class _Dist:
    def __init__(self, direct_url: str | None):
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        return self._direct_url if name == "direct_url.json" else None


def _with_dist(monkeypatch, direct_url):
    monkeypatch.setattr(metadata, "distribution", lambda _name: _Dist(direct_url))


def _without_dist(monkeypatch):
    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "distribution", missing)


def test_it_reports_the_process_it_is_running_in(monkeypatch):
    _with_dist(monkeypatch, None)
    got = provenance.provenance()
    assert got.version == agent_bus.__version__
    assert got.python == ".".join(map(str, sys.version_info[:3]))
    assert got.executable == sys.executable
    assert got.module_path == os.path.dirname(os.path.abspath(agent_bus.__file__))


def test_a_source_tree_with_no_distribution_says_so(monkeypatch):
    _without_dist(monkeypatch)
    got = provenance.provenance()
    assert got.version_source == "source-tree"
    assert got.install == "source-tree"


def test_an_editable_install_says_so(monkeypatch):
    _with_dist(monkeypatch, json.dumps({"url": "file:///x", "dir_info": {"editable": True}}))
    got = provenance.provenance()
    assert got.version_source == "distribution"
    assert got.install == "editable"


def test_a_regular_install_under_site_packages_is_installed(monkeypatch):
    _with_dist(monkeypatch, json.dumps({"url": "file:///x", "dir_info": {}}))
    monkeypatch.setattr(agent_bus, "__file__", _SITE)
    assert provenance.provenance().install == "installed"


def test_a_distribution_whose_code_is_elsewhere_is_a_source_tree(monkeypatch):
    _with_dist(monkeypatch, json.dumps({"url": "file:///x", "dir_info": {}}))
    monkeypatch.setattr(agent_bus, "__file__", "/home/dev/checkout/src/agent_bus/__init__.py")
    got = provenance.provenance()
    assert got.version_source == "distribution"
    assert got.install == "source-tree"


def test_a_garbled_direct_url_is_not_editable(monkeypatch):
    _with_dist(monkeypatch, "{not json")
    monkeypatch.setattr(agent_bus, "__file__", _SITE)
    assert provenance.provenance().install == "installed"


def test_the_logger_reports_where_it_really_writes_and_at_what_level(tmp_path, monkeypatch):
    dest = tmp_path / "x.jsonl"
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(dest))
    monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "warning")
    log.configure(force=True)
    try:
        assert log.destination() == str(dest)
        assert log.level_name() == "WARNING"
        monkeypatch.setenv("AGENT_BUS_LOG_LEVEL", "off")
        log.configure(force=True)
        assert log.level_name() == "OFF"
    finally:
        for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
            h.close()
            logging.getLogger(log.LOGGER_NAME).removeHandler(h)


def test_the_logger_says_stderr_when_it_could_not_open_a_file(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("AGENT_BUS_LOG_FILE", str(blocker / "under" / "x.jsonl"))
    log.configure(force=True)
    try:
        assert log.destination() == "stderr"
    finally:
        for h in list(logging.getLogger(log.LOGGER_NAME).handlers):
            logging.getLogger(log.LOGGER_NAME).removeHandler(h)
