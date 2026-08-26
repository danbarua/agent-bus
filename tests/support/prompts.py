"""Every prompt the tests send a model, in one directory.

They were f-strings in three files, which made two things impossible: seeing
that three different briefs open with the same sentence and diverge, and
changing what we ask an agent without editing Python.

Substitution is `{{name}}`, not `$name` or `{name}`. Both of those appear in
the prompts for real -- `$PPID` is what makes a shell peer's listener outlive
the command, and `{}` shows up in JSON the model is asked to print.

**Unfilled and unused are both errors.** An f-string cannot have a placeholder
nobody filled; Python raises. Moving to files gives that up unless it is put
back, and the failure it prevents is a model being told to run
`listen --name {{driver}}`, which does not fail, it just registers an agent
called `{{driver}}`.
"""

from __future__ import annotations

import re
from pathlib import Path

DIR = Path(__file__).resolve().parent / "prompts"

_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render(prompt: str, /, **values: object) -> str:
    """The prompt file `prompt`, with `{{...}}` filled in from `values`.

    Positional-only, because two of these prompts substitute `{{name}}` and a
    keyword parameter called `name` collides with it. That failed with
    "multiple values for argument", but only inside the spendy tests, which
    skip everywhere except the container.
    """
    path = DIR / f"{prompt}.md"
    if not path.is_file():
        raise AssertionError(
            f"no prompt {prompt!r} in {DIR}; have: "
            f"{sorted(p.stem for p in DIR.glob('*.md'))}"
        )
    text = path.read_text(encoding="utf-8")
    wanted = set(_TOKEN.findall(text))

    missing = sorted(wanted - set(values))
    if missing:
        raise AssertionError(f"{path.name} needs {missing}, which nothing supplied")
    unused = sorted(set(values) - wanted)
    if unused:
        raise AssertionError(
            f"{path.name} does not use {unused} -- a rename that stopped "
            "substituting would otherwise look like it still worked"
        )
    return _TOKEN.sub(lambda m: str(values[m.group(1)]), text).strip() + "\n"
