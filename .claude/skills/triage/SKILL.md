---
name: triage
description: Rank a new or changed agent-bus issue on the ship agent-bus board. Use when filing an issue, when one becomes blocked or unblocked, or when asked what to work on next.
---

# triage — the board is the only copy

Every issue in this repo carries a **Status** and a **Priority** on project 4,
`ship agent-bus`. Set both when you file it. There is no list in a file, and
there must not be one.

There was: `apply-board-priority.sh`, holding `P0="102 103 104 …"` and applying
it. Deleted 2026-08-31, on the day it had reached the state where it could only
misbehave — three open issues it did not name, so its guard refused to run; and
four closed issues it ranked but had not moved to `DONE`, so satisfying that
guard would have written **Todo** onto issues that were finished. Its comment
said the refusal "is how you find out there is something new to rank." By then
the board was auto-adding items, so the only thing the refusal told anyone was
that the script needed editing.

It was the third copy of the same script, borrowed from exo-ledger and mirrored
in labkit, and all three failed the same way. A queue is state. A file is prose.
Nobody owned keeping them in step.

## The vocabulary

Status says whether it can be picked up. Priority says what to look at first.

| Status | meaning |
| --- | --- |
| Todo | ready: no decision is owed to anyone, someone could start today |
| In progress | someone is on it — including *shipped, open under review* |
| Done | closed |

**Three, deliberately.** There is no `Blocked` and no `Parked`, and adding them
is not free — see the hazard below. A blocked issue is **P2, Todo, and names
what blocks it in the body**; an open question is **P2 and assigned to the
person who owes the decision**. Both of those are facts about the issue, and the
issue is where a reader finds out why. A column would only restate them.

| Priority | meaning |
| --- | --- |
| P0 | clear it next. Something ships wrong, or the bus loses a message. Normally one issue; if two are P0, one of them is not. |
| P1 | real, unblocked, and it slides if nobody ranks it |
| P2 | everything else — blocked work, open questions, and spikes. The honest value for something nobody should pick up today. |

An issue with neither field is **untriaged**, which is the state this exists to
refuse. `agent-bus` is not a search for what the thing is — it is the second
build of something whose shape is known, done properly. The interesting question
is almost always *is this correct*, and a defect that ships green outranks a
feature that does not exist yet.

## Doing it

**The board auto-adds.** A new issue is on it within seconds, with `Status` set
and `Priority` empty. So the job is setting the two fields, not adding the item —
`gh project item-add` is for the rare thing the automation missed.

Resolve field and option ids **by name, every time**. They are not stable, and
the hazard below is why.

```sh
PROJECT=$(gh project view 4 --owner danbarua --format json -q .id)
FIELDS=$(gh project field-list 4 --owner danbarua --format json)
ITEM=$(gh project item-list 4 --owner danbarua --limit 200 --format json \
  -q '.items[] | select(.content.number == <n>) | .id')
field()  { printf '%s' "$FIELDS" | jq -r --arg n "$1" '.fields[] | select(.name == $n) | .id'; }
option() { printf '%s' "$FIELDS" | jq -r --arg n "$1" --arg o "$2" '.fields[] | select(.name == $n) | .options[] | select(.name == $o) | .id'; }

gh project item-edit --id "$ITEM" --project-id "$PROJECT" \
  --field-id "$(field Priority)" --single-select-option-id "$(option Priority P1)"
```

To find what still needs ranking, ask the board rather than a list:

```sh
gh project item-list 4 --owner danbarua --limit 200 --format json \
  | jq -r '.items[] | select(.content.type == "Issue" and (.priority == null))
           | "#\(.content.number) \(.content.title)"'
```

`content.type == "Issue"` on purpose: the board carries pull requests too, and
they take a Status but not a Priority. Without the filter every open PR reports
as untriaged and the query cries wolf until nobody runs it.

**Writes need the `project` scope, which an ordinary `gh` login lacks:**
`gh auth refresh -s project`. Reads work without it, so the board looks
perfectly accessible until the moment you change something.

## Sub-issues are not `gh`'s

`gh issue` has no verb for linking an issue under a parent. Use
`mcp__github__sub_issue_write` from the GitHub MCP server, or the GraphQL
`addSubIssue` mutation via `gh api graphql`.

This matters more here than the count of sub-issues suggests. #58 and #59 both
carry real trees, and a design issue whose children are only mentioned in prose
loses the one thing the tree gives you: closing the last child is visible.

## The hazard worth keeping

**`updateProjectV2Field` replaces the entire option set.** Adding one Status
column clears every item's Status and regenerates every option id — no warning,
no error. On 2026-08-28 that happened on the labkit board and was harmless only
because everything was `Todo` at the time.

With the script gone there is nothing to re-apply the cleared values, so on this
board it is straightforwardly destructive. If a fourth Status is ever genuinely
needed, read every item's current Status first, change the options, then write
them all back — and never hold an option id anywhere but the call that resolved
it.

## What this is not

Not a place to record *why* an issue is ranked where it is. A P0 says in its body
what ships wrong; a P2 that is blocked names the issue blocking it. A rank
without a reason in the issue is a number, and the next person to read it has to
guess.
