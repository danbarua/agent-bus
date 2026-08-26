"""Every prompt renders, in a run that costs nothing.

The prompts are only used by the opt-in e2e tests, which skip everywhere
except the container. So a broken one is invisible until a spendy run --
which is how `render(name=...)` colliding with the loader's own `name`
parameter got as far as it did.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tests", "support"))

from prompts import DIR, render  # noqa: E402

TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
NAMES = sorted(p.stem for p in DIR.glob("*.md"))


def test_there_are_prompts_to_check():
    """A glob that found nothing would pass every test below in silence."""
    assert len(NAMES) >= 5, NAMES


@pytest.mark.parametrize("name", NAMES)
def test_a_prompt_renders_with_every_token_filled(name):
    text = (DIR / f"{name}.md").read_text(encoding="utf-8")
    values = {tok: f"<{tok}>" for tok in set(TOKEN.findall(text))}
    out = render(name, **values)
    assert not TOKEN.search(out), f"{name}: unfilled {TOKEN.findall(out)}"
    for tok in values:
        assert f"<{tok}>" in out, f"{name}: {tok} was not substituted"


def test_a_missing_value_is_an_error_not_a_literal():
    """A model told to run `listen --name {{driver}}` does not fail. It
    registers an agent called `{{driver}}`."""
    with pytest.raises(AssertionError, match="which nothing supplied"):
        render("join_via_shell", cli="x")


def test_an_unused_value_is_an_error():
    """A rename that stopped substituting would otherwise look like it worked."""
    text = (DIR / "join_via_shell.md").read_text(encoding="utf-8")
    values = {tok: "x" for tok in set(TOKEN.findall(text))}
    with pytest.raises(AssertionError, match="does not use"):
        render("join_via_shell", **values, unrelated="y")


def test_an_unknown_prompt_names_the_ones_that_exist():
    with pytest.raises(AssertionError, match="no prompt"):
        render("no_such_prompt")
