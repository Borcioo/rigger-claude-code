# rigger-core

The core plugin of Rigger: skills, agents, commands and hooks for running Claude Code with
subagents and long sessions.

Install: `claude plugin marketplace add Borcioo/rigger-claude-code`, then
`claude plugin install rigger-core@rigger`. Requirements: `sh` (Git Bash on Windows) and Python 3.9+
for the hooks and the rules; `/rigger-core:doctor` checks both. Update every Rigger plugin together:
the others' rules are printed by this plugin.

## Skills

- `git-workflow`: branches, commits, pull requests and SemVer releases.
- `orchestrating-subagents`: briefs, agent types and models, parallel work, fix rounds.
- `stack-tooling`, `e2e-playwright-speed`, `web-ui-traps`: tool choices, fast Playwright suites,
  UI and accessibility traps.

## Agents

`rigger-core:mapper` (finds code locations), `rigger-core:implementer` (one mapped change),
`rigger-core:worker` (implementation from a brief). The reviewer agent comes with the review
module and the testing skill with the testing module: `claude plugin install rigger-review@rigger`
and `claude plugin install rigger-testing@rigger`. Without one of them its slot stays empty, and
every session start, clear and compaction prints `rigger: slot <path> is empty: install <plugin>
(<command>)`.

## Commands

- `/rigger-core:doctor`: checks the machine (Claude Code version, the hook shell, `git`, Python as
  the hooks find it, the Rigger plugins and settings) and offers one block of install commands, run
  only after your yes. It reports `rigger-rules` and `rigger-lessons` (and `rigger-windows` on
  Windows) as MISSING when they are not installed: they are the recommended set, not a hard
  dependency of this plugin. With Python it also prints the assembler's rows: `rules-assembly` (OK
  with the module and section counts, or WARN with the first problem), one `rule-override` per
  `rules.d` section that replaces a Rigger one, one `expects` per plugin the project file expects
  that is not installed and enabled (FIX `claude plugin install <name>`), one `config-inactive`
  per config key that is read but has no effect yet (`shell_guard`, `company`, `leak_guard`), and
  these slot rows:
  - `slot <path>`: OK with what fills it and from which level; WARN when two plugins fill a hook slot
    (FIX `claude plugin disable <name>`); MISSING when the filler your config expects is not enabled
    (FIX `claude plugin install <name>`), and when the `reviewer` or `testing` default's plugin is
    not enabled (FIX `claude plugin install <plugin>@<marketplace>`); INFO `empty: <reason>` for
    another empty slot.
  - `slot-checker shell-guard`: the rule checker of the shell-guard filler, or INFO when it has none.
  - `slot-order <id>`: WARN for a config trigger row that loads `process.tdd` without `testing`
    before it.
  - `rules-d-table rules.d/<file>`: a `rules.d` file that holds a `| Before | Skill |` table, with
    the JSON of its rows to move into `triggers` of `<config dir>/rigger/config.json`.
- `/rigger-core:harness-setup`: runs doctor, then writes the Compact Instructions section into your
  user `CLAUDE.md` and, if you opt in, the `autoCompactWindow` setting. It
  asks before each change and changes nothing else.
- `/rigger-core:coordinate [plan]`: runs an approved plan with each independent part in its own
  session. Needs the Claude desktop app (its session tools) and the superpowers plugin; elsewhere it
  falls back to background subagents.
- `/rigger-core:environments`: fills `.claude/environments.json` from what the repo says, for the
  rigger-panel Environments pane.

## Hooks

