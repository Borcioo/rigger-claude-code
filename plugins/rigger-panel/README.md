# rigger-panel

Opt-in. A function-hook plugin for Claude Code (`hooks/register.tsx`, no shell hooks, no rules) that
adds four panes beside the chat. They open at session start; `/harness` opens them again and reads
everything once more.

| Pane | Shows |
|---|---|
| Rigger | context, cost, weekly limit, agents and harness state of this session |
| Issues | your open GitHub issues in this repository, each with a `Start` button |
| Plan | the session's task list, how old it is, and when it may be out of date |
| Environments | the project's `.claude/environments.json` as cards |

All text is English. Nothing in the plugin writes to GitHub.

## Rigger

| Row | Source |
|---|---|
| Context, Session cost, Weekly limit | `$.session.usage()` |
| Agents in session | `Agent` tool calls since the session started |
| Rigger | the first 7 characters of the `rigger-core@rigger` version in `plugins/installed_plugins.json`, when that version is a commit hash (7 or more hex digits) |
| Lessons in inbox | entries in `lessons-inbox.md` (or the file `HARNESS_LESSONS_INBOX` names) |
| Compaction | last line of `state/compact-guard.log` |

The Rigger and Compaction rows need rigger-core installed (its context guard writes
`compact-guard.log`), and the Lessons row needs rigger-lessons (its hooks keep the inbox); without
them these rows draw `?`. The files are read from the config dir: `CLAUDE_CONFIG_DIR`, else
`USERPROFILE` (Windows), else `HOME`, plus `/.claude`; nothing is read when none is set. A value the
plugin cannot get, or a version that is not a commit hash, draws as `?`.
The pane redraws every 30 seconds and after each turn. On a graphical surface the context bar is an
`Svg`; on the terminal it is text.

## Issues

The open issues assigned to you (`--assignee @me`) in the GitHub repository of the session folder's
`origin` remote, read with the [GitHub CLI](https://github.com/cli/cli) through `$.process.run` (no
shell):

1. `gh --version`: does `gh` start at all;
2. `gh auth status --hostname github.com`: is it logged in;
3. `git remote get-url origin`: an `https://`, `ssh://` or scp-style (`github.com:owner/repo`) remote of
   github.com, with or without a user name, gives `owner/repo`;
4. `gh issue list --repo owner/repo --assignee @me --state open --limit 100 --json number,title,state,url`.

Each row shows `#N`, the title (cut at 60 characters) and the state.

- **Read-only:** the plugin runs only the four commands above. `tests/test_panel.py` fails on any
  `gh issue create|edit|close|reopen|comment|delete|…`, any `gh pr`, any `gh api` with `-X`,
  `--method`, `-f`, `-F`, `--field`, `--raw-field` or `--input`, any `git` command but
  `git remote get-url`, any other program, any MCP tool named like a write and
  any MCP call to a server other than the session app, and each pattern is proven against planted
  lines. The tests also check every command the plugin starts against that list.
- **Empty states** (one line each, a `Refresh` button, no error):

  | Situation | Line |
  |---|---|
  | `gh` not on PATH | `GitHub CLI (gh) not found. Install it from https://github.com/cli/cli, then press Refresh.` |
  | `gh` not logged in | ``gh is not logged in to github.com. Run `gh auth login`, then press Refresh.`` |
  | no `origin`, or not a GitHub one | ``This folder has no GitHub `origin` remote, so there is no repository to list issues from.`` |
  | no open issue assigned | `No open issues assigned to you in owner/repo.` |

- **Failures:** a `gh issue list` that exits non-zero, output that is not the JSON list, an entry of
  another shape or a url that is not `https://github.com/owner/repo/issues/N` shows
  `Issues unavailable: <reason>` and no rows, never a shorter list. A read gets 15 seconds.
- **When it reads:** when the panes open (session start, `/harness`) and on `Refresh`; there is no
  timer, so an open pane does not poll GitHub. While a read is out the rows are cleared. When 100
  issues come back, `showing the first 100` says older ones are cut.
- **`Start`:** only a press starts anything. The repository must match `owner/repo` as GitHub allows
  it (no leading dash, no shell or path characters) and the number must be a positive integer, or
  the row says `not a valid issue, nothing was sent`. The pane then calls
  `$.mcp.call('ccd_session', 'start_session', …)` (the Claude desktop app's session tool) with
  `context: 'fresh'`, the title `owner/repo#N: <short title>` and a brief that names
  `owner/repo#N`, its url and `gh issue view N --repo owner/repo --comments`, asks for a summary and a
  plan, and says no code changes before you agree and the issue stays read-only. The issue title,
  which anyone who can open an issue writes, appears only as the title of a session the pane starts
  itself; no text sent to a model contains it (the arguments handed to this session are titled
  `owner/repo#N`).
  - An answer starting `Proposed` shows `ready: click the New task card in the conversation`.
  - An error shows `refused: <text>`, keeps `Start` and adds `Ask this session`. The app's refusal of a
    mod's own call outside bypass mode (it names `mcp__ccd_session__start_session` and says the call
    "needs a person's approval") hands the same arguments to this session at once:
    `handed to this session: approve it if it asks`.
  - No answer in 30 seconds, or an answer of another kind, shows as unconfirmed and offers nothing,
    so one press is never two sessions. `Refresh` offers `Start` again after a finished start.
