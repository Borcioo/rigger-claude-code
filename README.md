<p align="center">
  <img src="assets/rigger-logo.webp" alt="Claude Code Rigger logo" width="560">
</p>

# Rigger

> Rigger is an independent community project. It is not affiliated with, endorsed by or
> sponsored by Anthropic. Claude and Claude Code are trademarks of Anthropic, PBC.

Rigger is a Claude Code plugin marketplace: a working harness for Claude Code sessions and
subagents. It ships seven plugins: working rules (verification, tests, scope, git) injected at
session start and into every subagent, a lessons inbox that turns mistakes into rules, portable
skills and agents, context and compaction guards, a side panel, and opt-in packs for native Windows
and WordPress development. Install the plugins you want; only `rigger-rules` requires another plugin
(`rigger-core`).

Everything is plain Markdown, POSIX `sh` and Python hooks: read a rules file before you install it,
and change it by forking.

## Plugins

| Plugin | Install | Adds | Depends on |
|---|---|---|---|
| `rigger-core` | default | skills (git-workflow, orchestrating-subagents, stack-tooling, e2e-playwright-speed, web-ui-traps), agents (mapper, implementer, worker, reviewer), context-guard and compact-guard hooks, token-audit, variant-run, `harness_start`, `/rigger-core:doctor`, `/rigger-core:harness-setup`, `/rigger-core:coordinate`, `/rigger-core:environments`, the state-file rule (main session) | none |
| `rigger-rules` | default | the working rules, injected at session start and into subagents, plus every `*.md` file of your own `rules.d` folder | `rigger-core` |
| `rigger-lessons` | default | lessons inbox count at session start, the capture rule, the `curate-lessons` skill, a lint for lessons, and opt-in background curation batches | none |
| `rigger-talk` | opt-in | reply-format rules (language, length, report order) for the main session, configured through `userConfig` | none |
| `rigger-windows` | opt-in, native Windows only | rules (shell, skill triggers), shell-guard hooks (deny commands certain to fail in PowerShell or Git Bash), `safe-edit.ps1`, drift-lint, `mem-check`, skills windows-dev-env, php-herd-tooling, node-versions | none |
| `rigger-wp` | opt-in, for WordPress developers | the `rigger-wp-traps` skill, the rule that says when to load it, the `wp-outbound-block` template, and the upstream WordPress agent skills behind `wordpress-router` | none |
| `rigger-panel` | opt-in | the `/harness` command and four panes beside the chat: Rigger (the session's context, cost and limits, harness state), Issues (the GitHub issues assigned to you, read-only, through `gh`), Plan (the task list) and Environments (the project's `.claude/environments.json`) | none to run; the Rigger pane's version and Compaction rows read `rigger-core` files and its Lessons row reads the `rigger-lessons` inbox (a `?` without them) |

"Default" means this README recommends it; nothing is installed for you. Each plugin has its own
`plugins/<plugin>/README.md`.

## Requirements

- Claude Code with plugin support.
- `git`, `sh` and Python 3.9 or newer for the Python hooks. A hook that needs Python exits 0 without
  it, so the guards fail open.
- On Windows, `sh` comes from Git for Windows, and Claude Code must find its Git Bash: at
  `CLAUDE_CODE_GIT_BASH_PATH`, in `Program Files\Git\bin` (or `Program Files (x86)\Git\bin`) on the
  C drive, or two folders above the `git` on PATH. Otherwise it runs every hook in PowerShell, where
  every Rigger hook fails.
- `rigger-panel` needs a Claude Code build with function-hook plugins and a surface that docks
  panes; its issues list needs `gh`, logged in, in a repo with a GitHub `origin`.

`/rigger-core:doctor` checks all of this.

## Install

```
claude plugin marketplace add Borcioo/rigger-claude-code
claude plugin install rigger-core@rigger
claude plugin install rigger-rules@rigger
claude plugin install rigger-lessons@rigger
```

Opt-in plugins install the same way:

```
claude plugin install rigger-talk@rigger
claude plugin install rigger-windows@rigger
claude plugin install rigger-wp@rigger
claude plugin install rigger-panel@rigger
```

Plugins install at user scope; add `--scope project` or `--scope local` to limit one to a repo.
Restart Claude Code (or run `/reload-plugins`), then check with `claude plugin list`. Then run once:

```
/rigger-core:harness-setup
```

