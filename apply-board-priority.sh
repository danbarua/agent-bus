#!/usr/bin/env bash
# Sets Priority and Status on every item of the "ship agent-bus" project board.
# Needs the `project` scope:  gh auth refresh -s project
# Idempotent — re-run after triage.
#
# Borrowed from exo-ledger's tools/scripts/apply-board-priority.sh. One
# difference: this board's Status field has only Todo / In progress / Done —
# no Backlog/Ready split, no Blocked/Parked — so there is nothing to infer;
# every item's status is named explicitly below, same as its priority.
#
# Every board item must be named in exactly one of the three priority lists
# and exactly one of the three status lists. That is the point: an issue
# filed after the last triage would otherwise acquire values nobody chose,
# and the board would read as fully triaged when it is not. The script
# refuses instead, and the refusal is how you find out there is something
# new to rank.
set -euo pipefail
export OWNER=danbarua NUM=4

# P0: ships as a defect, or breaks something foundational (identity,
# reachability) that other work depends on.
#
# #147 moved here from P1, and #152 was filed straight in: #147 is the
# completeness guard that would have caught #152 the day it shipped, and #152
# is a live incident (#135) still reproducible for any MCP-native agent.
export P0="102 103 104 118 135 140 147 152"
# P1: real work, unblocked, and it slides if not ranked.
export P1="66 94 105 112 114 122 133"
# P2: everything else — open questions, spikes, done items nobody need look
# at again, and work that is genuinely waiting.
export P2="56 57 58 59 67 68 106 128 134 139 143 145 146 148 149"

export DONE="103 114 118 128 139 145 146"
export IN_PROGRESS="102 104"
# Everything not named above is Todo — this board has no third bucket to
# route the rest into.

PID=$(gh project view "$NUM" --owner "$OWNER" --format json -q .id)

# Field and option ids are resolved by name, every run, and never hardcoded.
# HAZARD: editing a single-select field's option set (adding "Blocked", say)
# goes through updateProjectV2Field, which REPLACES the option set — it clears
# the field's value on every item and regenerates every option id, with no
# warning and no error. Resolving by name survives the id half; the cleared
# values are restored by re-running this script. So: change the options, then
# run this.
FIELDS=$(gh project field-list "$NUM" --owner "$OWNER" --format json | python3 -c "
import sys, json
fields = {f['name']: f for f in json.load(sys.stdin)['fields']}
for name in ('Priority', 'Status'):
    f = fields.get(name)
    if f is None or not f.get('options'):
        sys.exit(f'board has no single-select field named {name!r} — was it renamed?')
    k = name.upper()
    print(f\"{k}_ID={f['id']}\")
    print(f\"export {k}_OPTS='{json.dumps({o['name']: o['id'] for o in f['options']})}'\")")
eval "$FIELDS"

gh project item-list "$NUM" --owner "$OWNER" --limit 100 --format json |
python3 -c "
import sys, json, os

pr = json.loads(os.environ['PRIORITY_OPTS']); st = json.loads(os.environ['STATUS_OPTS'])
rank = {n: p for p in ('P0', 'P1', 'P2') for n in os.environ[p].split()}
done = set(os.environ['DONE'].split())
in_progress = set(os.environ['IN_PROGRESS'].split())

items = {}
for it in json.load(sys.stdin)['items']:
    n = it.get('content', {}).get('number')
    if n is not None:
        items[str(n)] = it['id']

# An empty listing is a failure, not a no-op: a wrong project number, a gh
# that lists nothing, an auth state that reads but does not enumerate.
# Without this the loop below runs zero times and the script exits 0,
# reporting success for work it did not do.
if not items:
    sys.exit(f'no numbered items on {os.environ[\"OWNER\"]} project {os.environ[\"NUM\"]} — '
             'wrong project, or gh cannot enumerate it. Nothing was written.')

# Both directions. Unlisted-on-board is a new issue nobody ranked; listed-off-board
# is a typo or a removed item, which would silently no-op.
problems = []
if untriaged := sorted(items.keys() - rank.keys(), key=int):
    problems.append('  on the board but not in the priority triage above: '
                    + ' '.join('#' + n for n in untriaged)
                    + '\n    A new issue is not a default. Rank it in this script, then re-run.')
if ghosts := sorted(rank.keys() - items.keys(), key=int):
    problems.append('  in the priority triage above but not on the board: '
                    + ' '.join('#' + n for n in ghosts)
                    + '\n    A number that matches nothing writes nothing. Fix or drop it.')
if stray := sorted((done | in_progress) - rank.keys(), key=int):
    problems.append('  given a status but no priority: ' + ' '.join('#' + n for n in stray))
if dupes := sorted(n for n in rank if sum(n in os.environ[p].split() for p in ('P0','P1','P2')) > 1):
    problems.append('  in more than one priority list: ' + ' '.join('#' + n for n in dupes))
if overlap := sorted(done & in_progress, key=int):
    problems.append('  in both Done and In progress: ' + ' '.join('#' + n for n in overlap))
if problems:
    sys.exit('board triage is out of date:\n' + '\n'.join(problems))

# Validate everything before emitting anything: the loop below edits as it
# reads, so a guard that fired mid-stream would leave the board half-written.
for n in sorted(items, key=int):
    p = rank[n]
    s = 'Done' if n in done else ('In progress' if n in in_progress else 'Todo')
    print(items[n], pr[p], st[s], n, p, s)
" |
while read -r item popt sopt n p s; do
  gh project item-edit --id "$item" --project-id "$PID" --field-id "$PRIORITY_ID" --single-select-option-id "$popt" >/dev/null
  gh project item-edit --id "$item" --project-id "$PID" --field-id "$STATUS_ID" --single-select-option-id "$sopt" >/dev/null
  printf "  #%-3s %-3s %s\n" "$n" "$p" "$s"
done
