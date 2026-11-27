# rigger-panel

Opt-in. A function-hook plugin for Claude Code (`hooks/register.tsx`, no shell hooks, no rules) that
adds five panes beside the chat, plus any tab a Rigger module declares ([Module tabs](#module-tabs)).
They open at session start; `/harness` opens them again and reads everything once more. Each pane but
Rigger is a tab that can be switched off per project ([Tabs](#tabs)).

| Pane | Shows |
|---|---|
| Rigger | context, cost, weekly limit, agents and harness state of this session |
| Issues | your open GitHub issues in this repository, each with a `Start` button |
| Plan | the session's task list, how old it is, and when it may be out of date |
| Environments | the project's `.claude/environments.json` as cards |
| Inbox | what the other sessions on this machine wait on: a question, a result to check, an error or an approval |

All text is English. Nothing in the plugin writes to GitHub; a module tab runs only what its own
module's manifest declares.

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

## Tabs

The Rigger pane ends with a **Tabs** row: one button per tab (Issues, Plan, Environments, Inbox), `[x]` when
it is on, `[ ]` when it is off. A press switches the tab for this project and opens or closes its pane
at once; session start and `/harness` open only the tabs that are on. The Rigger pane is always on.

- **Where the choice is kept:** the plugin's own store (`$.store`, in the Claude Code config dir,
  outside any repository), key `tabs:<project>`, value `{ "on": [ids], "off": [ids] }`: the tab ids
  (pane ids such as `rigger-issues`) switched on and off there.
- **Project:** the parent folder of `git rev-parse --path-format=absolute --git-common-dir` run in the
  session folder, so every worktree of a repository shares the main checkout's choice; outside git,
  the session folder itself.
- **Default:** every tab is on, unless its id is in the config key `panel.tabs_off` (a list of tab ids;
  levels 2 and 3, a company module or the personal `rigger/config.json`), read from rigger-core's
  snapshot of the session (`<config dir>/rigger/state/<session id>.resolved.json`). A tab the project
  switched on stays on; one it switched off stays off. A missing or unreadable snapshot means no
  default off list.

  ```json
  { "schema": 1, "panel": { "tabs_off": ["rigger-envs"] } }
  ```

## Module tabs

A Rigger module (any plugin with a `rigger-module.json`) adds a tab by declaring it under `panel.tabs`
in that manifest. The panel knows no tool behind it: it calls the MCP tool or runs the command the tab
names, cuts the text into rows with the module's regexes and draws a plain list with a `Refresh`
button. The modules are the ones rigger-core's snapshot of the session lists
(`<config dir>/rigger/state/<session id>.resolved.json`, `modules: [{ name, root }]`); each manifest is
read from `<root>/rigger-module.json`. No snapshot, an unreadable manifest or no `panel` key adds no
tab. The tab's pane id is `m-<module>-<id>`; [Tabs](#tabs), `panel.tabs_off` and the per-project store
treat it like a built-in tab. rigger-core's SessionStart hooks may write the snapshot after the panel
starts: when it was missing then, the first main-loop turn reads it once more, opens a tab that is now
on and closes one that is now off.

```json
"panel": { "tabs": [ {
  "id": "tickets",
  "title": "Tickets",
  "source": { "mcp": { "server": "tracker", "tool": "list-tickets",
                       "args": { "assigned_to_me": true, "limit": 50, "project_id": "{select:project}" } } },
  "rows": {
    "pattern": "^- (?<key>[A-Z0-9]+-\\d+): (?<title>.+)$",
    "detail": "^\\s+Status: (?<detail>[^|]+?)\\s*\\|",
    "url": "https://example.com/tickets/{key}",
    "empty": "^Tickets \\(0\\)"
  },
  "select": [ {
    "id": "project", "label": "Project", "type": "number",
    "options": { "mcp": { "server": "tracker", "tool": "list-projects", "args": {} },
                 "pattern": "^- \\[(?<key>[A-Z0-9]+)\\] (?<label>.+) \\(ID: (?<value>\\d+)" },
    "default": "tracker.project_id"
  } ]
} ] }
```

Every field is checked and a wrong type refused. An invalid entry is skipped, and the Rigger pane shows
one line under Tabs: `module <m> tab <id>: skipped: <reason>` (`#<n>`, its place in the list, when the
id itself is invalid; `*` when `panel.tabs` is not a list).

- `id`: `^[A-Za-z0-9_-]{1,32}$`, unique within the module. The pane id `m-<module>-<id>` must match
  `^[A-Za-z0-9_-]{1,64}$` (the shape `panel.tabs_off` takes) and must not be another module's.
- `title`: a string of 1-40 characters.
- `source`: exactly one of `mcp` (`{ server, tool, args? }`, `args` an object) or `command` (an argument
  vector of 1-32 strings; an item starting `./` resolves against the module root). The command runs in
  the session folder. The text is the MCP result's text blocks joined with `\n` (with no text block but
  `structuredContent`, that as indented JSON), or the command's stdout. An error result, a non-zero
  exit or a call that fails shows the failure line `module <m> tab <id>: <first line of the error>`
  (cut at 200 characters). Each call is bounded at 20 s.
- `rows.pattern` (required): a JS regex compiled with flag `u`, matched against each line; a match
  starts a row. Named groups: `title` (required), `key`, `url`, `detail`. `rows.detail`: a later line
  that starts no row but matches it sets the row's `detail` from its `detail` group, when the row has
  none yet. `rows.url`: a template where `{<group>}` is that group's value, percent-encoded; every group
  it names must be a group of `rows.pattern`. Without `rows.url`, a `url` group is the link. A link that
  does not start `https://` is dropped. `rows.empty`: a regex; any line matching it shows the empty
  state. A regex that does not compile skips the tab.
- **Fail closed:** blank text or a `rows.empty` match shows `No items.`. Text with something in it, no
  row and no `rows.empty` match shows exactly `module <m> tab <id>: output did not match its pattern`,
  never the empty state.
- **Bounds, before any regex runs:** the text is cut at 256 Ki characters, lines over 2000 characters
  are skipped, at most 50 rows are kept (then `The first 50 are shown.`), and a regex source over 500
  characters is refused. The patterns run with no time bound (the manifest is the installed plugin's
  own code), so keep them linear: no nested repetition such as `(a+)+`.
- **A pick wins:** each read of a tab takes the next number and only the latest one writes, so a pick
  made while a read for the old choice runs is never overwritten by it.
- `select` (at most 3): `id` like a tab id, `label` 1-40 characters, `type` `"string"` (the default) or
  `"number"`. `options` is a source (`mcp` or `command`, with no `{select:…}` in it) plus `pattern`
  with a `value` group (required) and a `label` group (else the value); the same bounds, at most 200
  options, a repeated value kept once, a value that starts with `-` or holds a control character
  dropped (it would reach a command's argv); a number select whose value is not a number fails. `default`: a
  dot path into the session folder's `.claude/environments.json`; the option whose value equals that
  value as a string is chosen first. A missing file, a missing key, invalid JSON or a value that is no
  option picks the first option. The choice is kept for the session (in state, not the store) and a
  new pick reads the rows again. Options that fail to load, or none, show the failure line and the
  rows are not read.
- `{select:<id>}`: an `args` value (top level) or an argv item that is exactly this gets the chosen
  value, a number for a number select. A placeholder naming no declared select skips the tab.
- Each row shows the key (when there is one), the title, the detail (dim) and a link. A tab is read
  when its pane is first drawn, on `Refresh`, on a new pick, and again by `/harness` while it is on.

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

- **Read-only:** of its own, the plugin runs only the four commands above, and `git rev-parse` for
  [Tabs](#tabs); a [module tab](#module-tabs) runs the source its module declares. `tests/test_panel.py`
  fails on any `gh issue create|edit|close|reopen|comment|delete|…`, any `gh pr`, any `gh api` with
  `-X`, `--method`, `-f`, `-F`, `--field`, `--raw-field` or `--input`, any `git` command but
  `git remote get-url` and `git rev-parse --path-format=absolute --git-common-dir`, any other program,
  any program whose argument vector is not written out, any MCP tool named like a write and any MCP
  call to a server other than the session app, and each pattern is proven against planted lines. The
  one exception is the module tab's source: one MCP call and one program start in
  `hooks/module-tabs.ts`, at one spelling each, allowed there and nowhere else. The [inbox](#inbox) has
  the only others: its `get_session` call to the desktop app's `ccd_session_mgmt` server (in
  `hooks/inbox/writer.ts`; no other tool of that server) and the system opener (`open`,
  `cmd /c start`, `xdg-open`) with `uname -s` (in `hooks/inbox/reader.ts`), each at one spelling,
  with the server, the tool and the opener's argument vectors pinned. The tests also check
  every command the plugin starts against that list.
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

## Inbox

The pane `rigger-inbox` (`hooks/inbox/`) lists what the other sessions on this machine wait on, so a
session that stopped for you is not missed among many. Each session writes one entry for itself; every
session reads the others'. `/inbox` opens the pane.

- **What writes an entry** (main loop only, never a subagent):

  | Kind | When |
  |---|---|
  | ❓ question | `wrap_up` (the built-in `WrapUp` or the desktop app's `mcp__ccd_turn__wrap_up`) with a question, or one marked blocked |
  | ✅ result to check | `wrap_up` without either |
  | ⏳ approval | a tool call that asks for permission: `waiting for approval: <tool>` |
  | ⚠️ error | a turn that ends without `wrap_up` because it was aborted, failed or refused |

  The built-in `WrapUp` result's own next-step box is used when there is one (the engine has masked
  keys and tokens in it), else the call's input. Every text is cleaned (control and format characters
  become spaces) and capped before it is written, and again when it is read.
- **Where:** `<config dir>/rigger-panel/inbox/<session id>.json`, one file per session, replaced by its
  next entry. `$.fs` cannot delete, so an entry is closed by `<session id>.closed`, which holds a time:
  every entry written before it is hidden, the session's next entry shows again.
- **What closes it:** a prompt you send in that session (typed, from the desktop app, from a phone or
  a click on the app's own buttons; a message from another session, a task notification, a schedule
  or a plugin does not), the next tool call after an approval entry or a turn that ends after it, and
  `Close` on the item.
- **What is shown:** the other sessions' open entries younger than 7 days, oldest first, each with
  its title, folder, age, text, question, `Open` and `Close`; `Nothing waiting.` when there are none.
  The folder is read again every 5 seconds. While anything waits, a band above the prompt says
  `📥 N waiting · oldest: <title>, <age>` with an `Inbox` button.
- **`Open`:** the desktop app draws a non-https link as plain text, so the entry's session link
  (`claude://…`) is handed to the system opener: `open` on macOS, `cmd /c start "" <link>` on Windows,
  `xdg-open` elsewhere (the system from `$.session.machine()`, else `OS`, else `uname -s`). The entry
  file is written by another process and `cmd` re-parses its command line, so only the exact link of
  the entry's own session (`claude://claude.ai/epitaxy/<session id>`) is opened; anything else shows
  `not opened: the link is not a session link` and runs nothing.
- **Without the desktop app:** the session's identity comes from the app (`get_session` on
  `ccd_session_mgmt`, bounded at 5 seconds, asked again at most once per turn). Without it nothing is
  written and nothing is said. With it, a write that fails shows
  `rigger-panel inbox: entry not saved (<reason>)` on the status line.
- **Tab off:** with the Inbox tab off for the project ([Tabs](#tabs)) the pane does not open at start
  and the band is hidden. The session still writes its own entry, so the other sessions' inboxes keep
  it, and `/inbox` still opens the pane.

## Tests

`claude plugin test plugins/rigger-panel` runs every `*.test.ts` and `*.test.tsx` under `hooks/`. `gh` and `git` are a stand-in
beneath the plugin that recognises each command by its whole argument vector, and the session app's
`mcp.call` is a test hook, so no test reaches GitHub or starts a session. `tests/test_panel.py` runs
that under the repository's test runner (`scripts/run_tests.py`; at least 272 passing), checks the
source for machine paths, GitHub and git writes and programs other than `gh` and `git` (the module
tab's source and the inbox's calls excepted, each at its one spelling) in every file under `hooks/`, and skips the
`claude` run with `--no-claude`.
