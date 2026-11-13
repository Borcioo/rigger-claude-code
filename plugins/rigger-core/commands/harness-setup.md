---
description: Run the doctor checks, add Compact Instructions to the user CLAUDE.md and, if you opt in, set autoCompactWindow and the worktree root
---

Run the doctor, then set up the three things a plugin cannot ship. Install or write nothing until the
user has seen the change and agreed. Running this again on a machine that is already set up changes
nothing unless the user picks something new.

The config folder is `$CLAUDE_CONFIG_DIR`, else `~/.claude`.

## 0. Doctor

Read `${CLAUDE_PLUGIN_ROOT}/commands/doctor.md` and do all of it. Its commands name this plugin's
folder as `${CLAUDE_PLUGIN_ROOT}`: run them with this command's own plugin folder in its place. A
row still MISSING after its fix: say
which, and go on only if the user says so; the steps below work without it, the hooks do not.

## 1. Compact Instructions

The target is `CLAUDE.md` in the config folder. A missing file counts as empty. The section is
exactly:

```markdown
## Compact Instructions

Keep: the state file path, its Next step and Keep verbatim sections, and every open decision;
lessons captured this session; running agents (task, worktree, port); gate commands and modified
files.
```

- The file has a `## Compact Instructions` heading with this exact text: report "Compact Instructions:
  already set" and change nothing.
- The file has that heading with other text: show a diff of the section against the text above and
  ask whether to replace it. Keep the user's text unless they say to replace it.
- The file has no such heading: show a diff that appends the section at the end, and ask.

## 2. autoCompactWindow (opt-in)

The target is `settings.json` in the config folder. `compact_guard` works without this key; the
default window then decides when compaction fires. Compaction fires about 33k below the value.

Ask once, with AskUserQuestion where it exists, naming the current value or "not set", and write the
line the user picks; the option's text shows it, so picking is the agreement:

| Option | Fires at about | For |
|---|---|---|
| `"autoCompactWindow": 533000` (recommended) | 500k | the harness default: long sessions on a model with a 1M context; each turn costs more |
| `"autoCompactWindow": 283000` | 250k | shorter sessions, cheaper turns |
| Leave it as it is | unchanged | keeps the current value, or the default window when not set |

A typed value must stay below 560000, the hard cap in `compact_guard`: above it `safe_to_compact` no
longer holds compaction back, so say that and ask again. A session with `coordinator: true` in its
state file compacts at its pauses (`safe_to_compact: true`) and is forced at 650000 by the guard,
whatever is chosen here.

Parse the file as JSON before writing, change only this key, keep every other key and its order, and
parse the result again. If the file is not valid JSON, report that and leave it alone.

## 3. Worktree root

The target is the `HARNESS_WORKTREE_ROOT` key in the `env` object of `settings.json` in the config
folder. The rigger-rules SessionStart hook prints it as `Worktree root: <path>`, and the Git rules
put every worktree under it; unset, worktrees go next to the repo.

- The key is set: report "Worktree root: already set (<value>)" and change nothing.
- The key is not set: ask once, with AskUserQuestion where it exists; the option's text shows the
  path, so picking is the agreement:

| Option | Writes |
|---|---|
| `Worktrees` in the user's home folder (`<home>/Worktrees`; on Windows, `%USERPROFILE%\Worktrees`) (recommended) | that folder as an absolute path |
| A folder the user types | that folder as an absolute path |
| Leave it unset | nothing |

Write it the way section 2 writes its key, adding `env` when it is missing, and create the folder
when it does not exist. Then tell the user, when they use the Claude desktop app, to set Settings →
Claude Code → Worktree location to the same folder: the app makes the worktrees of the sessions it
starts there, and Claude cannot change that setting.

## 4. Report

Give one line per target after the doctor's table: unchanged, written, or declined. Tell the user to
restart Claude Code if anything was installed or written.