- **`Start` with `spawn_task`:** a session that has `mcp__ccd_session__spawn_task` and no
  `start_session` (a WSL session) proposes the same brief as a chip, title under 60 characters. Only a
  result that holds a task id shows `ready: click the card in the conversation`.
- **`Do it here`:** a session with neither tool cannot start another one, so each row has this button
  instead of `Start`: it sends the same brief to this session. The tool list is read when `/harness`
  opens the panes, on `Refresh` and at the start of each main-loop turn, never at session start (MCP
  tools are not connected yet then); a list that fails or does not answer in 3 seconds keeps `Start`.
- **`Ask this session`:** the fallback after a refusal: this session is asked to call `start_session`
  with the same arguments. The row then says it was asked, never that it started.

Outside the Claude desktop app there is no `ccd_session` server: `Start` shows the refusal and
`Ask this session` or `Do it here` sends the brief to the session you are in.

## Plan

The pane `rigger-plan` (`hooks/plan.ts`) mirrors the session's task list (the desktop app's
`update_plan` and `wrap_up` tools): `update_plan` changes it, `wrap_up` checks the whole list off. The
list is only as current as the session's last call, so the pane says how old it is and when the
session has moved on without it.

- **Age:** `Updated 14:32 (12 min ago)` above the steps (`just now` under a minute, `N h ago` from an
  hour), redrawn with the 30-second refresh.
- **May be out of date:** a yellow line and a running step that no longer spins when one of these came
  after the last list update while a step was not completed:

  | Line ends | Seen as |
  |---|---|
  | `a commit at 14:40` | a `Bash` or `PowerShell` call, the main loop's or a subagent's, that ran `git commit` and did not fail (a dry run and `commit-tree` do not count) |
  | `a background task finished at 14:40` | a prompt whose origin is a background task's notification, except a subagent's interim one |
  | `a turn ended at 14:40 with no update` | a main-loop turn that answered without touching the list while a step was running |

  The next `update_plan` or `wrap_up` clears it. A list update up to 2 seconds before the commit's
  command started, or while it ran, was sent in the same message and covers that commit.
- **Reminder:** the session reads one line, `The task list was last updated before <event>; update it
  to match the current state.`, with the result of the main loop's next shell call after the list
  fell behind, or at the next prompt, once per stale period. A commit's own result never carries it.
- **`wrap_up`:** every step is checked off, except steps in progress while `$.agent.list()` holds a
  running subagent.
- **Not seen:** a commit made outside this session's tool calls (your terminal, another session in
  the same worktree). The pane then shows the age alone.

## Environments

The pane `rigger-envs` (`hooks/envs.ts`, `hooks/env-flow.ts`) draws `.claude/environments.json` from
the session's folder: one rounded card per environment, the name with its `role` as a badge (a role
starting with `prod`, or with no role an environment name starting with `prod`, is white on red, with
a red border), the site and the admin as links, then
Branch, Server, Account, Path, Database, Deploy and Notes in a dim label column. It reads the file
when the pane opens and on `Refresh`, and again when the pane is drawn while the session is in
another folder than the one last read (a session moved into a worktree); there is no timer.

```json
{
  "version": 1,
  "project": "name",
  "repository": "https://github.com/owner/repo",
  "environments": {
    "local": { "url": "http://demo.test", "admin": "http://demo.test/wp-admin" },
    "staging": {
      "url": "https://example.com/staging",
      "server": "example.com",
      "account": "demo",
      "role": "STAGING",
      "path": "~/sites/example.com",
      "branch": "main",
      "database": "demo_db",
      "deploy": "one line: how a release gets here",
      "notes": "free text"
    },
    "prod": { "url": "https://example.com", "role": "PROD" }
  }
}
```

- **Fields:** none is required; `url` and `admin`, when present, must be `http(s)` addresses, every
  other field is text. `server` and `account` are a host name and a login, `database` a name only.
- **`repository` (optional, top level):** the repository's web address, drawn as a link under the
  project name.
- **Unknown keys** at any level are ignored, whatever their shape (`mcp`, for example, is read by
  other tools and draws nothing here).
- **Order:** any name is allowed; `local`, `dev`, `prod` come first in that order, the rest in file
  order.
- **No secrets:** a field named like `password`, `passwd`, `token`, `secret` or `key` (at any depth,
  any case), or a value holding `://user:pass@`, refuses the whole file: one line names the field and
  nothing from the file is drawn.
- **Other states:** no file shows ``No `.claude/environments.json` in this project.`` and names
  `/rigger-core:environments`, which creates it; invalid JSON or a wrong shape (no `version: 1`, no
  `environments` object) shows `Environments unavailable: <reason>`. A file that cannot be read counts
  as missing.
- **Checked when written:** rigger-core's `tools/env-check/env_check.py` gives the same reasons right
  after a Write or Edit of the file. Both read the cases in `tests/env-cases.ts`, so a change to
  `parseEnvironments` changes that file and `env_check.py` in the same commit.

## Tests

`claude plugin test plugins/rigger-panel` runs `hooks/*.test.tsx`. `gh` and `git` are a stand-in
beneath the plugin that recognises each command by its whole argument vector, and the session app's
`mcp.call` is a test hook, so no test reaches GitHub or starts a session. `tests/test_panel.py` runs
that under the repository's test runner (`scripts/run_tests.py`; at least 130 passing), checks the
source for machine paths, GitHub and git writes and programs other than `gh` and `git`, and skips the
`claude` run with `--no-claude`.