| Event | What runs |
|---|---|
| SessionStart (startup, clear, compact), SubagentStart | the rules of every Rigger plugin, through 6 and 4 part handlers (`tools/assemble/part.sh`; "Rules assembly" below); the state-file rule goes to the main session only. On `startup`, `harness_start` warns about hooks that run twice (in both `settings.json` and a plugin) and about `## ` sections of your user `CLAUDE.md` that an enabled plugin already injects |
| UserPromptSubmit, PreCompact, PostCompact, SessionStart (compact) | the context guard: reminds a long session to keep its state file and holds automatic compaction until the state file says `safe_to_compact: true`, then puts the file back after compaction (`tools/context-guard/INSTALL.md`) |
| PostToolUse (Write, Edit of `.claude/environments.json`) | `tools/env-check` returns the reason the rigger-panel Environments pane would refuse the file |
| PreToolUse (`wrap_up`, `AskUserQuestion`), Stop | the side-session guard, desktop app only: a main session cannot end its turn while a side session it started has delivered a result it has neither archived nor listed under `## Side sessions kept` in its state file; the denial says both ways out. While the state file frontmatter has `plan:` and no allowed `stop:`, it also denies `AskUserQuestion` and a `wrap_up` with an `ask`: an approved plan runs without mid-plan questions |
| SubagentStop | `report_shadow` records, without blocking, whether each subagent's report opens with `STATUS: <word>` and, for an agent that fills a slot, whether the reply's shape meets that slot's contract (`tools/report-gate/INSTALL.md`) |

## Rules assembly

`tools/assemble/assemble.py` prints the rules of every enabled plugin that holds a
`rigger-module.json` (its rules files, their audience and its skill-table rows): this plugin first,
then the marketplace's order, then the rest by name, then your `rules.d` (rigger-rules' README). It
packs them into parts of at most 9,000 bytes; each part starts with the `Plugin root:` line of its
first plugin. The rules print on `startup`, `clear` and `compact` (from a snapshot of the last `startup` or `clear`); `resume` prints nothing.

- **Problems print as lines:** `Rigger rules not loaded: … (run /rigger-core:doctor)` at the top of
  part 1, in place of the rules, when Python 3.9+ is missing or the assembler fails; a part above 1
  that fails or prints nothing gives `Rigger rules part <i> of <K> not loaded: <reason> (run /rigger-core:doctor)`
  in its place. Lines starting `rigger:` at the top of part 1 report a skipped plugin, a bad config
  file, a replaced or duplicate rule, and slot problems. Rules that fit in no part are named in
  `rigger: rules left out over the hook cap: <ids>` at the end of the last part.
- **rigger-core older than another Rigger plugin:** rigger-rules, rigger-windows, rigger-wp,
  rigger-lessons and rigger-talk each run `hooks/core_check.sh <name>` on `startup|clear|compact`. When that
  plugin runs from its cache install and no installed rigger-core holds `tools/assemble/part.sh`, it
  prints `Rigger rules not loaded for <name>: rigger-core is older than <name> (run claude plugin update rigger-core)`.
  A plugin from a directory marketplace is not checked.
- **`RIGGER_MODULE_DIRS`:** plugin roots separated by the OS path separator (`;` on Windows, `:`
  elsewhere), each absolute and holding a `rigger-module.json`. Each is loaded in place of the
  installed copy of that plugin (one `rigger:` line says so when the copies differ). Set it to the same roots you
  pass with `--plugin-dir`: a `--plugin-dir` plugin is in no Claude Code state file, so without it
  its rules are missing and nothing says so. Plugin options of such a root are read from
  `pluginConfigs["<plugin>@inline"]` in `<config dir>/settings.json`, an installed plugin's from
  `<plugin>@<marketplace>`.
