# Releasing

Two tag namespaces, and they are **independent on purpose**.

| tag | trigger | ships |
|---|---|---|
| `v*` | `cloudbuild.yaml` | the package to PyPI — `agent-bus` and `agent-bridge` |
| `cloud-v*` | `cloudbuild.deploy.yaml` | the server image, deployed to **staging** |

Coupling them would mean a docs-only release redeploying an internet-facing
OAuth server, and a server fix waiting on a package it did not touch.

Both triggers run `ci-build.sh` first — the same gate every pull request runs.
A tag that fails it never ships.

**`cloud-v*` does not touch production.** It updates `agent-bus-staging`.
Production's image lives in `infra/cloud/terraform.tfvars` and is applied by
hand, because a promotion should be a decision — see `infra/cloud/README.md`.

## Before

```sh
scripts/release_preflight.py v0.4.0
```

It answers the question nobody remembers to ask: **does this tag break
something already installed?**

```
v0.4.0  (pypi, since v0.3.0)

  9 commit(s) touch src/, pyproject.toml
    ...

  BREAKING (5):
    - agent-bridge: now requires a verb; the bare-flag invocation is gone
    - MCP get_inbox: field `name` removed
    - the launchd service invocation changed -- reinstall it
      (agent-bridge --kind X --name Y -> agent-bridge start --kind X --name Y)

  -> a MINOR bump, not a patch. Something installed stops working.
```

Only removals and new obligations count. Adding a tool, a flag or an optional
field never breaks a caller; removing one does, and so does making an existing
field required.

It refuses on a dirty tree, a branch that is not `main`, or a `main` that
differs from `origin/main`.

**It reads each surface using that revision's own code**, by extracting a
`git archive` of the old tag and running the probe inside it. A checker that
imported today's modules could only ever describe today.

The namespaces are scoped: a `cloud-v*` tag is judged on `cloud/contract.py`
alone, so it is never told it is breaking because the *package's* MCP schema
moved.

## Cutting it

From the **main checkout**, not a worktree.

```sh
git tag -a v0.4.0 -m "..." && git push origin v0.4.0
```

One command, taken deliberately. Automating it would put machinery between a
person and a decision — the preflight exists to inform that decision, not to
make it.

## After

```sh
scripts/release_postflight.py v0.4.0
scripts/release_postflight.py cloud-v0.0.4 --url https://<hostname>
```

**A green build is not the same claim as a shipped artefact.** `terraform
apply` reports success whether or not the new revision took traffic, and
`/health` answered 200 throughout a period when the deployment was five merges
behind — the old revision was still healthy.

So the postflight asks the artefact: PyPI has the version and serves it as
latest; the running server reports the tag that was cut.

That second check only became possible with #211, which bakes the tag into the
image as `AGENT_BUS_CLOUD_VERSION`. Before it, `/health` said `0+unknown` on
every deploy this service ever had. An image built without
`--build-arg VERSION=` still does, and the postflight says so rather than
guessing.

## When the CLI surface moves

The launchd service pins its argv in
`packaging/launchd/ai.framesift.agent-bridge.plist.template`. If the preflight
reports the invocation changed, the running service keeps working — it is
already running — but the moment it restarts under the new binary it exits, and
`KeepAlive` retries that every 60 seconds.

Reinstall deliberately rather than discovering it that way:

```sh
packaging/launchd/bridge-service.sh install desktop:claude
```

`install` boots out the existing job before bootstrapping, so it is also
`reinstall`, and it pre-flights the address before touching launchctl.

## Promoting to production

Separate from any tag, and by hand.

```hcl
# infra/cloud/terraform.tfvars
image = "us-central1-docker.pkg.dev/agent-bus-cloud/cloud/server:cloud-v0.0.4"
```

```sh
terraform plan -out=promote.tfplan   # read it
terraform apply promote.tfplan
scripts/release_postflight.py cloud-v0.0.4 --url https://<hostname>
```

**Read the plan.** On 2026-09-01 an apply without `-var-file` was one
confirmation away from emptying the production OAuth redirect allowlist —
`allowlist` has `default = {}`, and a default is not a prompt. The plan should
show the image line and nothing else surprising.

Rollback is the previous tag in the same variable, re-applied. Old images stay
in the registry.
