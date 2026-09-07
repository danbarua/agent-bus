# Releasing

## Tag namespaces

Two tag namespaces trigger releases. Each acts independently.

| tag | trigger | ships |
|---|---|---|
| `v*` | `cloudbuild.yaml` | the package to PyPI: `agent-bus` and `agent-bridge` |
| `cloud-v*` | `cloudbuild.deploy.yaml` | the server image, deployed to **staging** |

Coupling the namespaces would redeploy the internet-facing OAuth server for a docs-only change. It would also delay a server fix until an unrelated package changes.

## Release gate

Both triggers run `ci-build.sh` first. This is the same gate every pull request runs. A tag that fails the gate never ships.

## Staging and production

A `cloud-v*` tag updates `agent-bus-staging`. It does not update production. Production's image is set in `infra/cloud/terraform.tfvars` and applied by hand. A person decides each promotion to production. See `infra/cloud/README.md`.

## Preflight check

Run the preflight script before cutting a tag.

```sh
scripts/release_preflight.py v0.4.0
```

The script checks whether this tag breaks something already installed.

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

Only removals and new obligations count as breaking. Adding a tool, a flag, or an optional field never breaks a caller. Removing one breaks a caller. Making an existing field required breaks a caller too.

The script refuses to run on a dirty tree. It refuses on a branch other than `main`. It refuses when `main` differs from `origin/main`.

The script reads each surface using that revision's own code. It extracts a `git archive` of the old tag and runs the probe inside it. This keeps the check accurate for old tags whose code differs from the current code.

The preflight scopes each tag by its namespace. A `cloud-v*` tag is judged on `cloud/contract.py` only, not the package's MCP schema.

## Cutting a release tag

Cut a release tag from the main checkout, not a worktree.

```sh
git tag -a v0.4.0 -m "..." && git push origin v0.4.0
```

Tag the release with this one command. A person decides when to cut it. The preflight informs that decision.

## Postflight check

Run the postflight script after cutting a tag.

```sh
scripts/release_postflight.py v0.4.0
scripts/release_postflight.py cloud-v0.0.4 --url https://<hostname>
```

A green build does not confirm a shipped artifact. `terraform apply` can report success whether or not the new revision took traffic. `/health` can return 200 while running an old revision that is several merges behind.

The postflight checks the artifact itself. For a `v*` tag, it confirms PyPI has the version and serves it as latest. For a `cloud-v*` tag, it confirms the running server reports the tag that was cut.

The image bakes the release tag into `AGENT_BUS_CLOUD_VERSION`. An image built without `--build-arg VERSION=` reports `0+unknown`. The postflight reports the server's actual version string.

## The launchd service after a CLI change

The launchd service pins its argv in `packaging/launchd/ai.framesift.agent-bridge.plist.template`.

A changed invocation does not stop the running service right away. The service keeps working while it stays up. It exits only when it restarts under the new binary. `KeepAlive` retries the exit every 60 seconds.

Upgrade the tool before reinstalling the service. `bridge-service.sh` renders the plist from this repository. It points the service at whatever `uv tool install` last put on the PATH. These two update on different schedules.

Reinstalling the service without upgrading the binary first starts a job that exits immediately. `KeepAlive` retries every 60 seconds. `install` prints "installed" even while the job fails.

```sh
uv tool upgrade agent-bus-team
agent-bridge --help                                    # expect {start,read}
packaging/launchd/bridge-service.sh install desktop:claude
```

`install` checks the installed binary before starting the service. It reads the verb out of the template. It refuses to proceed if the binary's `--help` output does not name that verb.

`install` reads the help text instead of running the verb. `agent-bridge start --help` exits 0 even on a binary with no subcommands. argparse handles the `--help` flag before it objects to an unknown positional argument.

`install` stops the existing job before starting a new one. This makes `install` also act as `reinstall`. It checks the address before it touches launchctl.

## Promoting to production

Promoting to production is separate from any tag. Do this by hand.

```hcl
# infra/cloud/terraform.tfvars
image = "us-central1-docker.pkg.dev/agent-bus-cloud/cloud/server:cloud-v0.0.4"
```

```sh
terraform plan -out=promote.tfplan   # read it
terraform apply promote.tfplan
scripts/release_postflight.py cloud-v0.0.4 --url https://<hostname>
```

Read the plan before applying it. On 2026-09-01, an apply without `-var-file` came within one confirmation of emptying the production OAuth redirect allowlist. The `allowlist` variable defaults to `{}`. Terraform applies this default silently, without a prompt. Check that the plan shows only the image line.

To roll back, set the same variable to the previous tag and apply again. Old images stay in the registry.