- **Config files**, JSON with `"schema": 1`: `rigger-defaults.json` in this plugin, then
  `<config dir>/rigger/config.json`, then `<project>/.claude/rigger.json`, the later one winning
  key by key (`null` removes a key). `triggers` (rows of the Skill triggers table: `before`, `load`
  with 1 to 4 `plugin:skill` or `slot:<name>` entries, each at most 200 characters, and `section`,
  which is ignored), `slots`, `trust` (personal file only) and, in the project file, `expects` (a list
  of plugin names) take effect, and so does `review.paths` (a glob mapped to a rules file or to
  `{"rules": <file>, "skip": <reason>}`, read by `review_scope.py`; project entries are read at the
  reviewed diff's base). A config file or company module holds at most 50 trigger rows; the rows
  after the 50th are ignored with one notice. `shell_guard`, `company` and `leak_guard` are read and
  listed by doctor as `config-inactive`, with no effect yet.
- **Skill triggers:** one table, `## Skill triggers` (`rules/skills.md`), for the session and for
  subagents: the rows of every module's manifest in module order, then the config rows. A `load`
  entry whose plugin is not enabled, or whose slot is empty, is dropped, and so is a row with no
  entry left or an unmet `requires`; a `RIGGER_MODULE_DIRS` root that holds only
  `.claude-plugin/plugin.json` counts as an enabled plugin. The table must fit one part: when it would
  not, config rows leave it (project rows first, then personal, then company, each from its last row
  up; a dropped override brings back the row it replaced) and one `rigger: left out <n> of the trigger rows from <files>: …`
  line follows the table. Module rows always stay.
- **Slots:** `rigger-defaults.json` names what fills each slot: the `reviewer`, `worker`,
  `implementer` and `mapper` agents, the `testing` skill, the `shell-guard` and `lessons` hooks, and
  the four `process` skills (superpowers). A slot entry is `{kind, target, model, requires}`. The main
  session gets `## Slots` after this plugin's rules, one row per slot (`Slot`, `Kind`, `Target`,
  `Model`, `From`); a `rules.d` section cannot replace it. An entry from your config that does not
  validate falls back to the level below with one notice; a default whose plugin is not enabled
  leaves the slot empty, and a module rules sentence that names an empty slot is left out. A hook slot
  is filled by every enabled plugin whose `rigger-module.json` says `fills: <slot>`; its row shows the
  failure mode the filler declares (`fails open`, `fails closed`; `closed` when fillers disagree),
  and two fillers give one notice line.
- **Command slots:** the `reviewer` slot may be `{"kind": "command", "target": [<argv>]}`, with the
  placeholders `{base}`, `{head}`, `{scope}` (a file of `<verdict><TAB><path>` lines) and `{out}` (the
  file the command writes its `rigger.reviewer/1` JSON to). `## Slots` then ends with one line for the
  Bash tool, `sh '<core root>/bin/py.sh' '<core root>/tools/slot-run/slot_run.py' <slot> --session <id> --base <base revision>`.
  The program, `target[0]`, is a bare name found on absolute `PATH` entries outside the repository
  and the working directory (an `.exe` or `.com` file on Windows) or an absolute path outside both; a
  `.cmd` or `.bat` file is never run. Name an npm tool as `node <script>`, and a tool inside the
  repository as `<interpreter outside the repository> <script>`. A working directory that holds every
  `PATH` folder with the program (a drive root, say) refuses it as well.
- **`trust.project_commands`:** a list of slot names in the personal file. Without its slot there, a
  project file may not set the `kind` or `target` of a slot whose merged entry is a command: the
  value is dropped with `rigger: .claude/rigger.json may not set a command for slot <slot>; add it to trust.project_commands in <file> to allow it`.
  A trusted project command prints `rigger: slot <slot> runs the project's command <argv> … (<n> bytes, sha256:<hash>) (trusted in <file>)`
  in every session that uses it (the argv as ASCII JSON, cut to 200 characters; the size and hash are
  of the whole of it).
- **`--render [--subagent]`** prints the whole text a session or a subagent is assembled from, without the hook limits:
  every notice, and any section the parts leave out over the 9,000-byte cap (the parts name those in their last line);
  **`--check [--modules
  <dir>...]`** fails on a duplicate id in one layer, an orphan override (a company section whose file
  stem matches a general file but whose id matches no general section there, or an empty one), a
  section over 9,000 bytes, an unknown placeholder, a trigger `load` that resolves to nothing, a slot
  default that does not validate, a second trigger table, a `{{slot:<path>}}` whose default plugin is
  absent, a row that loads `process.tdd` without `testing` before it, or 0 rules files read.
  `rules.d` is not checked. Without `--modules` it checks the plugins next to this one, which is only this
  plugin in an installed cache: pass `--modules <dir>...` there (the summary line names how many modules it read).

