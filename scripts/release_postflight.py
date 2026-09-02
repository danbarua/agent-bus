#!/usr/bin/env python3
"""Did the tag actually ship, and is the thing running the thing you cut?

A build that goes green is not the same claim. `terraform apply` reports success
whether or not the new revision took traffic, and `/health` answered 200
throughout a period when the deployment was five merges behind -- because the
*old* revision was still healthy.

So this asks the artefact, never the pipeline.

    scripts/release_postflight.py v0.4.0
    scripts/release_postflight.py cloud-v0.0.4 --url https://bus.example.com

Stdlib only, and no gcloud: what it checks is what any caller could check, which
is the point. The cloud half is only answerable at all because #211 bakes the
tag into the image -- before that `/health` reported `0+unknown` on every deploy
this service ever had.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PYPI = "https://pypi.org/pypi/agent-bus-team/json"


def _get(url: str, timeout: float = 15.0) -> tuple[int, str]:
    # Checked rather than suppressed: `--url` is genuinely an argument, so a
    # `noqa` saying "not input" would be false. `file:` would make this read a
    # local path and report it as a deployment's answer.
    if not url.startswith(("http://", "https://")):
        return 0, f"refusing a non-http URL: {url!r}"
    req = urllib.request.Request(  # noqa: S310 -- scheme checked above
        url, headers={"User-Agent": "agent-bus-release-postflight"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- same
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return 0, str(e)


def _check_pypi(tag: str) -> list[tuple[bool, str]]:
    """The version is on PyPI, and it is the one the tag names."""
    want = tag.lstrip("v")
    status, body = _get(PYPI)
    if status != 200:
        return [(False, f"PyPI did not answer ({status or 'unreachable'})")]
    try:
        data = json.loads(body)
    except ValueError:
        return [(False, "PyPI answered something that is not JSON")]

    releases = data.get("releases") or {}
    latest = (data.get("info") or {}).get("version")
    out = [(want in releases, f"{want} is published" if want in releases
            else f"{want} is NOT on PyPI (latest is {latest})")]
    # Published-but-not-latest is a real state and worth naming: a yanked or
    # out-of-order upload leaves the version present and `pip install` reaching
    # for something else.
    if want in releases and latest != want:
        out.append((False, (f"published, but PyPI's latest is {latest} -- "
                            "a plain install will not get this one")))
    return out


def _check_cloud(tag: str, url: str) -> list[tuple[bool, str]]:
    """The running server reports the tag that was cut.

    Not "the deploy succeeded" -- what is *serving*. A revision that fails its
    startup probe leaves the previous one taking traffic, and every pipeline
    step still says success.
    """
    status, body = _get(f"{url.rstrip('/')}/health")
    if status != 200:
        return [(False, f"{url}/health did not answer 200 ({status or 'unreachable'})")]
    try:
        health = json.loads(body)
    except ValueError:
        return [(False, "/health answered something that is not JSON")]

    running = health.get("version")
    if running == tag:
        return [(True, f"serving {running}")]
    if running in (None, "", "0+unknown"):
        return [(False, ("/health reports no version. Either this image predates "
                         "#211's `ARG VERSION`, or the build did not pass "
                         "`--build-arg VERSION=` -- so what is running cannot be "
                         "identified from outside at all"))]
    return [(False, (f"serving {running}, not {tag} -- the new revision did not "
                     "take traffic, or production was never promoted "
                     "(a `cloud-v*` tag deploys STAGING only)"))]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="release_postflight.py",
        description="Did the tag ship, and is it what is running?")
    p.add_argument("tag", help="the tag that was cut")
    p.add_argument("--url", default=None,
                   help="the deployment to ask, for a cloud tag. Its hostname is "
                        "deliberately not in this repository -- pass it, or set "
                        "AGENT_BUS_CLOUD_ISSUER")
    args = p.parse_args(argv)

    if args.tag.startswith("cloud-v"):
        import os

        url = args.url or os.environ.get("AGENT_BUS_CLOUD_ISSUER")
        if not url:
            print("a cloud tag needs --url or AGENT_BUS_CLOUD_ISSUER: the "
                  "hostname is kept out of the repository on purpose")
            return 2
        results = _check_cloud(args.tag, url)
    else:
        results = _check_pypi(args.tag)

    print(f"{args.tag}\n")
    for ok, line in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
