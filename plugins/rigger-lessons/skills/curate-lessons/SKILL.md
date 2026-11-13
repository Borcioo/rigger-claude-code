---
name: curate-lessons
description: Use when the lessons SessionStart line reports waiting proposals, or the user asks to curate lessons at the end of a work phase. Reviews the proposal queue of the optional background batch, or else a slice of the inbox, with one subagent, routes accepted rules to where their scope says, applies them after the user's OK and clears the applied entries.
---

# Curate lessons

## Step 0: guard

Run only in a session the user drives. If another session started this one (a session an agent
opened, a subagent, a headless run), stop here: a session started by another session
never proposes or runs curation. Without the user's OK to curate, propose `/rigger-lessons:curate-lessons`
in one line and stop. When background batches are on (`RIGGER_LESSONS_BATCH=1`), a batch has already merged repeats and archived covered entries; the edits it proposes wait in `lessons-proposals.md` next to the inbox.

## Step 1: review

The inbox is the path the lessons SessionStart line names (`$HARNESS_LESSONS_INBOX`, else
`lessons-inbox.md` in `$CLAUDE_CONFIG_DIR`, else in `~/.claude`). Every line starting
`- YYYY-MM-DD` is an entry, also outside `## Entries`.

When `lessons-proposals.md` next to the inbox holds proposals, the subagent reviews those instead of a slice: each block names its entry, target, heading, old and new text and the batch's reason. It re-reads each target (the old text must still occur exactly once) and answers the questions below for each.

Otherwise one run reviews one slice, never the whole inbox. Print it, and do not sort by hand:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/inbox_lint.py" --slice <inbox>
```

Dispatch one subagent on the strongest model available. It edits no rules, skills or inbox lines. Its brief: the inbox
path, the printed slice, the routing table and rules below, and for each entry of the slice:

- General? Would it recur outside the one case?
- Already covered? Where: file and heading.
- Precise and short?
- The proposal: the exact edit (target file, heading, old text and new text), a merge with another
  entry, or a deletion.
- Past its `review:` date? Keep, sharpen or delete it, with a reason.
- Fields valid? Run `python "${CLAUDE_PLUGIN_ROOT}/tools/inbox_lint.py" <inbox>` and report what it names on the slice's lines (use `python3` where `python` is missing); the main session fixes those fields in step 3.

## Step 2: routing and rules

| scope | target |
|---|---|
| `global` | `<config dir>/rigger/rules.d/lessons.md` (`$CLAUDE_CONFIG_DIR`, else `~/.claude`), created if missing, one short bullet under a heading for its topic; the rigger-rules loader prints it at the next session start. Edit it directly: no plugin cache edit and no update step. Optional: draft the issue body into a local file for `Borcioo/rigger-claude-code` when the rule should change the plugins themselves, and file it only after the user's OK |
| `skill:<name>` | that skill's source; for a skill from an installed plugin, draft the issue body into a local file for the plugin's source repo and file it only after the user's OK, never an edit in the plugin cache under the config dir |
| `project:<name>` | that project's `CLAUDE.md`, section "Pitfalls", created if missing; added after the user's OK |

- **Second occurrence** (the class is already in the inbox, a rule or the skill): the written rule
  was not enough. Propose a process guard (shared helper, lint, test, template, hook) with its
  measured cost: time and spawned processes on a matching and a non-matching call, tokens, and for
  a detector its catch and false-positive rate on real data. Prefer zero-cost fixes.
- **A rule change needs evidence.** An entry with `signal_type: agent-observation`, or with none, only proposes: it changes no rule until a user correction, a test failure or a review finding backs it, however many entries repeat it. Name that `source_ref` in the proposal. A second occurrence names both sources.
- **A project class becomes a skill or global rule only when a second project has it.**
- Merge duplicates, sharpen vague rules, delete rules that proved wrong; cut before you add.

## Step 3: apply

Show the user the proposals. Apply only after the user's OK, with the Edit tool. Then remove the
applied entries from the inbox wherever they sit, also outside `## Entries`; rejected or deferred
entries stay: add ` · curated: <today>` to each rejected or deferred entry, at the end of its line
(`YYYY-MM-DD`), or the next slice is the same one. Such an entry comes back when nothing else waits.
Remove every reviewed block from `lessons-proposals.md`, applied or rejected. To undo a batch, `python "${CLAUDE_PLUGIN_ROOT}/tools/lessons_batch.py" restore <run id>` (the run id heads its blocks and its archive section).

## Step 4: archive (optional, only when `HARNESS_LESSONS_ARCHIVE` is set)

`HARNESS_LESSONS_ARCHIVE` names a folder of the user's choice for curated case files; this
skill writes to it, `tools/lessons_lint.py` checks it, and the verify rule of rigger-rules searches
it before debugging; nothing reads it at session start. For each applied lesson, write a case file in the archive dir following
`${CLAUDE_PLUGIN_ROOT}/templates/archive/README.md` (a new archive starts with that README and
`${CLAUDE_PLUGIN_ROOT}/templates/archive/classes.txt`). The `rule` of a `global` case is the file it
was routed to, `<config dir>/rigger/rules.d/lessons.md#<heading>`, absolute; the lint refuses an installed copy (`plugins/cache/`,
`plugins/marketplaces/` in the config dir), which the next update replaces. Then run the lint, with `python3` where
`python` is missing; `<archive dir>` is the value of `HARNESS_LESSONS_ARCHIVE`, written out as a
path:

```
python "${CLAUDE_PLUGIN_ROOT}/tools/lessons_lint.py" --archive <archive dir>
```

Test guards (`live:<file>::<test>`) resolve against the archive dir's parent; add one `--repo <dir>`
per repo the tests live in. A `live:<rule id>` guard is confirmed by a rule checker: the checker
that claims the rule id (its `RULES`), whatever the case's tools say, or any loaded checker when the
case's tools name a shell. The rigger-windows plugin ships one. To find it, Read
`plugins/installed_plugins.json` in the config dir (`$CLAUDE_CONFIG_DIR`, else `~/.claude`, written out as a path) and, under the key that starts with
`rigger-windows@`, take the first `installPath` that holds `tools/shell-guard/lessons_guard.py`; add
`--rule-checker "<installPath>/tools/shell-guard/lessons_guard.py"`. When there is no such
`installPath`, run without it and tell the
user in one line that rigger-windows is not installed, so rule guards are not checked: the lint then
reports each `live:<rule id>` guard in a case whose tools name a shell with "none is loaded", and
those lines stay. Fix every other reported line until only those remain, or until it exits 0 (exit 2
means the folder, a `--repo` or a checker is missing, or the archive holds no case file).
