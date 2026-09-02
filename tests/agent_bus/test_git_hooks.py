"""The hooks in `.githooks/`, run as git actually runs them.

Driven through real `git push` and `git pull` against a throwaway origin, not
by calling the scripts directly: half of what these do is wiring -- reading
stdin in the shape git supplies, resolving `origin/main`, blocking by exit
code -- and a test that invokes the script by hand checks none of it.

`pre-push` blocks on *overlap*, never on main merely advancing. A hook that
fires on every push is one people pass `--no-verify` to, and then it protects
nothing at all.
"""

from __future__ import annotations

import os
import subprocess

import pytest

HOOKS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".githooks")


def _run(cwd, *args, check=True, stdin=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       input=stdin, timeout=60)
    if check and p.returncode != 0:
        raise AssertionError(f"{args} -> {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def _git(cwd, *args, **kw):
    return _run(cwd, "git", *args, **kw)


@pytest.fixture
def repos(tmp_path):
    """A bare origin with one commit on main, and a clone wired to the hooks.

    `core.hooksPath` is set the way `.githooks/install` sets it, so the test
    exercises the same wiring a developer gets.
    """
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _run(tmp_path, "git", "init", "--quiet", "--bare", "-b", "main", str(origin))
    _run(tmp_path, "git", "clone", "--quiet", str(origin), str(work))
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                 ("commit.gpgsign", "false"), ("core.hooksPath", HOOKS)):
        _git(work, "config", k, v)
    (work / "a.txt").write_text("a\n")
    (work / "b.txt").write_text("b\n")
    _git(work, "add", "a.txt", "b.txt")
    _git(work, "commit", "--quiet", "-m", "base")
    _git(work, "push", "--quiet", "origin", "main")
    return origin, work


def _advance_main(origin, tmp_path, filename, text):
    """Move origin/main, from a second clone -- the way a colleague would."""
    other = tmp_path / f"other-{filename}"
    _run(tmp_path, "git", "clone", "--quiet", str(origin), str(other))
    for k, v in (("user.email", "o@example.invalid"), ("user.name", "o")):
        _git(other, "config", k, v)
    (other / filename).write_text(text)
    _git(other, "add", filename)
    _git(other, "commit", "--quiet", "-m", f"main changes {filename}")
    _git(other, "push", "--quiet", "origin", "main")


