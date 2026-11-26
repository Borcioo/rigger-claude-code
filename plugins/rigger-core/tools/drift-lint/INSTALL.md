# drift-lint — run / tune

Reports references in the harness's rule files that point at things that no longer exist: one line
per finding, `file:line  check  reference`. Not a hook; run it by hand after moving or renaming a
tool, skill, agent or folder, and before curating the lessons inbox.

## What it scans

The config folder is `CLAUDE_CONFIG_DIR` when it is set, else `~/.claude`; below it is written
`~/.claude`.

- `~/.claude/CLAUDE.md`;
- every `SKILL.md` under `<repo>\skills` and `<repo>\plugins\*\skills` for each repo given by `--repo`
  (or `HARNESS_DRIFT_REPOS`); a repo with neither is a `read` finding;
- `<repo>\plugins\*\rules\*.md`, also when `--skills-root` is given;
- `rules\*.md` of the plugins in `--plugins-dir` (`~/.claude/plugins`): each `installPath` of
  `installed_plugins.json`, else `cache\<market>\<plugin>\<version>`, and every `<dir>\<plugin>` (the flat
  layout of a marketplace's `plugins` folder); a `templates\…` path there resolves in that plugin. The
  summary line ends with the number of rules files read, repo and `--plugins-dir` together, a file reached
  twice counted once;
- the `SKILL.md` files of the non-junction folders in `~/.claude/skills` (junctions are scanned
  through their repo); `wp-*`, `wordpress-router`, `wpds`, and `blueprint` are upstream
  copies, listed under `## upstream copies` and left out of the exit code;
- `~/.claude/agents/*.md`.

## Checks

| Check | Finds | Exists when |
|---|---|---|
| `path` | `<drive>:\…`, `<drive>:/…`, `~/…`, `~\…`, `$HOME\…`, `$env:USERPROFILE\…` | the path exists (`~`, `$HOME`, `$env:USERPROFILE` = `--home`); with spaces, any prefix up to 8 words; between a pair of quotes or backticks a missing path is reported whole and an ellipsis anywhere in it counts |
| `read` | a skills root, or a `--claude-md`, `--user-skills`, `--agents-dir`, `--plugins-dir` or `--baseline` named on the command line, that is missing (the defaults may be absent: a plugin-only config has none of them), a rule file or baseline that cannot be opened or is not valid UTF-8 (upstream copies are decoded leniently), a folder that cannot be listed, 0 files scanned, `--plugins-dir` given and 0 rules files read through it (those of `--repo` do not count) | never: always a finding |
| `repo-path` | `tools\…`, `templates\…` (either slash) | it exists in a skill repo, the skill's own folder, or the `<repo>\plugins\<plugin>` that holds the file (a sibling plugin does not count) |
| `skill` | `skill:<name>`, ``load/see `<name>` ``, `` `<name>` skill``, a `Skill` table column | a skill folder in the scanned roots, a `~/.claude/skills` entry, or a skill of an installed or `<repo>\plugins` plugin (`plugin:name` too) |
| `agent` | `subagent_type: <name>`, `` `<name>` agent``, ``agent type `<name>` ``, an `Agent` table column | `~/.claude/agents/<name>.md`, a built-in type, or `plugin:agent` of an installed plugin or with `<repo>\plugins\<plugin>\agents\<agent>.md` |
| `command` | first word of each command in a code span of 2+ words and in shell or untagged blocks (split on pipes, `&&`, `;`) | a cmdlet, alias or builtin on the allowlist, or found on PATH (plus Git's `usr\bin`) |

## Run

```
python -I tools/drift-lint/drift_lint.py --repo <skills repo> [--repo <second repo>] [--baseline <file>]
```

The skill repos have no built-in default: pass each with `--repo`, or set `HARNESS_DRIFT_REPOS` to
their roots separated by `;` on Windows or `:` elsewhere (`os.pathsep`); `--repo` wins. With neither,
it prints `drift-lint: no repos; pass --repo or set HARNESS_DRIFT_REPOS` to stderr and exits 2.

Exit 1 when the main section has a finding, else 0. Options (each replaces its default; the
repeatable ones take several values): `--claude-md`, `--skills-root`, `--user-skills`,
`--upstream <pattern>`, `--agents-dir`, `--plugins-dir`, `--repo`, `--home`, `--path <PATH>`,
`--baseline <file>`.
About 1.2 s on the live files.

## Skip rules

Each class has a test that it is skipped and that a planted fake beside it is still reported.

| Class | Skipped |
|---|---|
| placeholder, glob, ellipsis | a path running into `<…>` when the folder before it exists; a path running into `*`, `?` when the glob matches; `…` / `...` when an entry of the folder starts with the text before it, or the ellipsis ends a segment (`name …\log.txt`) and the folder before that segment exists; after arguments, as a path with no ellipsis |
| PATH list | a path ends at `;` |
| shell default | a `~` path directly after `${VAR:-`; a plain `~` path beside it is still checked |
| remote | `~`, `$HOME` paths (the server's home) on a line that mentions `ssh`, `remote` or "on/to/from the server" (a bare `server`, as in "MCP server", does not count), or under a heading (outside code blocks, until the next heading) that mentions `remote` or `server` |
| phrase span | an inline code span of plain words only (`` `unexpected EOF` ``), one starting or ending in whitespace (prose between two spans a stray backtick paired wrongly), and a markdown list item (`- …`) in a block; the same words as a line of a shell block are still commands |
| capitalised word | a first word starting upper-case (prose, error text, SQL, directives); a `Verb-Noun` cmdlet missing from the allowlist is still reported |
| code statement | a first word with `_`, a code keyword (`import`, `require`, `global`, …), or followed by `=`, `=>`, `:=` |
| file name | a first word ending in an extension other than `.exe`/`.cmd`/`.bat`/`.com` |
| server command | `htpasswd`, `rrsync` (`REMOTE_COMMANDS`) |
| project-local tool | `wp-env`, `biome`, `astro`, `knip`, `mago`, `vue-tsc`, `playwright` (`PROJECT_BINS`) |
| shell block bodies | heredoc bodies, PowerShell here-strings, multi-line quoted strings, `#` comments; a pipe without spaces around it is part of a word |

## Baseline

Accepted false positives live in a baseline file of your own, read only when `--baseline <file>`
names it (keep it outside the plugin folder, which updates replace), one
`relative-file<TAB>check<TAB>reference` per entry with a `# reason` line above it; the file is
relative to the grandparent of the input it was found through (`.claude/CLAUDE.md`,
`<repo>/skills/<name>/SKILL.md`, `<plugin>/skills/<name>/SKILL.md`, `<plugin>/rules/<file>.md`). A listed finding is suppressed on
every line; an entry that matches no finding is reported as `stale-baseline` (exit 1). Every change
to the baseline is reviewed like code: real drift is fixed in the rule file, never listed.

## Test

```
python -I tools/drift-lint/tests/test_drift_lint.py
```

Creates its fixtures (including one junction, a symlink on POSIX) under `%TEMP%` and removes them.
Inside the harness repo it also lints the repo's own plugins against an empty home with
`tests/repo-baseline.txt` (references to files of a configured user machine, each with its reason):
0 findings, files and rules files read, and a planted broken reference in a copy caught.
