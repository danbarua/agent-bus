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
    (other / "only-theirs.txt").write_text("theirs alone\n")
    _git(other, "add", "a.txt", "only-theirs.txt")
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
    # Scoped to the collision section: the diffstat above it legitimately
    # names every file that arrived, `only-theirs.txt` included. One file per
    # side, neither of which may be listed as a collision -- with only one of
    # them, an overlap computed from either side alone passes.
    section = out.split("both sides changed these", 1)[1]
    for only in ("only-mine.txt", "only-theirs.txt"):
        assert only not in section, (
            f"{only} was changed by one side and reported as a collision:\n{out}")


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


def test_a_rebasing_pull_says_what_arrived(repos, tmp_path):
    """`git pull --rebase` with local commits fires `post-rewrite`, and
    nothing else -- `post-merge` never runs.

    Which hook fires is not guessable, it was measured: the same command
    fast-forwards and fires `post-merge` when there is nothing local to
    replay. Both paths exist because the person who does `pull --rebase` hits
    both, depending only on whether they happened to have a commit.
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
    # A file only upstream touched. With `only-mine.txt` below, the assertions
    # pin an intersection from both directions: one file per side, neither of
    # which may be reported. With only one of them, an overlap computed from
    # either side alone passes.
    (other / "only-theirs.txt").write_text("theirs alone\n")
    _git(other, "add", "a.txt", "only-theirs.txt")
    _git(other, "commit", "--quiet", "-m", "upstream edits the last line")
    _git(other, "push", "--quiet", "origin", "main")

    mine = lines.copy()
    mine[0] = "line 1, mine\n"
    (work / "a.txt").write_text("".join(mine))
    (work / "only-mine.txt").write_text("mine alone\n")
    _git(work, "add", "a.txt", "only-mine.txt")
    _git(work, "commit", "--quiet", "-m", "I edit the first line")

    p = _git(work, "pull", "--rebase", "--quiet", "origin", "main")
    out = p.stdout + p.stderr
    assert "rebased onto 1 new commit" in out, out
    assert "upstream edits the last line" in out, out
    assert "both sides changed these" in out, out
    assert "a.txt" in out
    section = out.split("both sides changed these", 1)[1]
    for only in ("only-mine.txt", "only-theirs.txt"):
        assert only not in section, (
            f"{only} was changed by one side and reported as a collision:\n{out}")


def test_a_rebasing_pull_does_not_report_my_own_commits_as_arriving(repos, tmp_path):
    """A replay makes new objects, so `ORIG_HEAD..HEAD` cannot tell my
    replayed commits from upstream's. The hook reads the old/new pairs git
    hands it on stdin and subtracts them; without that it would announce my
    own work as news."""
    origin, work = repos
    _git(work, "checkout", "--quiet", "-b", "feature")
    for n in ("x", "y"):
        (work / f"{n}.txt").write_text(f"{n}\n")
        _git(work, "add", f"{n}.txt")
        _git(work, "commit", "--quiet", "-m", f"mine: {n}")
    _advance_main(origin, tmp_path, "upstream.txt", "theirs\n")

    p = _git(work, "pull", "--rebase", "--quiet", "origin", "main")
    out = p.stdout + p.stderr
    assert "rebased onto 1 new commit" in out, out
    assert "mine: x" not in out and "mine: y" not in out, (
        f"the hook reported my own replayed commits as incoming:\n{out}")


def test_both_pull_hooks_use_the_same_banner():
    """Two scripts saying the same thing in two places is what drifts. The
    wording differs by one word -- merged vs replayed -- and that word is the
    only thing that may differ."""
    banner = "both sides changed these -- "
    for name, verb in (("post-merge", "merged"), ("post-rewrite", "replayed")):
        with open(os.path.join(HOOKS, name), encoding="utf-8") as f:
            body = f.read()
        assert banner + verb + " textually, not checked" in body, name
