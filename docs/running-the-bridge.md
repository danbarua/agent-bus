# Running a bridge as a service

A bridge stands in for one remote peer — `desktop:claude`, `webhook:github` —
and it has to be running for that peer to be reachable at all. Started by hand
it works and then quietly stops: the one that carried real mail for thirteen
hours died overnight and nobody noticed, because a bridge that stops does not
error, it just stops appearing in the roster.

macOS only. `systemd --user` is the same shape and is not written here because
nothing runs on Linux yet.

## Install the binary first

```sh
uv tool install agent-bus-team
```

**Never `uv run` from a checkout.** The service would then change behaviour on
every `git checkout` and stop existing while the tree is mid-refactor — and it
would be pinned to one directory on one machine, which is the shape of dev
wiring rather than of something running in the field.

## Put the token in the Keychain

```sh
security add-generic-password -U -a "$USER" -s agent-bus-cloud-token -w
```

**No value after `-w`.** It then prompts, twice, and the token reaches the
Keychain without passing through argv or shell history. `-w "$(cat …)"` is the
obvious one-liner and it puts a live bearer token in `ps` output for as long as
the command runs — a small window, on a machine that also runs coding agents
which can read it.

Confirm it without printing it:

```sh
security find-generic-password -s agent-bus-cloud-token   # metadata, no -w
```

The Keychain wins over `~/.agent-bus/cloud-token`, deliberately: a file left
behind after moving the credential would otherwise keep being used, silently,
for as long as it stayed valid. **Delete the file once the item is added.**

The file remains the fallback, for machines that are not Macs and for a service
that starts before the Keychain unlocks. `agent-bridge` says which one it used
at startup, because "which of these is live" is the first question a 401 raises.

### It will expire, and it will say so first

A bridge token is minted for 30 days. `agent-bridge` reads the `exp` claim it
already parses for the issuer, prints the days remaining at startup, and logs a
warning once a day from a week out. Rotate by re-running the `security` command
above with `-U` and restarting the service.

## Install the service

One address per service. A second connector is a second copy of this file with
a different address, never a flag on the first — an alias is a role with
exactly one holder.

```sh
ADDRESS=desktop:claude
LABEL="${ADDRESS/:/-}"                       # desktop-claude
PLIST="$HOME/Library/LaunchAgents/ai.framesift.agent-bridge.$LABEL.plist"

mkdir -p "$HOME/Library/Logs/agent-bus" "$HOME/Library/LaunchAgents"

sed -e "s|__LABEL__|$LABEL|g" \
    -e "s|__KIND__|${ADDRESS%%:*}|g" \
    -e "s|__NAME__|${ADDRESS##*:}|g" \
    -e "s|__BIN__|$HOME/.local/bin|g" \
    -e "s|__LOGS__|$HOME/Library/Logs/agent-bus|g" \
    packaging/launchd/ai.framesift.agent-bridge.plist.template > "$PLIST"

launchctl bootstrap "gui/$UID" "$PLIST"
```

`sed` rather than a variable inside the plist: launchd expands nothing — not
`~`, not `$HOME` — so every path in it has to be absolute before it is written.

## Operating it

```sh
launchctl kickstart -k "gui/$UID/ai.framesift.agent-bridge.$LABEL"   # restart
launchctl print "gui/$UID/ai.framesift.agent-bridge.$LABEL"          # state, exit codes
tail -f "$HOME/Library/Logs/agent-bus/$LABEL.log"                    # where the mail went
launchctl bootout "gui/$UID/ai.framesift.agent-bridge.$LABEL"        # stop and remove
```

Stopping it takes the address off the roster within a TTL, so senders are told
the peer is unreachable rather than having mail accepted into a queue nobody is
draining.

`KeepAlive` restarts it; `ThrottleInterval` is 60 seconds because this talks to
a billed endpoint and a crash loop against one is a different failure from a
crash.

## How often it polls

Adaptive, and the number worth knowing is not the average:

| | |
|---|---|
| within a minute of any traffic | every 5s |
| otherwise | `--inbound-poll`, default 120s |

**This does not improve first-message latency and cannot.** Nothing local knows
a message exists until it asks, so the first message after a quiet spell waits
up to the idle interval — the busy window buys the reply loop after it, which is
where the waiting is actually felt. A fixed 30s carried ~5,600 requests a day to
deliver a handful of messages; if you would rather pay that for a faster first
hop, `--inbound-poll 30` in the plist restores it.
