# rigger-rules

Engineering rules for Claude Code, injected into every session and subagent: scope of changes,
tests that can fail, recurring defect classes, plans, git through worktrees and pull requests,
verifying in the real environment, and which skill to load before which step. It depends on
`rigger-core`, whose skills and agents the rules name; the `superpowers:` rows of `skills.md` apply
when the superpowers plugin is installed.

The rules speak to the model: "me" and "my OK" mean you, the person running the session.

## What it prints

| Event | Matcher | What it prints |
|---|---|---|
| `SessionStart` | `startup\|clear\|compact` | one handler per rules file: `Plugin root: <root>`, then the file |
| `SessionStart` | `startup\|clear\|compact` | `hooks/worktree_root.sh`: `Worktree root: <path>` when `HARNESS_WORKTREE_ROOT` is set, nothing otherwise |
| `SessionStart` | `startup\|clear\|compact` | `rules/rules_extra.sh`: your own rules from `rules.d` (below) |
| `SubagentStart` | none | as JSON `additionalContext`: `00-scope`, `english`, `scope`, `git`, `tests`; `verify`, `plans`, `defects`; `skills`; then your `rules.d` files except `*-main.md` |

`SubagentStart` prints nothing for `Explore`, `Plan`, `rigger-core:implementer` and
`rigger-core:mapper`, which run without `CLAUDE.md`. `resume` is left out of the matcher because the
resumed transcript already holds the rules; `compact` is in it because compaction drops them.

`verify.md` and `plans.md` hold the lines subagents get; `verify-main.md`, `plans-main.md` and
`environment.md` hold the lines only the main session gets. Keep a rule in one file: a test checks
each handler's size (9,000 at most, counted with LF, because the hook text cap is 10,000
characters).

Set `HARNESS_WORKTREE_ROOT` to the folder where new git worktrees go; `git.md` sends them there.

## Your own rules: `rules.d`

Put Markdown files in `<config dir>/rigger/rules.d/`, where `<config dir>` is `CLAUDE_CONFIG_DIR`
if set, else `~/.claude`. They are printed after this plugin's rules:

- every `*.md` file, sorted by name in byte order (`10-x.md` before `9-x.md`, `Z.md` before `a.md`);
- a name ending in `-main.md` goes to the main session only, every other file to subagents too;
- no folder, or no `*.md` in it, prints nothing;
- CR characters and other control characters except tab are dropped, so CRLF files are fine;
- a file that cannot be read prints `rules_extra: cannot read <name>` in its place;
- each hook prints at most 9,000 bytes of rules: the file that would go over the cap and every file
  after it are left out, named in one line `rules_extra: left out over the hook cap: <names>`.

Write files as UTF-8. `rigger-lessons` writes the lessons you promote to global rules into this
folder as `lessons.md`. Start a new session (or `/clear`) after changing a file.

## HARNESS_RULES_DROP

Skips rules, for a variant run that measures what a rule is worth. The hooks read it from their
environment, in `SessionStart` and `SubagentStart`, in every plugin that ships `rules_start.sh`; it
does not apply to `rules.d`.

- The value is a list of entries separated by `;` or a newline.
- Each entry is trimmed (spaces, tabs, CR), then matched exactly and case-sensitively.
- An entry is either a rules-file basename or a whole `## ` heading line:

| Entry | Effect |
|---|---|
| `tests.md` | skips that file; no root line when the handler prints no other file |
| `## Tests` | skips that heading and its lines, up to the next `## ` heading or the end of the file |
| `Tests`, `tests`, `## tests` | no match, nothing is skipped |

```
HARNESS_RULES_DROP='tests.md;## Plans: main session'
```