It runs `/rigger-core:doctor` first, then offers to write the Compact Instructions section into
your user `CLAUDE.md` (Claude Code reads Compact Instructions only from `CLAUDE.md`, so a plugin
cannot supply them) and, if you agree, the `autoCompactWindow` setting and a worktree root. It shows
each change first and writes nothing without your agreement. The compaction guards also work
without the setting, with Claude Code's default window.

In a project, `/rigger-core:environments` fills `.claude/environments.json` from what the repo
already says. For a large approved plan, `/rigger-core:coordinate <plan file>` runs it through
separate sessions.

### Installing with an agent

An agent asked to install Rigger from a link can do it in one go:

1. Check, reading only: the OS, `claude --version`, `git`, Python 3.9+ (`python3 --version`, on
   Windows `py -3 --version`), and on native Windows Git Bash where Claude Code finds it. Name what
   is missing and how to get it; install nothing without the user's yes.
2. Ask once what to leave out (`rigger-talk`, `rigger-wp`, `rigger-panel`, the separate
   `superpowers` plugin, plugin auto-update). `rigger-core`, `rigger-rules` and `rigger-lessons`
   are always installed; `rigger-windows` goes on native Windows and never elsewhere.
3. Install what is kept with the commands above. Superpowers, unless left out:
   `claude plugin marketplace add obra/superpowers-marketplace`, then
   `claude plugin install superpowers@superpowers-marketplace`.
4. On native Windows set `CLAUDE_CODE_USE_POWERSHELL_TOOL` to `1` under `env` in the user
   `settings.json`, keeping what is there.
5. Check `claude plugin list`, then tell the user to restart Claude Code and run
   `/rigger-core:harness-setup`.

## Your own rules

`rigger-rules` prints, after its own rules, every `*.md` file in
`${CLAUDE_CONFIG_DIR:-~/.claude}/rigger/rules.d/`, sorted by name: at session start, and into
subagents too unless the name ends in `-main.md`. A missing folder prints nothing. Output stops
before the hook's size cap and names the files it left out. `rigger-lessons` writes the lessons you
mark as global into `rules.d/lessons.md`. Your files live in your config directory, so plugin
updates never touch them.

## What is optional

"Depends on" in the table is the only hard requirement. The rest works without the other plugin;
text that names it then points at nothing:

- **Sibling plugins.** A rule or skill that names a skill of another plugin (`rigger-core:<skill>`,
  `rigger-windows:<skill>`, the `safe-edit.ps1` of `rigger-windows`) works only when that plugin is
  installed and enabled.
- **Superpowers.** A separate plugin, not required. Rule text that names it (the plan rules, the
  skill table, the agents, `/rigger-core:coordinate`, the question format of `rigger-talk`) applies
  only if it is installed.
- **Lessons curation batches.** Off by default. With `RIGGER_LESSONS_BATCH=1` in the environment,
  `rigger-lessons` curates the inbox in the background with up to two `claude -p` runs per batch, billed
  to your plan usage or API credit (capped at $2 + $3 a batch, typically about $0.5).
  `plugins/rigger-lessons/README.md` has the details.
- **`HARNESS_LESSONS_ARCHIVE`.** Points at an optional folder of archived cases, one file per case
  (layout: `plugins/rigger-lessons/templates/archive/README.md`). `curate-lessons` writes applied
  lessons there, and the verify rule searches it for an error text before debugging. Unset, both
  steps are skipped.
- **PowerShell tool.** `rigger-windows` assumes Claude Code has a PowerShell tool;
  `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` adds it. Without it the "default shell" rule names a tool
  that does not exist, and the model uses Bash; the Bash guard still runs.

## Cost

Context added by the rules hooks with all seven plugins installed: about 9.5k tokens in a main
session over `rigger-core` alone, about 4.2k per worker spawn and about 1.4k per Explore spawn, plus
whatever your `rules.d` holds. `resume` re-injects nothing. The `Skill` tool adds the skills listing,
about 3k more per worker or reviewer spawn.

## Update

```
claude plugin marketplace update rigger
claude plugin update rigger-core@rigger
```

Run `update` once per installed plugin, then restart Claude Code; a change to a rules file or a hook
reaches a session only after both. A plugin has no `version` in its `plugin.json`, so its version is
the git commit of the marketplace. Releases are tagged and listed in `CHANGELOG.md`.

### Auto-update

Auto-update is off for this marketplace until you turn it on in your user `settings.json`:

```json
"extraKnownMarketplaces": {
  "rigger": {
    "source": { "source": "github", "repo": "Borcioo/rigger-claude-code" },
    "autoUpdate": true
  }
}
```

Claude Code checks once per session, at a random moment in its first 10 minutes. Every installed
plugin moves to the new commit, and the change reaches the next session. If `DISABLE_AUTOUPDATER=1`
is set, also set `FORCE_AUTOUPDATE_PLUGINS=1`, or the check is skipped.

## Opting out and uninstalling

To drop a set of rules, disable its plugin; `enable` brings it back. Add `--scope local` to disable
it for one repo only.

| To drop | Run |
|---|---|
| working rules and your `rules.d` | `claude plugin disable rigger-rules@rigger` |
| lessons rules and the inbox count | `claude plugin disable rigger-lessons@rigger` |
| reply-format rules | `claude plugin disable rigger-talk@rigger` |
| Windows rules, shell guards and skills | `claude plugin disable rigger-windows@rigger` |
| WordPress rule and skills | `claude plugin disable rigger-wp@rigger` |
| the side panel and `/harness` | `claude plugin disable rigger-panel@rigger` |
| skills, agents, guards and the state-file rule | `claude plugin disable rigger-core@rigger` (disable `rigger-rules` first: it depends on `rigger-core`) |

The skills and agents of `rigger-core` stay available without `rigger-rules`; that is why they are
separate plugins.

```
claude plugin uninstall rigger-wp@rigger
claude plugin marketplace remove rigger
```

`--keep-data` keeps a plugin's data directory. Uninstall the plugins before removing the
marketplace, and **uninstall `rigger-rules` before `rigger-core`**: `claude plugin uninstall
rigger-core` succeeds with only a warning, and `rigger-rules` then fails to load.
`claude plugin uninstall rigger-rules@rigger --prune -y` also removes `rigger-core` when it came in
as a dependency.

## Where the rules are injected

- Main session: each plugin that injects rules has a `SessionStart` hook on `startup|clear|compact`
  that prints its rules files, one handler per file, after a `Plugin root: <path>` line so the rules
  can name plugin tools without an absolute path. `resume` is left out: the resumed transcript
  already holds the rules.
- Subagents: a `SubagentStart` hook prints a subset. `rigger-rules` (working rules and `rules.d`),
  `rigger-lessons` (report lessons to the main session) and `rigger-windows` (root name and default
  shell) have one. Nothing is printed for the agents that run without `CLAUDE.md` (Explore, Plan,
  `rigger-core:implementer`, `rigger-core:mapper`).

## userConfig

`rigger-talk` has options you set once per user:

| Option | Meaning | Default |
|---|---|---|
| `reply_language` | language of replies; empty follows Claude Code's `language` setting | empty |
| `reply_lines` | default reply length in lines | 5 |
| `report_lines` | final-report length in lines | 15 |

```
claude plugin configure rigger-talk@rigger
echo '{"reply_language":"en","reply_lines":"5"}' | claude plugin configure rigger-talk@rigger --values-stdin
```

Or at install time: `claude plugin install rigger-talk@rigger --config reply_lines=8`. Restart
Claude Code to apply. Values are stored in your user settings (`pluginConfigs`), so updates keep
them; an option you never set is not exported to hooks, which then apply their own defaults.

## Migrating a hand-made setup

If you already copied similar hooks, rules or skills into `~/.claude`, installing the plugins runs
them twice. Start a new session after installing and read the `harness_start` warnings: they name
each hook that runs twice (in `settings.json` or `settings.local.json` and in a plugin, or in two
plugins) and each `## ` heading of your user `CLAUDE.md` that an enabled plugin's `rules/*.md` also
has, matched exactly. Remove the named duplicates, keep your machine-specific lines, and restart.
Hooks in a project's `.claude/settings.json` and plugins installed at project scope are not
scanned: check those by hand.

## Platforms

Windows is the tested platform. Linux is checked under WSL (Ubuntu, dash as `/bin/sh`) with a fresh
isolated config. macOS is not verified; please open an issue with the command and its output when
something fails.

## Contributing and license

See `CONTRIBUTING.md` for the test gate and how to propose a change. Rigger is licensed under the
GNU General Public License v3.0 or later (`LICENSE`). The skills under
`plugins/rigger-wp/skills/wordpress-router/references/skills/` are upstream copies, shipped
unchanged; their origin is in `plugins/rigger-wp/skills/wordpress-router/references/UPSTREAM.md`.