def test_a_push_is_refused_when_main_changed_the_same_file(repos, tmp_path):
    """The case the hook exists for: two changes to one file that nothing has
    ever seen together. `cloud/app.py` went 1039 -> 91 lines under a worktree
    whose holder had no reason to look."""
    origin, work = repos
    _git(work, "checkout", "--quiet", "-b", "feature")
    (work / "a.txt").write_text("mine\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "--quiet", "-m", "I change a.txt")
    _advance_main(origin, tmp_path, "a.txt", "theirs\n")

    p = _git(work, "push", "origin", "feature", check=False)
    assert p.returncode != 0, f"the push was allowed:\n{p.stdout}\n{p.stderr}"
    assert "REFUSED" in p.stderr and "a.txt" in p.stderr, p.stderr
    assert "--force-with-lease" in p.stderr, "it must say how to recover"

    remote = _git(work, "ls-remote", "--heads", "origin", "feature")
    assert not remote.stdout.strip(), "the branch reached origin despite the refusal"


def test_a_push_is_allowed_when_main_advanced_elsewhere(repos, tmp_path):
    """The common case, and the one that decides whether the hook survives.
    Blocking here would fire on nearly every push in an active repo, and a
    hook that cries wolf gets `--no-verify` -- after which the case above is
    unprotected too."""
    origin, work = repos
    _git(work, "checkout", "--quiet", "-b", "feature")
    (work / "a.txt").write_text("mine\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "--quiet", "-m", "I change a.txt")
    _advance_main(origin, tmp_path, "b.txt", "theirs\n")

    p = _git(work, "push", "origin", "feature", check=False)
    assert p.returncode == 0, f"a non-overlapping push was blocked:\n{p.stderr}"
    assert "main advanced" in p.stderr, "it should still say the base moved"
    assert "REFUSED" not in p.stderr


def test_a_push_from_a_current_branch_says_nothing(repos):
    """No news is the right output. A hook that comments on every push trains
    people to skip the one that matters."""
    _origin, work = repos
    _git(work, "checkout", "--quiet", "-b", "feature")
    (work / "c.txt").write_text("c\n")
    _git(work, "add", "c.txt")
    _git(work, "commit", "--quiet", "-m", "new file")

    p = _git(work, "push", "origin", "feature", check=False)
    assert p.returncode == 0, p.stderr
    assert "main advanced" not in p.stderr and "REFUSED" not in p.stderr


def test_no_verify_still_gets_through(repos, tmp_path):
    """Deliberate, and worth a test: the hook is a seatbelt, not a lock. If it
    could not be overridden it would be removed instead."""
    origin, work = repos
    _git(work, "checkout", "--quiet", "-b", "feature")
    (work / "a.txt").write_text("mine\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "--quiet", "-m", "I change a.txt")
    _advance_main(origin, tmp_path, "a.txt", "theirs\n")

    _git(work, "push", "--no-verify", "--quiet", "origin", "feature")
    assert _git(work, "ls-remote", "--heads", "origin", "feature").stdout.strip()


def test_a_pull_flags_a_file_both_sides_changed(repos, tmp_path):
    """A clean merge is not evidence the two changes are compatible.

    Both sides edited `a.txt`, in different hunks, so git merged it without a
    murmur -- which is exactly the case worth naming, and the only one
    reachable: git *refuses* a merge that would overwrite an uncommitted edit,
    so a hook keyed on dirty files would never run at all. The first version of
    this hook was keyed that way, and this test passed vacuously behind a
    `returncode == 0` guard.
    """
    origin, work = repos
    lines = [f"line {i}\n" for i in range(1, 11)]
    (work / "a.txt").write_text("".join(lines))
    _git(work, "add", "a.txt")
    _git(work, "commit", "--quiet", "-m", "ten lines")
    _git(work, "push", "--quiet", "origin", "main")

    other = tmp_path / "other"
    _run(tmp_path, "git", "clone", "--quiet", str(origin), str(other))
    for k, v in (("user.email", "o@example.invalid"), ("user.name", "o")):
        _git(other, "config", k, v)
    theirs = lines.copy()
    theirs[9] = "line 10, theirs\n"
    (other / "a.txt").write_text("".join(theirs))
    _git(other, "add", "a.txt")
    _git(other, "commit", "--quiet", "-m", "upstream edits the last line")
    _git(other, "push", "--quiet", "origin", "main")

    # My own commit: the same file at the other end of it, and one file only
    # I touched. The second is what makes this an intersection test -- without
    # it, comparing my side against itself passes just as well.
    mine = lines.copy()
    mine[0] = "line 1, mine\n"
    (work / "a.txt").write_text("".join(mine))
    (work / "only-mine.txt").write_text("mine alone\n")
    _git(work, "add", "a.txt", "only-mine.txt")
    _git(work, "commit", "--quiet", "-m", "I edit the first line")

    p = _git(work, "pull", "--no-rebase", "--no-edit", "--quiet", "origin", "main")
    out = p.stdout + p.stderr
    assert "pulled 1 commit" in out, out
    assert "both sides changed these" in out, out
    assert "a.txt" in out, out
    assert "only-mine.txt" not in out, (
        f"a file only this side changed was reported as a collision:\n{out}")


def test_the_pull_summary_lists_the_incoming_commits(repos, tmp_path):
    """Without the collision: the summary still has to say what arrived, or
    the habit it exists to create never forms."""
    origin, work = repos
    _advance_main(origin, tmp_path, "b.txt", "from upstream\n")

    p = _git(work, "pull", "--no-rebase", "--quiet", "origin", "main")
    out = p.stdout + p.stderr
    assert "pulled 1 commit" in out, out
    assert "main changes b.txt" in out, out
    assert "b.txt" in out
