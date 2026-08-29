# Running a bridge as a service

A bridge stands in for one remote peer — `desktop:claude`, `webhook:github` —
and it has to be running for that peer to be reachable at all. Started by hand
it works and then quietly stops: the one that carried real mail for thirteen
hours died overnight and nobody noticed, because a bridge that stops does not
error, it just stops appearing in the roster.

macOS only. `systemd --user` is the same shape and is not written here because
nothing runs on Linux yet.

## What it is for

**Cross-platform group chat for a human and their AI agents.** The best code
reviews come from *outside* a coding harness, and getting an opinion out of a
desktop AI and back into a coding agent otherwise costs two sore thumbs and a
lot of copy-paste:

1. a big context dump goes into a long-running desktop chat — usually "review
   this branch on GitHub";
2. the desktop chat has opinions;
3. the coding agents do a flurry of work;
4. another context dump. Six-plus rounds, until nothing is left to action.

A bridge automates the carrying, and only the carrying — it never reads,
summarises, filters or re-orders anything it moves (`src/agent_bridge/bridge.py`
opens with why that line matters). It is not persistent team messaging, and not
a substitute for tagging `@claude` on a PR. Both of those exist and are a
different thing.

## A desktop peer cannot wake up

Claude Desktop and ChatGPT have no loop. Nothing inserts a message into their
context when a turn ends; **the user types "you've got mail", and that is the
mechanism.** Three consequences, none of them a defect:

- **The failure is one-sided.** Coding peers send and get on with their work, so
  everything looks fine from every machine-local vantage point while mail piles
  up against a peer nobody is prodding. That asymmetry is why this runs as a
  service rather than in a terminal you can see.
- **One conversation per provider.** `desktop:claude` and `desktop:chatgpt` is
  the whole address; there is no `desktop:claude:<conversation>`, because
  nothing in either app lets an outside process enumerate or target one. A
  second chat window is not on the bus — and a desktop peer is worth having
  precisely because one conversation accumulates the whole review.
- **Mail expires after an hour**, in the cloud and locally, by the same rule on
  both sides. A bridge down for the afternoon does not deliver the morning's
  post when it returns: the branch moved, the question was answered, and the
  message arrives looking current. That is correctness, not housekeeping —
  `MESSAGE_TTL_SECONDS` in `store.py` says it at the code.

Traffic between two apps on one laptop therefore goes out to the public
internet and back. Absurd, and unavoidable: it is the only route those apps
expose.

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
security add-generic-password -U \
    -a "$USER" -s agent-bus-cloud-token -w '<the token>'
```

**The value goes on the command line, and that is deliberate.** `-w` with no
value prompts instead — and the prompt reads through a 128-byte buffer, so it
truncates a 254-character token, exits 0, and says nothing. A credential that
looks stored and is not is worse than the few milliseconds the value spends in
`ps` output. Measured, on this machine, the day this was written.

So check the length rather than trusting either form:

```sh
security find-generic-password -s agent-bus-cloud-token -w | tr -d '\n' | wc -c
```

`bridge-service.sh install` refuses to start a service whose stored token is
under 200 characters, for the same reason.

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

```sh
packaging/launchd/bridge-service.sh install desktop:claude
```

That is the whole of it: it renders the template, checks the plist actually
parses, bootstraps the LaunchAgent, and refuses to start if the stored token is
too short to be one. Re-running it is a reinstall.

One address per service. A second connector is `install webhook:github`, never
a flag on the first — an alias is a role with exactly one holder. The address
is always explicit, because a default would eventually restart the wrong one.

To see what it would write before it writes it:

```sh
packaging/launchd/bridge-service.sh render desktop:claude /tmp/out.plist
```

## Operating it

```sh
packaging/launchd/bridge-service.sh status    desktop:claude   # loaded? running? last exit?
packaging/launchd/bridge-service.sh logs      desktop:claude   # follow it
packaging/launchd/bridge-service.sh restart   desktop:claude
packaging/launchd/bridge-service.sh uninstall desktop:claude
```

`status` shows the launchd state and whether the address is on the roster,
which are two different failures: a service that is not running, and a service
that is running and not registered.

Stopping it takes the address off the roster, so senders are told the peer is
unreachable rather than having mail accepted into a queue nobody is draining.
The bridge leaves on SIGTERM — it stops its listener and gives up the name —
which is also why a restart takes a second rather than the two minutes it did
while the listener outlived it.

`KeepAlive` restarts it; `ThrottleInterval` is 60 seconds because this talks to
a billed endpoint and a crash loop against one is a different failure from a
crash.

The plain `launchctl` forms, if you want them:

```sh
launchctl print     "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
launchctl kickstart -k "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
launchctl bootout   "gui/$UID/ai.framesift.agent-bridge.desktop-claude"
```

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