## Tools

- `tools/token-audit/token_audit.py`: where tokens go, from local transcripts (`report`), the cost
  of an A/B run (`runs`) or of one job's agents (`job`).
- `tools/variant-run/variant_run.py`: runs a command or an eval with a modified copy of your global
  `CLAUDE.md` instead of the live one.
- `tools/session-watch/session_watch.py`: the background watcher `/rigger-core:coordinate` uses
  for side sessions.
- `tools/slot-run/slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]`: runs a
  command slot as the session's snapshot holds it, with no shell, from the repository root, without
  `CLAUDE_*`, `HARNESS_*`, `GIT_*` and `RIGGER_MODULE_DIRS`. The command's `PATH` keeps only absolute
  entries outside the repository and the working directory (a virtualenv or `node_modules/.bin` inside
  the repository is dropped), and on Windows `NoDefaultCurrentDirectoryInExePath=1` is set, so a
  program the command starts by bare name through the operating system's lookup is never one the
  repository holds (known limit: a lookup the command's own code does that searches the working
  directory first, as Python 3.11's `shutil.which` and npm's `which` do on Windows, can still find a
  program in the repository root; trust only a command whose lookups you know). The command also runs
  with `PYTHONSAFEPATH=1`, so Python 3.11+ started with `-m` or `-c` never imports a module from the
  repository root; a script's own folder is left off `sys.path` too, so a reviewer script that imports
  a sibling module adds its folder to `sys.path` itself (known limit: an interpreter that resolves
  modules from the working directory, Python before 3.11 among them, can still load the repository's
  code, and on Windows the DLL search of the command's process can still read the working
  directory). It stops the whole process tree past
  900 s or 1,000,000 bytes of console output, when a process the command started still holds the
  console 5 s after the command exits (`child process left running`), and after every run (known limit:
  on POSIX a process the command detaches with `setsid` leaves the tree and can outlive the run). It then
  checks the output file with `slot_check.py`. The last line is `slot-run: ok <slot> <n> findings` or
  `slot-run: refused: <reason>`; a refusal is a failed review, never "no findings".
- `tools/slot-check/slot_check.py <slot> <file> [--repo DIR] [--base REV] [--head REV] [--session ID] [--shape-only]`:
  checks a reply against its slot's contract (`reviewer`, `worker`, `implementer`, `mapper`,
  `shell-guard`, `lessons`) and prints `slot-check: ok <slot>` or
  `slot-check: refused: <rule id>: <reason>`. A reviewer reply is the `rigger.reviewer/1` JSON object,
  alone or in the reply's last fenced `json` block; unknown keys are ignored. A reviewer reply that
  skips a test file `review_scope.py` lists for review is refused (`coverage-test-skipped`);
  `--session` (default `CLAUDE_CODE_SESSION_ID`) supplies the personal and company `review.paths`
  entries, without which a test file only they skip is refused.
- `tools/review-scope/review_scope.py --base REV [--head REV] [--repo DIR] [--session ID] [--rules-out DIR]`:
  the files a review covers, one `<verdict><TAB><path>` line each: `review`, `review: binary added`
  (every verdict that starts with `review` is to review), or `skip: <reason>` (deleted, binary,
  lockfile, vendored, generated, or a `review.paths` skip). A file named like a test is never skipped
  unless `review.paths` names it. Then `# review.paths: …` notices, one
  `# rules <glob> <file> (level <n>)` line per `review.paths` entry that matched a path to review (a
  project rules file is read at base and written to `--rules-out`), and
  `# review-scope: files <n>, review <r>, skipped <s>`. `--session` defaults to
  `CLAUDE_CODE_SESSION_ID`.
