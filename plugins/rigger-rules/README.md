# rigger-rules

Engineering rules for Claude Code, injected into every session and subagent: scope of changes,
tests that can fail, recurring defect classes, plans, git through worktrees and pull requests,
verifying in the real environment, and which skill to load before which step. It depends on
`rigger-core`, whose skills and agents the rules name; the `superpowers:` rows of `skills.md` apply
when the superpowers plugin is installed.

The rules speak to the model: "me" and "my OK" mean you, the person running the session.

## What it prints

rigger-core's assembler prints this plugin's rules together with the other Rigger plugins' rules:
`Plugin root: <root>`, then the sections, on `SessionStart` (`startup|clear|compact`) and as JSON
`additionalContext` on `SubagentStart`; then your `rules.d` files (below). `rigger-module.json`
lists the rules files and their audience; rigger-core's README has the handlers. This plugin's own
hooks:

| Event | Matcher | What it prints |
|---|---|---|
| `SessionStart` | `startup\|clear\|compact` | `hooks/worktree_root.sh`: `Worktree root: <path>` when `HARNESS_WORKTREE_ROOT` is set, nothing otherwise |
| `SessionStart` | `startup\|clear` | `hooks/core_check.sh rigger-rules`: one line when the installed rigger-core is too old to print the rules (rigger-core's README) |

Subagents get nothing for `Explore`, `Plan`, `rigger-core:implementer` and `rigger-core:mapper`,
which run without `CLAUDE.md`. `resume` prints nothing because the resumed transcript already holds
the rules; `compact` prints them again because compaction drops them.

`verify.md` and `plans.md` hold the lines subagents get; `verify-main.md`, `plans-main.md` and
`environment.md` hold the lines only the main session gets. Keep a rule in one file: each section
stays under 9,000 bytes, counted with LF, because the hook text cap is 10,000 characters.

Set `HARNESS_WORKTREE_ROOT` to the folder where new git worktrees go; `git.md` sends them there.

## Your own rules: `rules.d`

Put Markdown files in `<config dir>/rigger/rules.d/`, where `<config dir>` is `CLAUDE_CONFIG_DIR`
if set, else `~/.claude`. They are read while rigger-rules is enabled, and printed after the rules
of every Rigger plugin:

- every `*.md` file, sorted by name in byte order (`10-x.md` before `9-x.md`, `Z.md` before `a.md`);
- a section whose id equals a Rigger section's id replaces that section in place, and the session
  gets the line `rigger: rules.d/<file> replaces rule <id>` among the notices (past 2,000 bytes of notices the rest are folded into one `rigger: N more notices not shown` line); `/rigger-core:doctor` lists each as a
  `rule-override` row. The id is the file stem, a dot and the heading slug: `rules.d/tests.md`
  with `## Tests` replaces `tests.tests`. The slug is the heading cut at the first ` (`, `:`, ` — `
  or ` - `, lower-cased, accents removed, every run of other characters turned into `-`;
- a replacing section with only its heading removes the Rigger section;
- a replacement reaches only the audience of the section it replaces (main session, subagents or
  both); a `*-main.md` file stays main-only;
- a name ending in `-main.md` goes to the main session only, every other file to subagents too;
- no folder, or no `*.md` in it, prints nothing;
- CR characters and other control characters except tab are dropped, so CRLF files are fine;
- a file that cannot be read prints `rules_extra: cannot read <name>` in its place;
- each hook prints at most 9,000 bytes of rules: the file that would go over the cap and every file
  after it are left out, named in one line `rules_extra: left out over the hook cap: <names>`.

Write files as UTF-8. `rigger-lessons` writes the lessons you promote to global rules into this
folder as `lessons.md`. Start a new session (or `/clear`) after changing a file.

## HARNESS_RULES_DROP

Skips rules, for a variant run that measures what a rule is worth. rigger-core's assembler reads it
from its environment, in `SessionStart` and `SubagentStart`, for the rules of every Rigger plugin;
it does not apply to `rules.d`, including a `rules.d` section that replaced a Rigger one.

- The value is a list of entries separated by `;` or a newline.
- Each entry is trimmed (spaces, tabs, CR), then matched exactly and case-sensitively.
- An entry is a rules-file basename, a whole `## ` heading line as printed, or a section id:

| Entry | Effect |
|---|---|
| `tests.md` | skips that file |
| `## Tests` | skips that heading and its lines, up to the next `## ` heading or the end of the file |
| `tests.tests` | skips the section with that id (`rules.d` above has the id form) |
| `Tests`, `tests`, `## tests` | no match, nothing is skipped |

A plugin with no section left prints no `Plugin root:` line.

```
HARNESS_RULES_DROP='tests.md;## Plans: main session'
```
