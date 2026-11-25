# Rigger modular architecture: design

Status: design, approved decisions applied. Base: `main` at `34e7713` (release 0.4.0).
Every `path:line` below is a line of that commit.
Names of modules that do not exist yet are written in capitals (`rigger-REVIEW:reviewer`,
`rigger-TESTING:testing`): phase 3 creates them.

## 1. Intent and why

Rigger today is seven plugins whose wiring is written into their text. Each plugin prints its own
rules files through its own `SessionStart` and `SubagentStart` handlers. Skill names, agent names
and sibling plugins are hard-coded in rules, skills, agents and tests. For example:

- `plugins/rigger-rules/rules/skills.md:6-15` holds the skill-trigger table as literal rows.
- `plugins/rigger-core/agents/worker.md:8-10` names `rigger-core:testing` and three superpowers
  skills.
- `plugins/rigger-core/skills/orchestrating-subagents/SKILL.md:89-93` names the worker and reviewer
  agents.
- `plugins/rigger-rules/rules/rules_start.sh:87` lists the agent types that get no rules.
- `plugins/rigger-core/tools/doctor/doctor.sh:489` requires `rigger-windows` on Windows.

So replacing the reviewer, the testing skill or the shell guard means editing several plugins at
once. A company that wants its own rules, deploy skill or ticket flow on top of Rigger has to fork
Rigger or copy rules files whole. There is no defined way to replace one section, and no code that
decides which version wins.

This design makes Rigger a small fixed core plus modules:

- **Module:** a Claude Code plugin with a `rigger-module.json` manifest. Claude Code installs,
  updates and versions it.
- **Core:** reads the manifests and a four-level config once per session start. It assembles the
  rules, the skill-trigger table and the slot table, then injects them.
- **Overrides:** a company overlay or a person's own files replace whole rule sections by id. Code
  resolves every conflict.
- **Slots:** the reviewer, the testing skill, the shell guard and the other replaceable parts each
  have an output contract. A different agent, skill or external CLI can fill a slot, and code checks
  its output.

**Fixed core** (never a slot, never replaced by a module), all in rigger-core:

- the orchestrator: `/rigger-core:coordinate` and `orchestrating-subagents`;
- the state file rule and the compaction guard that reads it;
- rules and trigger assembly, with its config resolver and snapshot (§7–§9);
- the leak guard (§11.4);
- the slot contracts and their checkers (§10).

## 2. Goals, non-goals, anti-goals

### Goals

- G1. A module is a plugin plus `rigger-module.json`: its slot, its trigger rows, its rules with
  their layer, and its tests.
- G2. Rules text and the trigger table name no skill. Names come from manifests and config at
  session start.
- G3. Rule layers general → company → personal, resolved by section id in code. Duplicate ids are
  rejected by a check.
- G4. Swappable slots with output contracts a deterministic checker can verify.
- G5. Lessons land personal. Curation proposes a target among the installed ones, a person decides,
  and a leak guard blocks private terms on the way out.
- G6. Config in four levels, merged by key, read once at session start. With no file, Rigger
  behaves as today.
- G7. Per-module tests: changed plugins, plus a seconds-long contract test, plus the sweep.
- G8. Phased delivery, mechanism first, one release per phase, with a migration path for existing
  Windows and macOS users.

### Non-goals

- Rewriting existing rules content. Phase 1 reproduces today's text byte for byte. Content edits
  are ordinary later PRs.
- A module registry or discovery service. Marketplaces are Claude Code's.
- Per-turn reconfiguration. Config changes apply at the next `startup` or `/clear`, as `rules.d`
  does today (`plugins/rigger-rules/README.md:45`).
- Porting company content into the public repository. Moving an existing company fork onto modules
  is that fork's own plan. Phase 4 ships only the mechanism, a fixture and a migration path.

### Anti-goals (never)

- **Never a second plugin manager.** Rigger reads Claude Code's plugin state and never writes it. It
  does not install, enable, disable, update or version anything. "Turn a module off" is
  `claude plugin disable`.
- **Never a model-resolved rule conflict.** Two texts for one rule id never both reach the model
  with a request to choose. The code picks one, or the check fails.
- **Never a private term in public.** No company, client, person or private repository name in
  anything under `plugins/`, `scripts/`, `tests/` or `docs/`. Denylists live in private modules or
  on the person's machine, never in this repository.
- **Never a silent fallback that drops rules.** If assembly cannot run, the session gets one visible
  line saying so, never an empty rules block that looks like success.
- **Never a project file that runs a command without the person's consent.** A cloned repository's
  `.claude/rigger.json` cannot add a command-kind slot on its own (§7.4).

## 3. Constraints

| Constraint | Consequence for the design | Source |
|---|---|---|
| Windows (Git Bash `sh`, PowerShell), macOS, Linux (dash as `/bin/sh`) | Hook launchers stay POSIX `sh`. Assembly runs in Python through the existing `bin/py.sh`. No bash syntax. | `rules_start.sh:11-12`, `CONTRIBUTING.md:26` |
| Hook output cap of 10,000 characters per handler, CR bytes counted | Assembled text is split into parts of at most 9,000 bytes written with LF. Each part is its own handler. | `CLAUDE.md:33`, `rules_extra.sh:15` |
| No paid API | Tests and CI run against `plugins/rigger-rules/tests/stub_api.py`. Slot contracts are checked by code, not a model. Real-model evals run on plan usage with `--no-publish`. | `CLAUDE.md:37,41` |
| Read once at session start | Config and manifests are read at `startup` and `clear`. The resolved result is saved and reused on `compact` and by later hooks and the panel (§9.4). The cost lands once per session, inside the 10 s handler timeout. | `rigger-rules/hooks/hooks.json:5-31` |
| A plugin never reaches a sibling by relative path | Core finds modules through `plugins/installed_plugins.json` `installPath`. | `CLAUDE.md:35` |
| `installPath` of a directory marketplace is a stale cache snapshot | The assembler must not read a dev checkout's rules from a stale snapshot. Spike S3. | `CLAUDE.md:28` |
| Plugin hooks are static `hooks.json` | Config cannot move a hook from one plugin to another. Hook-backed slots are filled by enabling the plugin that fills them (§10.2). | `rigger-core/hooks/hooks.json` |
| Rules text ceiling 27,681 bytes across `*/rules/*.md` | The ceiling test moves from "files under `rules/`" to "assembled general layer" (§13, P1), rendered with default options: 27,708 bytes for the same text. | `test_plugin_rules.py:31,309-314` |
| Everything under `plugins/` is public | Example modules in this document are generic: hosting-provider deploy, ticket intake, accessibility audit. | `CLAUDE.md:3-5` |

## 4. How Rigger works today: the hook points

| Concern | Today | Where the design hooks in |
|---|---|---|
| Rules injection | One `SessionStart` handler per rules file, prints `Plugin root: <root>` then the file. `SubagentStart` prints the same as JSON `additionalContext`. | `rigger-rules/rules/rules_start.sh:145-160`, `rigger-rules/hooks/hooks.json:7-18,37-39`. The script exists as 6 byte-identical copies, one per plugin with rules. Today there are 19 `SessionStart` rules handlers across those plugins. |
| Per-plugin rules | Each plugin's `hooks.json` lists its files. | `rigger-core/hooks/hooks.json:27`, `rigger-lessons/hooks/hooks.json:18,29`, `rigger-windows/hooks/hooks.json:7-8,15-16`, `rigger-wp/hooks/hooks.json`, `rigger-talk/hooks/talk_start.sh:22` |
| Personal rules | `<config dir>/rigger/rules.d/*.md`, byte order, `*-main.md` main-only, 9,000-byte cap. | `rigger-rules/rules/rules_extra.sh:15,31,78,101`, `rigger-rules/hooks/hooks.json:30,40` |
| Rule removal for experiments | `HARNESS_RULES_DROP` by basename or `## ` heading. | `rules_start.sh:60-83`, `rigger-rules/README.md:47-65` |
| Template variables | `--var NAME=VALUE`, fed from `CLAUDE_PLUGIN_OPTION_*` of the plugin's own `userConfig`. | `rules_start.sh:111-126`, `rigger-talk/hooks/talk_start.sh:7-9`, `rigger-talk/.claude-plugin/plugin.json:10` |
| Skill-trigger tables | Three separate tables with literal skill names. | `rigger-rules/rules/skills.md:1-15`, `rigger-windows/rules/windows-skills.md`, `rigger-wp/rules/wp-skills.md` |
| Skill names in other rules | Git heading, plans, safe-edit. | `rigger-rules/rules/git.md:1`, `plans-main.md:3-4`, `tests.md:13`, `defects.md:37` |
| Subagents without rules | Hard-coded list `Explore Plan rigger-core:implementer rigger-core:mapper`. | `rules_start.sh:87`, `rules_extra.sh:24` |
| Agents | implementer (sonnet, `omitClaudeMd`), mapper (haiku, `omitClaudeMd`), worker, reviewer. Their reply formats are de facto contracts. | `rigger-core/agents/implementer.md:4,8,33-43`, `mapper.md:4,8,14,18`, `worker.md:8-12`, `reviewer.md:10-18` |
| Orchestrator | `/rigger-core:coordinate` and `orchestrating-subagents` name agents and skills. | `commands/coordinate.md:16,49,62`, `orchestrating-subagents/SKILL.md:89-93,122,140,147` |
| State file | Main-session rule, under 9,000 characters. | `rigger-core/rules/state-file.md:1-16` |
| Plugin dependencies | Only `rigger-rules` declares one (`rigger-core`). | `rigger-rules/.claude-plugin/plugin.json:10-12` |
| Installed-plugin reading | `harness_start.py` reads user settings and `installed_plugins.json` to find duplicate handlers and heading overlaps. | `rigger-core/tools/harness_start/harness_start.py:1-20` |
| Lessons routing | Fixed table: `global` → `rules.d/lessons.md`, `skill:` → issue draft, `project:` → CLAUDE.md. Public-repo issue drafts are a sentence, not a target. | `rigger-lessons/skills/curate-lessons/SKILL.md:49-55`, `rigger-lessons/rules/lessons.md:39-45` |
| Leak check | `scripts/sweep.py` (repo-only, not shipped). Denylist by flag or `HARNESS_SWEEP_DENYLIST`. `gate.py --release` plants a denylist term and requires one finding. | `scripts/sweep.py:21-25,60`, `scripts/gate.py:10-12,328` |
| Test selection | `select_tests.py` maps a change to test files by imports and names. `run_tests.py --fix-round` uses it and is never a gate. | `scripts/select_tests.py:1-11,19,27`, `scripts/run_tests.py:39-42` |
| Panel | Function-hook plugin with four panes, reads files through `$.fs.read`, no writes. | `rigger-panel/hooks/register.tsx:49-54,114-117,168`, `hooks/config-dir.ts:7-12` |
| Wiring tests | Pin today's handlers, tables and sizes. | `test_plugin_rules.py:77,196,290,309`, `tests/test_repo.py:336,379` |
| Windows pack | Shell guard and shadow gates, 3 skills, 5 tools, 5 evals, 2 rules files. | `plugins/rigger-windows/**`, `rigger-windows/tools/shell-guard/INSTALL.md:190-206` |

## 5. Approaches considered

### Decision 1: what a module is

| | Approach | Verdict |
|---|---|---|
| **A** | **A Claude Code plugin plus a `rigger-module.json` manifest.** Claude Code installs, updates and versions it. Private modules live in their own marketplace. A light `<config>/rigger/` folder layer covers single files without a plugin. | **Picked.** It uses the install path users already have. The manifest says what folders cannot: slot, layer, trigger rows, tests. |
| B | Rigger's own module format: folders under `<config>/rigger/modules/`, installed and updated by a Rigger command. | Rejected. It is a second plugin manager with its own versioning, trust and update story, and it duplicates Claude Code. |
| C | Plain plugins with conventions only: no manifest, layer and triggers inferred from folder names (`rules/*.md`, `skills/*`). | Rejected. Folders cannot express a slot, a rule's layer, a trigger row's wording or which tests a module owns. Inference would move decisions into heuristics. |

### Decision 6: how to deliver it

| | Approach | Verdict |
|---|---|---|
| **A** | **Mechanism first, in phases, one release each:** (1) manifest, assembly and config with no behaviour change; (2) slots and contracts; (3) split modules; (4) company overlay out; (5) panel Settings. | **Picked.** Each release is small enough to verify. Phase 1 is provable by byte equality. Later phases move content onto a mechanism that already shipped. |
| B | One big release that introduces the mechanism, splits the modules and moves the overlay together. | Rejected. No release proves "same behaviour", and a regression cannot be pinned to the mechanism or to the move. |
| C | Modules first: split rigger-review, rigger-testing and the Windows items now, then build assembly. | Rejected. Without assembly, every split hard-codes the new names in rules, skills and tests, which are then rewritten again in the mechanism phase. |

## 6. The module manifest

### 6.1 Location and discovery

- **Location:** `rigger-module.json` sits at the plugin root, next to `.claude-plugin/`.
  `.claude-plugin/plugin.json` stays Claude Code's and gains nothing Rigger-specific.
- **Discovery:** at session start, core reads `<config dir>/plugins/installed_plugins.json` and the
  `enabledPlugins` maps of every settings scope Claude Code reads. It keeps the enabled plugins
  whose `installPath` holds `rigger-module.json`. `harness_start.py:1-20` already reads user
  settings and `installed_plugins.json` this way.
- **Modules loaded with `--plugin-dir`:** they appear in neither file, so they are named
  explicitly. `RIGGER_MODULE_DIRS` is a list of plugin roots separated by `os.pathsep` (`;` on
  Windows, `:` elsewhere), each holding `rigger-module.json`. The stub tests, the evals
  (`scripts/eval_stage.py` copies) and developers set it next to `--plugin-dir`.
  - **Discovery is installed and enabled plugins plus `RIGGER_MODULE_DIRS`.**
  - An entry that is not a directory, or holds neither a manifest nor `.claude-plugin/plugin.json`,
    prints one notice line and is skipped. An entry with a `plugin.json` and no manifest counts as an
    enabled plugin (its `plugin.json` name), with no notice.
  - A name present both installed and in `RIGGER_MODULE_DIRS` is taken from `RIGGER_MODULE_DIRS`,
    the same way `--plugin-dir` takes precedence for that session, and a notice names it.
  - The variable is read like every other input: once, at `startup` or `clear`.
- **Reading, not managing:** a plugin without a manifest is not a module. Superpowers is one. Its
  skills can still be named as slot targets or trigger skills, and core checks only that the plugin
  is enabled.
- **Config-folder module:** `<config dir>/rigger/` (`CLAUDE_CONFIG_DIR`, else `~/.claude`) counts as
  one more module named `personal`:
  - `rules.d/*.md` are its personal-layer rules, as today.
  - `config.json` is its config (§7).
  - It carries no skills of its own. A single personal skill uses Claude Code's own
    `<config dir>/skills/`, and a personal trigger row may name it.

### 6.2 Schema (version 1)

```text
{
  "schema": 1,                          required; core refuses another number with one line
  "name": "<plugin name>",              required; equals plugin.json "name"
  "layer": "general" | "company",       required; "personal" is reserved for the config folder
  "fills": "<slot>" | null,             the slot this module fills by default; null = add-on
  "provides": {                         what the module offers to slots (any number)
    "<slot>[.<role>]": { "kind": "agent"|"skill"|"command"|"hook", "target": ..., "model": ... }
  },
  "rules": [                            rules files in print order
    { "file": "rules/<name>.md", "layer": "general"|"company",   optional; default: the module's layer
      "audience": "main"|"subagent"|"both",
      "requires": ["slot:<slot>" | "plugin:<name>" | "os:windows"|"os:macos"|"os:linux"] }
  ],                                    requires is optional; a file whose requires is unmet is not printed
  "triggers": [                         rows of the skill-trigger table
    { "id": "<module>.<row>", "before": "<when, in words>",
      "load": ["<plugin>:<skill>" | "slot:<slot>[.<role>]", ...],
      "section": "<rule id of the table it belongs to>",      phase 1 only (§9.3)
      "requires": [...] }
  ],
  "subagents": { "<agent name>": { "rules": "none" | "all" } },   default: from frontmatter (§9.5)
  "config": { ... },                    level-2 defaults; honoured only when layer = "company" (§7)
  "lessons": { "target": true, "repo": "<where proposals go, in words or a URL>",
               "denylist": "<path relative to the plugin root>" },  company modules (§11)
  "tests": {
    "paths": ["tests/", "tools/*/tests/"],                   relative to the plugin root
    "contract": ["<slot>"]                                   slots whose contract fixtures it ships
  }
}
```

`os:` is read from Python's `sys.platform`: `win32` is `windows`, `darwin` is `macos`, and anything
else is `linux`. WSL is `linux`, which matches "native Windows only" in rigger-windows today.

**Layer per rule: my reading of decision 1.** The decision says a manifest declares "the rules it
adds with their layer". So `layer` is accepted per rules entry, and the module's own `layer` is that
entry's default. An entry may not claim a layer above its module's: a `general` module cannot ship a
`company` rule. The module-level `layer` also decides whether its `config` block counts as level 2.

Validation is code, run by the session-start assembler and by the contract test:

- `name` matches `plugin.json`.
- Every `file` exists and every `load` resolves.
- `layer: company` is refused from a module in the public `rigger` marketplace.
- `config` from a `general` module other than core is refused. Level 1 is core's alone.
- `triggers[].id` values are unique.

At session start, an invalid manifest prints one line `rigger: module <name> skipped: <reason>`
and its rules are not printed. A broken module never half-loads.

### 6.3 Examples

The public reviewer module (phase 3):

```json
{
  "schema": 1,
  "name": "rigger-review",
  "layer": "general",
  "fills": "reviewer",
  "provides": {
    "reviewer": { "kind": "agent", "target": "rigger-REVIEW:reviewer", "model": "by-rule" }
  },
  "rules": [],
  "triggers": [
    { "id": "rigger-review.review",
      "before": "reviewing a change, a branch or a PR, or verifying an agent's claim",
      "load": ["rigger-REVIEW:review"] }
  ],
  "tests": { "paths": ["tests/", "tools/*/tests/"], "contract": ["reviewer"] }
}
```

A company module in a private module marketplace, adding a hosting-provider deploy skill and
replacing one general rule section:

```json
{
  "schema": 1,
  "name": "company-deploy",
  "layer": "company",
  "fills": null,
  "rules": [
    { "file": "rules/environment.md", "audience": "main" },
    { "file": "rules/deploy.md", "audience": "both" }
  ],
  "triggers": [
    { "id": "company-deploy.deploy",
      "before": "an upload, deploy or SSH session on the hosting provider",
      "load": ["company-deploy:hosting-deploy"] }
  ],
  "config": { "slots": { "reviewer": { "model": "opus" } } },
  "lessons": { "target": true, "repo": "the company module repository",
               "denylist": "leak/denylist.txt" },
  "tests": { "paths": ["tests/"], "contract": [] }
}
```

Two things follow from that example:

- Its `rules/environment.md` has the same `## Working environment` heading as rigger-rules'
  `environment.md`, so it **replaces** section `environment.working-environment` (§8).
- Its `deploy.md` has a new stem, so its sections are **added**.

A ticket-intake add-on and an accessibility-audit add-on have the same shape: `fills: null`, a
skill and one trigger row each, and rules only where they change behaviour.

## 7. Config

### 7.1 Levels and files

| Level | File | Who writes it | Shared by |
|---|---|---|---|
| 1. Rigger defaults | `plugins/rigger-core/rigger-defaults.json` | Rigger releases | everyone |
| 2. Company-module defaults | the `config` block of each enabled `layer: company` manifest | the company module | everyone who installs that module |
| 3. Personal | `<config dir>/rigger/config.json` | the person (by hand, or the panel in phase 5) | one machine |
| 4. Project | `<repo>/.claude/rigger.json` | the team, in the repository | everyone who clones it |

A higher level overrides a lower one by key. With no level-3 or level-4 file and no company module,
the effective config is level 1. Level 1 reproduces today's behaviour exactly (P1 exit criterion).

Module enable and disable is not a config key at any level. That is Claude Code's job (user,
project and local `enabledPlugins`).

**My reading of "per-stack modules" in decision 5:** `.claude/rigger.json` may list `expects`, the
modules a project needs. Core and `/rigger-core:doctor` report one missing or disabled module per
line, with the `claude plugin install` command. The team turns modules on per project through
Claude Code's project-scope `enabledPlugins` in `.claude/settings.json`.

### 7.2 Keys (schema 1)

| Key | Type | Levels | Meaning |
|---|---|---|---|
| `schema` | 1 | all | required in every file; another number makes that file count as absent, with one notice line |
| `slots.<slot>` | object `{kind, target, model}`, any field optional | 1–4 | slot filler; fields merge one by one (§7.3) |
| `slots.<slot>.<role>` | same | 1–4 | for slots with roles (`process.tdd`) |
| `triggers.<id>` | object `{before, load, requires}` or `null` | 1–4 | adds a row, replaces the row with that id, or `null` removes it |
| `expects` | list of plugin names | 4 | modules this project needs (reported, never installed) |
| `company` | module name | 4 | marks the project as that company's: lessons from it carry client context (§11.3) |
| `leak_guard.denylist` | path | 3 | the person's own denylist file, outside any repository |
| `trust.project_commands` | list of slot names | 3 | slots whose `kind: command` a project file may set (§7.4) |
| `review.paths` | object `{glob: rules file}` or `{glob: {rules, skip}}` (at least one key) | 1–4 | per-path review rules and named skips for rigger-review (phase 3). A rules file resolves against the module root (level 2), the personal config file's folder (level 3) or the repository root (level 4). Project entries and their rules files are read at the reviewed diff's base by `review_scope.py`, which hands the rules over as files (`--rules-out`) |
| `shell_guard.shellwise_path` | path | 3 | the shellwise binary for `rigger-shellwise` when it is not on PATH (phase 3); a project file cannot set it, for the reason in §7.4 |

Unknown keys print one notice line naming the file and key, and are ignored. A value of the wrong
type is ignored at that level with a notice, and the lower level's value stands.

### 7.3 Precedence algorithm

```text
inputs: L1 = rigger-defaults.json
        L2 = config blocks of enabled company modules, each tagged with its module name
        L3 = personal config.json      (absent = {})
        L4 = project .claude/rigger.json (absent = {})

1. Parse each file as UTF-8 JSON. On a parse error, the file counts as absent and prints one line
   "rigger: config <path> ignored: <reason>".
2. Merge the L2 inputs into one L2 object. Two company modules that set the same leaf key is a
   conflict: neither value applies, L1's value stands, one line names both modules and the key.
   (No "last wins": module order is not a meaning a person chose.)
3. effective = L1
   for level in (L2, L3, L4):
       effective = merge(effective, level)

merge(base, over):
   for key, value in over:
       if value is null:                     remove key from base
       elif value is object and base[key] is object:
                                             base[key] = merge(base[key], value)
       else:                                 base[key] = value     (scalars and lists replace whole)
   return base

4. Apply the trust filter (§7.4) to slots set at L4.
5. Validate slots (§10): a slot whose effective entry fails validation falls back to the highest
   lower level whose entry validates, with one notice line. If none validates, the slot is empty
   and the line says so. A level-1 default whose plugin is not enabled leaves its slot empty with
   no notice (doctor lists it as INFO), except for `reviewer` and `testing`: their slot gets
   `rigger: slot <path> is empty: install <plugin> (claude plugin install <plugin>@<marketplace>)`
   (for `testing` followed by `; the commit-time weak-test guard is off`) and a doctor `MISSING`
   row. A fallback from a level above 1 keeps its one notice; when it lands on such a default whose
   plugin is not enabled, the notice ends with `; install <plugin> (<command>)`. A
   `null` for one slot empties it, except a hook slot, which stays filled by its plugins' hooks. A
   `null` for `slots` or for the `process` group above level 1 is ignored with a notice.
6. Record, for every leaf key, the level and file it came from (the panel shows this).
```

Lists replace whole, because a merged list cannot be undone from a higher level without per-item
ids. Whatever needs per-item merging is an object keyed by id. The trigger table is one.

### 7.4 Trust boundary for project files

A project file arrives with a `git clone`. It may:

- name agent and skill targets;
- add trigger rows;
- set `model`;
- list `expects`.

It may not set `kind` or `target` of a slot whose entry merged with the L4 value is a command
unless the person's level-3 file lists that slot under `trust.project_commands`: fields merge one by
one, so a `target` alone would rewrite a level-3 argv. Without that, the whole L4 value for that slot
is dropped, `model` included, with the line
`rigger: .claude/rigger.json may not set a command for slot <slot>; add it to trust.project_commands in <config dir>/rigger/config.json to allow it`.
An L4 `null` and an L4 `model` alone are allowed.

A project command in effect prints, at `startup`, `clear` and `compact`,
`rigger: slot <slot> runs the project's command <argv> … (<n> bytes, sha256:<12 hex digits>) (trusted in <config file>)`:
the argv as one line of ASCII JSON cut to 200 characters, the size and hash of the whole line.

Command targets are argv lists, run without a shell (§10.3).

### 7.5 Examples per level

Level 1, `rigger-core/rigger-defaults.json`. Phase 1 already holds `schema`, `triggers` and `slots`
with today's targets, so `{{slot:…}}` placeholders and core's slot-named trigger rows render today's
names. Phase 1 does not validate slots, render a `## Slots` section, or let levels 2–4 change a slot.
That is phase 2.

```json
{
  "schema": 1,
  "slots": {
    "reviewer":    { "kind": "agent", "target": "rigger-core:reviewer",    "model": "by-rule" },
    "worker":      { "kind": "agent", "target": "rigger-core:worker",      "model": "by-rule" },
    "implementer": { "kind": "agent", "target": "rigger-core:implementer", "model": "pinned" },
    "mapper":      { "kind": "agent", "target": "rigger-core:mapper",      "model": "pinned" },
    "testing":     { "kind": "skill", "target": "rigger-core:testing" },
    "shell-guard": { "kind": "hook",  "target": "rigger-windows", "requires": ["os:windows"] },
    "lessons":     { "kind": "hook",  "target": "rigger-lessons" },
    "process": {
      "brainstorm": { "kind": "skill", "target": "superpowers:brainstorming" },
      "plans":      { "kind": "skill", "target": "superpowers:writing-plans" },
      "tdd":        { "kind": "skill", "target": "superpowers:test-driven-development" },
      "debug":      { "kind": "skill", "target": "superpowers:systematic-debugging" }
    }
  },
  "triggers": {
    "rigger-core.testing": {
      "section": "skills.skill-triggers",
      "before": "writing code or tests, reviewing tests, a spike, port or rehearsal",
      "load": ["slot:testing", "slot:process.tdd"] },
    "rigger-core.debugging": {
      "section": "skills.skill-triggers",
      "before": "a bug, a failing test or unexpected behaviour",
      "load": ["slot:process.debug"] }
  }
}
```

Core's rows carry `section` like module rows (§9.3). In phase 1 it places each row into the
rigger-rules `## Skill triggers` table (`skills.md:6-15`) in today's order. From phase 2 all tables
are merged and `section` is ignored; the key stays valid so phase-1 files keep loading.

In phase 3 the defaults change to `rigger-REVIEW:reviewer`, `rigger-TESTING:testing` and
`rigger-shellwise`. The `os:windows` condition on `shell-guard` goes at the same time: until then, a macOS
or Linux session does not get a "slot not filled" notice for a guard that never existed there.

Level 2, inside a company module's manifest:

```json
{ "config": { "slots": { "reviewer": { "model": "opus" } },
              "triggers": { "company-intake.ticket": {
                  "before": "a new ticket or a client request arrives",
                  "load": ["company-intake:ticket-intake"] } } } }
```

Level 3, `<config dir>/rigger/config.json`:

```json
{
  "schema": 1,
  "leak_guard": { "denylist": "~/private/rigger-denylist.txt" },
  "trust": { "project_commands": ["reviewer"] },
  "triggers": { "personal.notes": { "before": "writing meeting notes", "load": ["notes-style"] } }
}
```

Level 4, `<repo>/.claude/rigger.json`:

```json
{
  "schema": 1,
  "company": "company-deploy",
  "expects": ["rigger-wp", "company-deploy", "a11y-audit"],
  "slots": { "reviewer": { "kind": "command",
                           "target": ["review-cli", "--format", "rigger-json", "--base", "{base}", "--out", "{out}"] } },
  "triggers": { "a11y-audit.audit": { "before": "a template, form or component change", "load": ["a11y-audit:audit"] },
                "rigger-core.e2e": null }
}
```

The reviewer command applies only because the level-3 example trusts `reviewer`.
`"rigger-core.e2e": null` removes a row that does not fit this stack.

## 8. Rule layers and section ids

### 8.1 Section ids

- **Sections:** a rules file is split at lines starting `## `, the same test `rules_start.sh:132-136`
  uses. Text before the first heading is the file's preamble. `windows.md:1` has one.
- **Id:** each section's id is `<file stem>.<heading slug>`, and a preamble's id is
  `<file stem>._preamble`.
- **Slug:** take the heading text after `## `, then cut it at the first ` (`, `:`, ` — ` or ` - `.
  Fold it to ASCII (NFD, marks removed), lowercase it, and turn each run of characters outside
  `[a-z0-9]` into one `-`, trimming `-` at both ends.

Today's ids:

| File | Heading | Id |
|---|---|---|
| `rigger-rules/rules/git.md:1` | `## Git (procedure and naming: …)` | `git.git` |
| `rigger-rules/rules/tests.md:1` | `## Tests` | `tests.tests` |
| `rigger-rules/rules/plans-main.md:1` | `## Plans: main session` | `plans-main.plans` |
| `rigger-rules/rules/defects.md:1` | `## Recurring defect classes — check while writing, not after review` | `defects.recurring-defect-classes` |
| `rigger-talk/rules/talk.md:1` | `## Talking to me` | `talk.talking-to-me` |
| `rigger-windows/rules/windows.md:1-3` | preamble, then `## Windows shell` | `windows._preamble`, `windows.windows-shell` |

**Granularity: my reading of decision 3.** The owner's example `talk.language` assumes a finer
heading than today's: `talk.md` has one section. Phase 1 keeps today's headings, so ids come out
coarse and the output stays identical. Splitting a file into finer sections (`## Language`,
`## Length`) is a content PR after phase 1.

Renaming a heading changes its id. An override whose id matches no lower section is an orphan, and
the check fails on it (§8.4). A rename then breaks a test, not a session.

### 8.2 Layers

| Layer | Sources | Position |
|---|---|---|
| general | rules of enabled `layer: general` modules (rigger-core, rigger-rules, rigger-lessons, rigger-talk, rigger-wp, …) | first, in module order (core first, then the order `marketplace.json` lists them, then other general modules by name) |
| company | rules of enabled `layer: company` modules | replaces general sections in place; new sections follow the general ones |
| personal | `<config dir>/rigger/rules.d/*.md`, byte order as today | replaces general or company sections in place; new sections last |

Other behaviour:

- **Empty replacement:** a section in a higher layer whose body is empty, holding only its heading,
  replaces the lower one with nothing. Heading included, it disappears. That is how a person drops a
  general rule, without a separate config key.
- **Audience:** `*-main.md` in `rules.d` stays main-only. Module rules declare `audience`. A
  replacement inherits the audience of the section it replaces unless its own file says otherwise.
- **Experiments:** `HARNESS_RULES_DROP` keeps working on basenames and `## ` heading lines, and also
  accepts ids. It applies last, to every module layer; personal text in `rules.d` is never
  dropped by it, as today.

### 8.3 Resolution

```text
sections = []                                   ordered list of (id, layer, module, file, text, audience)
for module in general modules, in order:
    for section in module's rules files, in file order:
        if id already in sections from another general module: duplicate (below)
        else append
for layer in (company, personal):
    for module in that layer, in order:
        for section in its files:
            if id in sections from a lower layer: replace that entry's text, layer, module, file in place
            elif id in sections from this same layer: duplicate
            else append
apply HARNESS_RULES_DROP
drop sections whose file-level or row-level "requires" is unmet
render placeholders (§9.2), then render the trigger sections (§9.3)
```

A duplicate is two sections with one id in one layer:

- **At session start:** the entry from the module that sorts first stays, and the session gets one
  line `rigger: rule id <id> in <module A> and <module B>; kept <module A>`.
- **At check time:** the contract test (§12) and `/rigger-core:doctor` fail on it.

The code decides; the model never sees both texts.

### 8.4 The check

`rigger-core/tools/assemble/assemble.py --check [--modules <dir>...]` fails (exit 1, one line per
finding) on:

- a duplicate id in one layer;
- an orphan override;
- a section over 9,000 bytes;
- an unknown placeholder;
- a trigger `load` that resolves to nothing when every module of the repository is enabled;
- `layer: company` in the public marketplace.

It also refuses to report "clean" when it read 0 rules files.

It runs in the contract test for this repository, and private module marketplaces run it against
their own modules plus an installed Rigger.

## 9. Assembly at session start

### 9.1 Mechanism

- **Owner:** the assembler is fixed core, so it lives in `rigger-core/tools/assemble/assemble.py`.
  rigger-rules keeps its rules files as the main general module and loses its hook handlers for
  rules. `rules_extra.sh` is folded into the assembler as the personal layer.
- **Dependencies:** every module that ships rules declares `"dependencies": ["rigger-core"]` in
  `plugin.json`, as rigger-rules already does at `plugin.json:10-12`.
- **Handlers:** `rigger-core/hooks/hooks.json` gets `K` fixed part handlers per event:
  `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S ".../assemble.py" --part <i> [--subagent]`, i = 1..K.
  K starts at 6 for `SessionStart` (today's rules text is 27,681 bytes plus up to 9,000 of
  `rules.d`) and 4 for `SubagentStart`.
- **Parts:** each part handler computes the whole assembly, which is deterministic and reads about
  30 small files, then prints only its part. Parts cut at section boundaries and are filled in
  order up to 9,000 bytes each, written with LF.
- **Overflow:** if the text needs more than K parts, the last part ends with
  `rigger: rules left out over the hook cap: <ids>`, as `rules_extra.sh:101` does today, and the
  check fails.
- **Root lines:** a module's first printed section is preceded by `Plugin root: <its root>`. That
  keeps today's root lines (`rules_start.sh:151`) that `windows.md:1` and `wp-skills.md:3` rely on.
- **Python:** with no Python, `py.sh --notice` prints its notice. Part 1 then prints
  `Rigger rules not loaded: Python 3 not found (run /rigger-core:doctor)` and the other parts print
  nothing. That is a visible failure, not an empty success. rigger-core's other hooks already need
  Python.

**Assumptions, each proved by a phase-1 spike before any code depends on it:**

| # | Assumption | Spike and pass condition |
|---|---|---|
| S1 | Several `SessionStart` handlers of one group run independently and their texts all reach the context. The context order follows `hooks.json` order, or order does not matter because every part is self-contained. | Stub run (`stub_api.py`, isolated config) with K handlers printing distinct markers. The first request holds all K, and their order is recorded. If the order is not stable, each part repeats the module root line it needs, and the golden test compares per-part text. |
| S2 | `CLAUDE_PLUGIN_OPTION_*` of rigger-talk does not reach a rigger-core hook. Where Claude Code stores the option values is to be found (`talk_start.sh:3-5` says unset options are not exported). | Stub run that prints the environment of a rigger-core hook with a talk option set. If the values do not arrive, the fallback is §9.6. |
| S3 | For a directory marketplace, `installPath` lags behind the checkout. | Install from a directory marketplace, edit a rules file, start a session. Compare what `installPath` holds with the checkout. If it lags, the assembler reads a directory marketplace's modules from the marketplace's `source` path. |
| S4 | The settings scopes and their `enabledPlugins` maps are user `settings.json` and `settings.local.json`, project `.claude/settings.json` and `.claude/settings.local.json`, plus any managed settings. | Enable a plugin in each scope in an isolated config and compare the assembler's list with the `plugins` array of the stub's init event. Pass when they are equal in every scope. A plugin loaded with `--plugin-dir` is not compared: it is in the init list only, so the run sets `RIGGER_MODULE_DIRS` to its root, and the pass condition is "init list = installed and enabled ∪ `RIGGER_MODULE_DIRS`". |
| S5 | `SubagentStart` input carries the parent `session_id`. | A stub run reads it. If it does not, the subagent parts recompute from files instead of the snapshot. |

### 9.2 Placeholders in rules text

Rules text names no skill or agent. It names a slot or a trigger row instead:

| Placeholder | Renders as | Example use |
|---|---|---|
| `{{slot:testing}}` | `` `rigger-core:testing` `` (the slot target in backticks) | `tests.md` |
| `{{slot:process.brainstorm}}` | `` `superpowers:brainstorming` `` | none in the repository: `plans-main.md` names the Skill triggers table instead, and `--check` reports a `{{slot:…}}` whose default plugin is outside the checked modules |
| `{{skill:<plugin>:<skill>}}` | `` `<plugin>:<skill>` `` when that plugin is enabled, else the row or sentence's `requires` drops it | `git.md:1` |
| `{{tool:<plugin>:<path>}}` | `<installPath>/<path>` | `tests.md:13`, `defects.md:37` (safe-edit) |
| `{{triggers:<section id>}}` | the one merged trigger table, whatever the id (§9.3); a second such placeholder renders nothing, with a notice, and is a `--check` finding | rigger-core's `skills.md` |
| `{{var:<name>}}` | a module option (§9.6) | `talk.md:3,5,14` |

`--var` and `{{NAME}}` stay supported for one release, then go away. An unknown placeholder prints
as written and the check fails. In module rules text, a sentence that holds a `{{slot:<path>}}` of
an empty slot is left out, and a block left with no sentence goes whole; `rules.d` text is not
rendered and keeps placeholders as written.

### 9.3 Trigger-table assembly

Rows come from four places:

- every enabled module's `triggers`;
- the `triggers` key of the effective config (L1–L4, §7.3);
- core's own rows, which live in `rigger-defaults.json` and name slots;
- for phase 1 only, rows of a module whose table is its own section.

```text
rows = {}                                     ordered by first appearance
for module in modules (same order as rules):  for row in module.triggers: add(row)
for row in effective_config.triggers:         id null → delete; id present → replace whole; new → append
for each row:
    resolve each load entry:  "slot:x[.r]" → the slot's skill target; "<plugin>:<skill>" → kept
    drop the entry when its plugin is not enabled (phase 2 on; phase 1 keeps the text as today)
    drop the row when no entry is left or its requires is unmet
render: one table "| Before | Skill |", entries joined with ", then " in load order
```

- **Phase 1** keeps three tables (rigger-rules, rigger-windows, rigger-wp), each rendered into its
  own section through `section`. The table header, intro lines and row text stay byte-identical,
  and `SkillTableTests` (`test_plugin_rules.py:196`) keeps passing unchanged.
- **Phase 2** merges them into one `## Skill triggers` table and drops rows whose skill's plugin is
  not enabled. That replaces the sentence "the `superpowers:` rows apply when the superpowers
  plugin is installed" (`skills.md:3-4`) with code. The table is rigger-core's
  (`rigger-core/rules/skills.md`, moved from rigger-rules, id `skills.skill-triggers`), so a setup
  without rigger-rules still gets every row. rigger-windows' table section goes, and rigger-wp's
  section keeps its site-copy sentence under `## WordPress site copies`. A row's `section` is
  ignored but stays valid. Order: module rows in module order, then config rows.
- **Fit:** the merged table must fit one hook part. When it would not, config rows leave it, L4
  first, then L3, then L2, each level from its last row up; a dropped override brings back the row
  it replaced, and one line after the table names the count and the files. Module and level-1 rows
  always stay. A config file or company module holds at most 50 rows, and a `load` entry at most 200
  characters.

### 9.4 The resolved snapshot ("read once")

On `startup` and `clear`, part 1 writes
`<config dir>/rigger/state/<session id>.resolved.json` atomically (write to a temp name, then
rename). It holds:

- the rendered text of every part, for the main session and for subagents, so `compact` prints it
  as is;
- the module list with versions;
- the effective config with the source of every leaf;
- every section id with its layer, module and file;
- the trigger rows;
- the slot table;
- any notices.

Who reads it:

- **On `compact`:** every part re-renders from the snapshot and does not re-read files. Slots and
  rules therefore cannot change in the middle of a plan because a file changed, and the state file
  (`state-file.md`) stays the only thing that carries the plan across compaction. A missing or
  unreadable snapshot means a fresh read, with one notice line.
- **Subagent parts:** they read the parent's snapshot (S5).
- **Hooks that need a slot:** the panel, the review tools and the lessons skill read the snapshot
  and never resolve config themselves.

Snapshots older than 14 days are deleted by part 1 at startup (builder's call on the age).

### 9.5 Subagents without rules

The skip list of `rules_start.sh:87` comes from data:

- the built-in types `Explore` and `Plan`;
- any `<plugin>:<agent>` whose agent file's frontmatter has `omitClaudeMd: true`
  (`implementer.md:8`, `mapper.md:8`);
- any agent a manifest lists with `"rules": "none"`.

An agent from a non-module plugin gets the rules, as today.

### 9.6 Module options (`{{var:…}}`)

**Default, if S2 shows the values are readable:** the assembler reads a module's option values,
read-only, from where Claude Code stores them. It falls back to the `userConfig` default in that
module's `plugin.json`, as `talk_start.sh:7-20` does.

**Fallback, if they are not readable:** the module keeps a single `SessionStart` handler of its own
that writes its resolved options to
`<config dir>/rigger/state/<session id>.options.<module>.json`. The assembler waits up to 1 s for
that file, then uses the defaults with a notice. That race is the reason the default is preferred.

## 10. Slots and contracts

### 10.1 Slot entries

A slot entry is `{kind, target, model}`:

| Field | Values |
|---|---|
| `kind` | `agent` (a subagent type), `skill` (a skill to load), `command` (an argv run by core), `hook` (an enabled plugin whose static hooks do the work). **My reading of decision 2:** the decision names three kinds, and `hook` is added because the shell-guard and lessons slots are filled by hooks that no config can move (§10.2). It is the "command" kind as Claude Code runs it, not a fourth mechanism. |
| `target` | `<plugin>:<agent>`, `<plugin>:<skill>`, an argv list with `{base}`, `{head}`, `{out}`, `{scope}` placeholders, or a plugin name |
| `model` | a model alias passed to the dispatch, `pinned` (the agent's frontmatter decides, as for implementer and mapper), or `by-rule` (the model rule of `orchestrating-subagents/SKILL.md:101-103` decides). Ignored for `skill` and `hook`. |

The assembler renders a `## Slots` section for the main session, with one row per slot: slot,
kind, target, model, source level. The orchestrator text points to it:

- **Phase 2 rewording:**
  - `orchestrating-subagents/SKILL.md:89-93` becomes "Agent type: the `worker` slot for
    implementation, the `reviewer` slot for review (table `## Slots`)".
  - `worker.md:8-10` becomes "Load the testing skill and the process skills the Skill triggers
    table names".
- **Why the agents are reworded, not templated:** agents and skills are static files that Claude
  Code reads directly, so they cannot hold placeholders. They refer to the injected tables instead.
  Subagents get the trigger table already (`rigger-rules/hooks/hooks.json:39`).

### 10.2 Hook-backed slots

Config cannot move a hook from one plugin to another: hooks are static `hooks.json`. So a `hook`
slot (shell-guard, lessons) is filled by whichever enabled plugin declares `fills: <slot>`:

- **Config:** the slot entry is only an expectation. When it is set above level 1 and the expected
  plugin is not enabled, the session gets one notice, and doctor names the install command. A
  level-1 default that is not enabled gives no notice.
- **Two fillers enabled:** both run, because Claude Code runs both. The assembler prints
  `rigger: slot shell-guard is filled by A and B; disable one with claude plugin disable <name>`
  as a notice at `startup`, `clear` and `compact` until one is disabled; `harness_start.py` is not
  changed. When the fillers declare both `open` and `closed`, the stricter mode wins and the row
  reads `failure mode: closed (fillers disagree)`.
- **No filler:** the slot is empty. Rows and rules with `requires: ["slot:<slot>"]` drop.
- **Turning lessons off: my reading of decision 2.** It means disabling the rigger-lessons plugin in
  Claude Code. The `lessons` slot is then empty, and every rule sentence that names the inbox is
  dropped by its `requires`. For example `orchestrating-subagents/SKILL.md:122` gets a conditional
  wording, since skills cannot drop lines. There is no Rigger switch.

The weak-test guard (`rigger-core/hooks/hooks.json:79,85`) is a hook of the testing slot's module.
It moves with it in phase 3.

### 10.3 Command-kind slots

`rigger-core/tools/slot-run/slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]`
runs a command slot. There is no `--out`: the tool picks the output file. The session id comes on
the command line because a shell tool process may not know which session started it, and `--base`
is required, with no default. The `## Slots` line that shows the call is one line for the Bash tool,
`sh '<core root>/bin/py.sh' '<core root>/tools/slot-run/slot_run.py' <slot> --session <id> --base <base revision>`;
the PowerShell tool finds no `sh` to run it.

- **How it runs:** the argv comes from the session's snapshot only (a missing snapshot, one of
  another format and an untrusted project command are refused), placeholders are filled, and it
  runs with no shell, its cwd set to the repo root. `{scope}` names a file of
  `<verdict><TAB><path>` lines from `review_scope.py`. A verdict may read `review: binary added`;
  every verdict that starts with `review` is a path to review.
- **The program:** found by the tool's own lookup, never the operating system's: a bare name on the
  absolute `PATH` entries outside the repository and the working directory (`.exe` and `.com` on
  Windows), or an absolute path; the result must lie outside both. A `.cmd` or `.bat` file is
  refused, because `cmd.exe` re-parses its arguments.
- **Limits:** 900 s, 1,000,000 bytes of console output read from one pipe, a 5,000,000-byte output
  file; environment stripped of `CLAUDE_*`, `HARNESS_*`, `GIT_*` and `RIGGER_MODULE_DIRS`
  (`ANTHROPIC_*` stays, for a tool with its own key). A process the command started that still
  holds the console 5 s after the command exits gives `child process left running`, and the whole
  process tree is stopped after every run.
- **Contract check:** it validates the output against the slot's contract (`slot_check.py`) and
  prints one last line `slot-run: ok <slot> <n> findings` or `slot-run: refused: <reason>`, after
  a `slot-run: output <file>` line once the output folder exists.
- **Failure:** a refused or timed-out command is a failed review. The orchestrator handles it like
  a `STATUS: BLOCKED` report (`orchestrating-subagents/SKILL.md:117-120`), never as "no findings".

### 10.4 Slot list and contracts

| Slot | Default (phase 2 → phase 3) | Kinds allowed | Output contract (checked by `slot_check.py <slot> <file>`) |
|---|---|---|---|
| `reviewer` | `rigger-core:reviewer` → `rigger-REVIEW:reviewer`, `by-rule` | agent, command | §10.5 |
| `worker` | `rigger-core:worker`, `by-rule` | agent | First line `STATUS: DONE\|PARTIAL\|BLOCKED\|NEEDS_CONTEXT`, then `## Summary` with 3–5 bullets, then the brief's format (`worker.md:11-12`). |
| `implementer` | `rigger-core:implementer`, `pinned` | agent | `STATUS`, `FILES`, `ROOT CAUSE`, `GATE`, `RED RUNS`, optional `QUESTION`; with BLOCKED or NEEDS_CONTEXT also `CALL`, `OUTPUT`, `ALTERNATIVE` (`implementer.md:33-43`). |
| `mapper` | `rigger-core:mapper`, `pinned` | agent | Only lines `path:START[-END] - text` (at most 12 words) or one `NOT FOUND: <terms>` (`mapper.md:14,18`). Each path exists at the reviewed commit. |
| `testing` | `rigger-core:testing` → `rigger-TESTING:testing` | skill | The skill exists in an enabled plugin. Every trigger row that loads `process.tdd` loads `testing` first (today `TestingSkillTriggerTests`, `test_plugin_rules.py:290`, made slot-aware; `--check` and a doctor `slot-order` row report a row that breaks it). From phase 3, the default filler ships the commit-time weak-test guard: `tests/test_module_contracts.py` checks that its `hooks.json` has a `PreToolUse` handler on `Bash` with `if` exactly `Bash(*commit*)` and one on `PowerShell` with `if` exactly `PowerShell(*commit*)`, each running `weak_test_guard.py`. A filler chosen in config is not checked for it. |
| `shell-guard` | `rigger-windows` → `rigger-shellwise` | hook | (a) A PreToolUse hook on `Bash` and `PowerShell`. It prints nothing to allow, may add a non-blocking note or warning the agent reads while the command runs (shellwise `warn`), or prints one JSON deny whose reason names a rule id in square brackets (`[<rule id>]`; rigger-windows' reason starts `shell-guard [<rule>]: `) and a fix. It always exits 0 (`tests/test_repo.py:336`), because an exit 2 from a crash blocks the tools needed to fix it (`CLAUDE.md:32`). (b) The filler declares `"failure_mode": "open"` or `"closed"` in its manifest: whether a command is allowed or denied when the guard cannot finish its analysis. Core prints the declared mode in the `## Slots` row and doctor reports it. A filler without the key is reported as `failure mode: undeclared`; fillers that declare both modes read `failure mode: closed (fillers disagree)`. rigger-windows declares `open`, and `rigger-shellwise` declares `closed` (shellwise answers `danger` when analysis cannot finish). (c) From phase 3: median added latency on a command that matches no rule is under 50 ms on each OS, measured as `rigger-lessons/rules/lessons.md:46-51` asks of any process guard. (d) **Optional:** a rule checker, a Python module with `RULES` and `rule_guard_problem` as `lessons_lint.py --rule-checker` loads it (`lessons_lint.py:217-241`), named by `provides.shell-guard.rule_checker` in the filler's manifest. rigger-windows' default passes it with today's `lessons_guard.py`. A filler without one passes the contract. Each `live:<rule>` lesson is then reported `no checker` by doctor and curation, and is never dropped. |
| `process.brainstorm`, `.plans`, `.tdd`, `.debug` | superpowers skills | skill | The skill exists in an enabled plugin. An empty role drops its trigger entries and its `{{slot:…}}` sentences in module rules text (§9.2); no shipped rules sentence names one. |
| `lessons` | `rigger-lessons` | hook | (a) A `SessionStart` line naming the inbox path and its open count (the panel reads the inbox, `rigger-panel/README.md:23`). (b) Inbox entries pass `inbox_lint.py`. (c) Its curation skill reads lesson targets from the snapshot (§11). |

Every contract has fixtures in its module's tests (`tests.contract`): one passing reply, plus one
failing reply per rule of the contract, each failing on its own line. Those fixtures are the
seconds-long contract test of §12.

### 10.5 Reviewer contract (`rigger.reviewer/1`)

The reviewer contract is the one an external tool must meet, so it is strict and machine-checked.
It carries what the fix-round rules rely on today:

- severity and `Pre-existing` (`reviewer.md:10-12`);
- the `SHORTCUTS` block (`reviewer.md:13-16`, used at `orchestrating-subagents/SKILL.md:140`).

```json
{
  "contract": "rigger.reviewer/1",
  "status": "DONE",
  "base": "<sha>", "head": "<sha>",
  "findings": [
    { "id": "F1", "file": "src/auth/session.py", "line": 42, "end_line": 44,
      "severity": "Critical",
      "quote": "if user.is_admin or token:",
      "title": "token alone grants admin",
      "detail": "…", "pre_existing": false, "evidence": "git blame line, or the probe run" }
  ],
  "coverage": {
    "in_scope": ["src/auth/session.py", "tests/test_session.py"],
    "reviewed": ["src/auth/session.py", "tests/test_session.py"],
    "skipped":  [ { "file": "package-lock.json", "reason": "lockfile" } ]
  },
  "shortcuts": [ { "file": "tests/test_session.py", "line": 10, "kind": "test weakened without a reason" } ],
  "shortcuts_check": "git diff --stat <base>..<head> -- tests/"
}
```

`slot_check.py reviewer` refuses the output when any of these fails:

- `status` is not one of the four words.
- A severity is not `Critical`, `Important` or `Minor`.
- A `quote`, whitespace-normalised, does not occur in `file` at `head` within `line..end_line`
  (±3 lines). This is the quote anchor, so a finding cannot point at code that is not there.
- `in_scope` is not exactly `reviewed` ∪ `skipped`.
- `in_scope` differs from what `review_scope.py` lists for `base..head`. That list is every path of
  `git diff --no-renames --name-status` between the two commit ids, so a rename is a deletion and an
  addition; an agent reviewer runs `review_scope.py` itself rather than a plain `git diff`.

`review_scope.py` is part of the contract, so it lives in core
(`rigger-core/tools/review-scope/review_scope.py`) from phase 2. An external CLI filling the slot
without rigger-review installed is checked against the same list. rigger-review (phase 3) calls it;
it does not own it. Its behaviour is in §13, phase 3.
- A skipped file has no reason.
- `shortcuts` is empty and `shortcuts_check` is empty.
- `coverage.skipped` holds a test file whose `review_scope.py` verdict starts with `review`
  (`coverage-test-skipped`, from phase 3). `slot_check.py --session <id>` (default
  `CLAUDE_CODE_SESSION_ID`) reads the same `review.paths` entries as `review_scope.py`. The rule
  checks existing keys more strictly, so the contract id stays `rigger.reviewer/1`.

An agent reviewer ends its reply with this JSON in one fenced block, after the Markdown report the
brief asks for. A command reviewer writes it to `{out}`.

## 11. Lessons routing and the leak guard

### 11.1 Targets

| Target id | Exists when | Destination | Denylists applied on the way |
|---|---|---|---|
| `personal` | always | `<config dir>/rigger/rules.d/lessons.md`, as today (`curate-lessons/SKILL.md:53`) | none (it stays on the machine) |
| `project:<name>` | always | that project's `CLAUDE.md` "Pitfalls", as today (`SKILL.md:55`) | the personal denylist, plus the denylists of companies other than the project's `company` |
| `module:<plugin>` | an enabled company module declares `lessons.target` | an issue or PR draft for that module's repository (its manifest's `repo`), written to a local file | the personal denylist, plus every other company module's denylist |
| `rigger` | always | an issue draft for the public Rigger repository, written to a local file (today a sentence in `lessons.md:43-45`) | the personal denylist plus **every** enabled company module's denylist |
| `skill:<plugin>:<skill>` | the skill's plugin is enabled | as today (`SKILL.md:54`), routed through the target of the module that owns the skill | as for that module's target |

**New lessons always go personal.** They land in the inbox, which lives in the config dir. Applying
a `global` lesson writes `rules.d/lessons.md`, which is personal. Promoting a lesson to a module or
to Rigger is a second, explicit step.

**What replaces maintainer mode.** The public-repo route becomes the ordinary `rigger` target. A
person who maintains a company module gets that module's target because the module is installed.
There is no mode and no switch.

### 11.2 Flow

1. **Propose.** The curation subagent proposes a target for each entry as today. It now chooses
   from the target list in the snapshot, and the list replaces the table in `SKILL.md:51-55`.
2. **Filter.** Code, not the subagent, computes which targets are allowed. It runs the leak guard
   for each candidate non-personal target and applies the client-context rule (§11.3).
3. **Show.** The person sees each proposal with its proposed target, the allowed targets and, for a
   blocked target, the reason. They decide in Step 3 (`SKILL.md:65-71`).
4. **Write.** A draft bound for `module:` or `rigger` is written to a local file and checked again
   by the leak guard before the person files it. Filing stays the person's step, as today.

### 11.3 Client context

A lesson has client context when any of these holds:

- its `project` field names a repository whose `.claude/rigger.json` sets `company`;
- any enabled company module's denylist matches its text;
- its scope is `project:` and the project is a company's.

Such a lesson cannot go to `rigger`, and the proposal offers `module:<that company's module>`
instead. When the company cannot be determined (a denylist hit with two companies installed), it
offers each matching company target and nothing public.

### 11.4 The leak guard (ships in core, fails closed)

- **Engine move:** the sweep engine moves from `scripts/sweep.py` (not shipped) into
  `rigger-core/tools/leak-guard/sweep.py`. `scripts/sweep.py` becomes a thin wrapper with the same
  command line, so `CLAUDE.md`, `CONTRIBUTING.md`, `gate.py` and `test_sweep.py` keep working.
- **Command:** `rigger-core/tools/leak-guard/leak_guard.py check --target <id> <paths>` builds that
  target's denylist set (§11.1) and runs the engine.
- **Refusals:** it refuses (exit 2, one reason line) when a denylist the target needs is missing,
  unreadable or empty, or has no term the sweep finds exactly once when planted. That is
  `gate.py`'s `release_term` rule (`scripts/gate.py:328`).
- **Personal denylist:** a public or module-bound target with no personal denylist configured is
  refused with the line that says how to set `leak_guard.denylist`. It is not passed.
- **Control:** every run plants one term from each denylist it uses into a scratch file and
  requires exactly one finding per planted term before it reads the real input. That is the
  positive control the Tests rule requires of anything that can report nothing.
- **Output masking:** the denylist terms never appear in the guard's output except inside a
  finding's excerpt, as in `sweep.py:23-25`. Every line the guard writes is masked
  (`CLAUDE.md:34`).
- **Where denylists live:** in the private module (`lessons.denylist`, relative to its install
  path) or on the person's machine (`leak_guard.denylist`). Never in this repository: the contract
  test fails on any `rigger-module.json` here that has a `lessons.denylist`.

## 12. Tests per module

- **Manifest declares tests:** `tests.paths` names a module's tests. `run_tests.py` discovery
  (`run_tests.py:6-7`) keeps finding the same files, and the manifest makes ownership explicit.
- **New mode:** `run_tests.py --changed-modules [--base REV]` maps the change between the merge base
  and the working tree to plugin folders and runs:
  - every test file of each changed module;
  - `tests/test_module_contracts.py`, which takes seconds: manifest schema, `assemble.py --check`
    over the repository, every contract fixture, and `tests/test_repo.py`'s manifest checks;
  - the sweep with `--no-denylist`.
- **Full gate instead:** a change to shared files means the full gate (`gate.py`). Shared files are
  `scripts/`, `tests/`, any `plugins/*/rules/**`, `rigger-defaults.json`, `rules_start.sh` while it
  exists, `bin/py.sh`, `plugins/*/hooks/core_check.sh`, `.github/**` and `.claude-plugin/**`. The
  mode then prints `changed-modules: shared file <path>; run the gate` and exits 2, so a caller
  cannot read it as green. Besides each changed module's tests it runs the tests of the modules
  whose `dependencies` reach a changed one, and `tests/test_repo.py`, `tests/test_plugin_refs.py`
  and `plugins/rigger-rules/tests/test_plugin_rules.py`.
- **Gate or not:** until phase 3 the mode is a local pre-check, not the gate for a change inside one
  module: some test files read another module's files without a dependency between the two, so it
  can pass where the gate fails. From phase 3, `tests/read_by.json` names those reads (a
  `tests/test_*.py` file it does not list reads every module; `tests/test_read_by.py` fails on an
  undeclared read), the mode also runs every test that reads a changed module, and
  `gate.py --changed-modules [--base REV]` is the local gate for a change whose every path is a
  module path. The host computes the changed-file list once and hands it to each leg; the WSL leg
  runs `run_tests.py --changed-from <file>` and never calls git. The status records the mode, and
  `gate.py --verify` names it and refuses a changed-modules status for a change with a shared path.
  A release takes the full gate; CI is unchanged. `select_tests.py` stays the engine of
  `--fix-round`.
- **Test setup:** a test that starts `claude` with `--plugin-dir` also sets `RIGGER_MODULE_DIRS`
  to the same roots, joined with `os.pathsep`, next to the isolated `CLAUDE_CONFIG_DIR` and the
  stub's `ANTHROPIC_BASE_URL` (`CLAUDE.md:9,41`). `scripts/eval_stage.py` sets it for every eval
  case it stages, pointing at the staged copies. `run_tests.py` strips `HARNESS_*` and `CLAUDE_*`
  but not `RIGGER_*`, so it also clears `RIGGER_MODULE_DIRS` from the inherited environment. A test
  that needs it sets it itself, and a developer's own value never leaks into the suite.
- **CI:** pull requests to `main` only, on Windows, macOS and Linux (the workflow is a separate PR).
  It keeps the full gate on Linux and the full suite on macOS and Windows for every pull request. A
  release runs `gate.py --release`.

## 13. Phases

Each phase is one release. Estimates are agent working hours, then lines of product code (Python,
sh, TypeScript, JSON manifests), then lines of tests. Moved files count as moved, not as new code.

### Phase 1: manifest, assembly and config, with no behaviour change

**Scope:**

- **Schema and manifests:** the `rigger-module.json` schema, plus a manifest for each of the 7
  plugins (the panel is `layer: general`, `rules: []`).
- **Assembler:** `rigger-core/tools/assemble/assemble.py` and its handlers in
  `rigger-core/hooks/hooks.json`. It covers parts, layers, ids, trigger sections, the snapshot and
  `--check`. Placeholders for `slot:` resolve from level-1 `slots` only.
- **Retired handlers:** the rules handlers in `rigger-rules`, `rigger-lessons`, `rigger-talk`,
  `rigger-wp` and `rigger-windows` are removed, and `rules_extra.sh` is folded in. `rules_start.sh`
  stays in each plugin for one release, unused, then goes.
- **Rules text:** skill names move from rules text into placeholders and manifest rows. The
  rendered text is identical.
- **Config:** `rigger-defaults.json` and the four-level config resolver. Keys other than
  `triggers`, `expects`, `company` and `leak_guard` are accepted and recorded, with no effect yet.
- **Dependencies:** `"dependencies": ["rigger-core"]` on every module with rules.
- **Doctor:** checks Python, manifests and `--check`.
- **Spikes:** S1–S5 run first.
- **Tests that change, and how:**
  - `WiringTests` (`test_plugin_rules.py:77`): handlers come from the manifest, not from
    `hooks.json` per file.
  - `SkillTableTests` (`:196`): rows come from manifests, and the rendered tables are unchanged.
  - `TestingSkillTriggerTests` (`:290`): reads the rendered table.
  - `RulesSizeTests` (`:309`): the ceiling applies to the assembled general layer.
  - `DuplicateHookTests` (`tests/test_repo.py:379`): new per-plugin handler counts.

**What "no behaviour change" is measured against:** the golden test runs today's handlers from
`34e7713` and the assembler over the same inputs. The inputs are the default set on a Windows
layout and a macOS layout, with and without `rules.d`, with `HARNESS_RULES_DROP` cases, and the
skipped and non-skipped subagent types. The test compares the emitted text per section, byte for
byte with LF, plus the set of root lines.

**Exceptions, stated in the release notes:**

- Python 3 becomes required for rules. It was already required for rigger-core's hooks.
- rigger-lessons, rigger-talk, rigger-windows and rigger-wp now pull rigger-core through `dependencies`, as rigger-rules already does.
- The rules part handlers replace the 19 `SessionStart` rules handlers of today (13 in
  rigger-rules, 2 in rigger-windows, 1 each in rigger-core, rigger-lessons, rigger-talk and
  rigger-wp). Startup time is measured on Windows and macOS and reported. It may not grow by more
  than 300 ms at the median.
- **Accidental override:** a `rules.d` section whose id equals a general one (same file stem and
  heading slug) now replaces that section instead of being printed after it. Whenever a personal or
  company section replaces another, the session gets one line
  `rigger: <file> replaces rule <id>`. The golden test has a case for it, and doctor lists every
  override.

**Exit criterion:**

- the golden test passes on 3 OSes;
- S1–S5 have recorded results;
- the full gate passes;
- a stub run shows every section reaching the first request. That run loads the repository's
  plugins with `--plugin-dir` and names the same roots in `RIGGER_MODULE_DIRS`;
- the same stub run with one root left out of `RIGGER_MODULE_DIRS` shows that module's sections
  missing and its root line absent (no notice: a `--plugin-dir` plugin is in neither Claude Code
  state file), which proves the variable, not a scan of `--plugin-dir`, is what discovery reads.

**Migration:**

- **Windows:** `claude plugin marketplace update rigger`, then `claude plugin update` for each Rigger
  plugin, then `/rigger-core:doctor`. If doctor reports Python missing, use the install line it
  prints (py.sh already finds `py`, `python3` or `python`). `rules.d` and `HARNESS_RULES_DROP` keep
  working unchanged.
- **macOS:** the same commands. Python 3 comes from the Command Line Tools or Homebrew, and doctor
  names both. rigger-windows was never installed there, so nothing else changes.

**Estimate:** 16 h, about 1,250 lines of code (assembler 600, config resolver 220, manifests and
defaults 250, hooks and doctor 180), about 1,600 lines of tests (golden 400, assembler and resolver
900, wiring test rewrites 300).

**Simpler version:**

- **What it keeps:** one handler per module, each module printing its own sections and checking the
  snapshot only to skip sections a higher layer replaced.
- **What it drops:** a single owner of order and duplicates, which means the model may see two
  texts during the race window. It also drops the clean removal of 6 copies of `rules_start.sh`.
  That is not acceptable under "never a model-resolved conflict", so it is listed, not proposed.

### Phase 2: slots and contracts

**Scope:**

- **Slot resolution:** validation and the `## Slots` section.
- **Contracts:** `slot_check.py` with the contracts of §10.4 and their fixtures, `slot_run.py` for
  command kinds, and `review_scope.py` in core (§10.5) as the reviewer contract's source of scope.
- **Trust:** the trust filter of §7.4.
- **Trigger table:** merged into one table, and rows of absent plugins dropped.
- **Rewording to point at the tables:**
  - `worker.md:8-10`;
  - `reviewer.md:17`;
  - `orchestrating-subagents/SKILL.md:86-93,122,147`;
  - `coordinate.md:49,62` (`:16` stays: it names `rigger-core:orchestrating-subagents`, a fixed
    core skill no slot covers);
  - `implementer-brief.md:3,50-51`.
- **Report shadow:** the existing `report_shadow.py` (SubagentStop, shadow only) also records, per
  row, the slot whose agent target is the stopped agent's type and the `slot_check --shape-only`
  verdict on the reply (`ok`, `refused:<rule id>`, `none`, `unread`, `error`), with no git. That
  measures contract conformance before anything is enforced.
- **Doctor:** checks fillers and double fillers.

**Exit criterion:**

- every default filler passes its contract fixtures, and each failing fixture fails on its own
  rule;
- a stub run with the reviewer slot set to a stub command shows `slot_run.py` invoked and a refused
  output reported as blocked;
- the merged table renders every row of today's three tables when all plugins are enabled;
- the full gate passes.

**Behaviour change:**

- one trigger table instead of three;
- rows for absent plugins disappear;
- a new `## Slots` section in the main session, capped at 1,200 bytes for the default set (eleven
  rows of five columns; 717 bytes measured on Windows).

**Migration:**

- **Windows and macOS:** update the plugins. Nothing to configure. A person who had overridden a
  table row in `rules.d` (text copied from `skills.md`) moves it to `triggers` in `config.json`.
  Doctor detects a `rules.d` file with a `| Before | Skill |` table and prints the equivalent JSON.

**Estimate:** about 40 h, 1,850 lines of code and 3,450 lines of tests (the phase 2 plan's
estimate; this section first said 12 h, 800 and 1,100).

**Simpler version:**

- **What it keeps:** slots for `skill` kinds only (testing, process), with no agent or command
  slots.
- **What it drops:** swapping the reviewer for an external CLI, the reviewer contract check and the
  conformance shadow. The orchestrator keeps naming core agents.

### Phase 3: split out the modules

**New modules in the public marketplace:**

- **`rigger-review`** (`fills: reviewer`). It takes ideas, not code, from an Apache-2.0 review CLI:
  - **Agent and skill:** `agents/reviewer.md`, moved from rigger-core with the contract ending, and
    skill `review`, the procedure.
  - **`review_scope.py`** (in core since phase 2; rigger-review calls it, and phase 3 fills in the
    skip reasons): lists the files changed between `base` and `head`, one per line
    with `review` or `skip: <reason>`. Reasons are binary, generated (by header marker), vendored
    (by path list), lockfile, or deleted. It reads git with
    `git -c core.quotepath=off diff --no-ext-diff --no-textconv --no-color --name-status -z`, so a
    repository's diff driver or textconv cannot run code or hide changes. It never skips a test
    file unless `review.paths` names it with a reason. First match wins: `skip: deleted`; a
    `review.paths` skip; a file named `test_*`, `*_test.*`, `*.test.*` or `*.spec.*` is `review`;
    `skip: binary` when the first 8,000 bytes of the head blob and of the base blob hold a NUL byte,
    `review: binary added` when only the head blob does; a file under a `test`, `tests`,
    `__tests__` or `spec` folder is `review`; `skip: lockfile` (`package-lock.json`,
    `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`, `composer.lock`,
    `Cargo.lock`, `poetry.lock`, `uv.lock`, `Pipfile.lock`, `Gemfile.lock`, `go.sum`);
    `skip: vendored` (a `vendor`, `node_modules`, `third_party` or `third-party` segment) and
    `skip: generated` (`@generated`, `DO NOT EDIT` or `auto-generated` in the first 5 lines, or
    `*.min.js` and `*.min.css`) only for a path that existed at base, its marker too; everything
    else `review`. A skip the reviewed diff itself would create never applies.
  - **Per-path review rules:** `review.paths` in config maps globs to rules files that the skill
    loads only for matching files.
  - **Per-language checklists:** `checklists/<language>.md` (Python, TypeScript, PHP, shell to
    start), loaded by the extensions in scope.
  - **Coverage and anchors:** the coverage check and quote anchors are `slot_check.py reviewer`
    with `review_scope.py` as the source of `in_scope`.
- **`rigger-testing`** (`fills: testing`): today's `rigger-core:testing` skill, the weak-test guard
  and its hooks (`rigger-core/hooks/hooks.json:91-102` before the move), and the test-evidence
  shadow gates from rigger-windows (below). Phase 3a moves only rigger-core's parts (the skill, the
  guard with its two handlers, the `testing-skill-loads` eval); rigger-windows' test-evidence gates,
  `tools/gate-lab/` and `tools/safe-edit/` move in 3b. An eval control moves with the case it
  controls: `add-commit-push-chain`'s `no-testing-skill` grader becomes the testing module's
  `git-question-no-testing` case.
- **`rigger-platform`** (add-on, opt-in): the Windows and toolchain knowledge of rigger-windows
  that is not the shell guard. The name is the builder's call.
- **`rigger-shellwise`** (`fills: shell-guard`): a thin Rigger plugin around the separately
  installed shellwise binary.

**About shellwise.** shellwise is a standalone tool and stays independent of Rigger: Rigger never
requires a change in it, and phase 3 does not wait for a shellwise release. Today it is a binary
that people add as a hook by hand, with no `.claude-plugin`. It has the commands `check`, `hook`,
`bench`, `explain` and `lint`, and the levels `ok`, `warn`, `break`, `danger` and `critical`. When
its analysis cannot finish it blocks with `danger`, so it fails closed.

**What `rigger-shellwise` does:**

- **Manifest:** it carries `rigger-module.json` with `fills: shell-guard` and
  `"failure_mode": "closed"` (§10.4 (b)).
- **Finding the binary:** it uses `shell_guard.shellwise_path` from config when set, else
  `shellwise` on PATH.
- **Version check:** it checks the binary against a minimum version pinned in the plugin. How the
  version is read is spike S7: shellwise's README documents no `--version` flag, and the `"version":1`
  in `check --json` is the output schema, not the tool's version.
- **Missing binary:** when the binary is missing or too old, a `SessionStart` line says so and gives
  the install route from the shellwise README. Doctor reports the same. The hook then allows and
  prints nothing, and the core slot line reports the slot unfilled.
- **Hook:** it registers one PreToolUse handler on `Bash|PowerShell` that runs
  `shellwise hook claude-code` with the event on stdin, timeout 5 s, as the shellwise README's own
  hook entry does. `warn` reaches the agent as a note, and `break`, `danger` and `critical` deny.
- **Rule check (optional, §10.4 (d)):** built from shellwise's own commands. `explain <id>`
  succeeding means the filler has that rule, and `check --shell <s> --json -- <probe>` returns the
  findings, so a probe is denied by a rule when a finding names it at a blocking level. shellwise
  has no command that lists every rule id, so `RULES` is answered per id through `explain`.

**rigger-windows goes away. Where each item lands:**

| Item today | Lands in | Note |
|---|---|---|
| `tools/shell-guard/shell_guard.py` (deny rules, PowerShell and git handlers, `hooks.json:20-77`) | **deleted**; the slot is filled by `rigger-shellwise` | shellwise is not changed for Rigger. 4 Python-guard rules have no shellwise counterpart: `bash-powershell-syntax`, `bash-ps-call-operator`, `bash-too-long` and `bash-unterminated-quote`. They are not a Rigger dependency, and their tests go with the Python guard. Proposing them to shellwise is a separate item outside this plan. |
| `tools/shell-guard/lessons_guard.py` (rule checker for `lessons_lint.py`) | **`rigger-shellwise`** `tools/rule_checker.py`, a Python module with `RULES` and `rule_guard_problem` built on `explain` and `check` | `curate-lessons/SKILL.md:92-99` takes the checker path from the shell-guard filler's manifest (`provides.shell-guard.rule_checker`) instead of looking up `rigger-windows@`. A `live:<rule>` lesson for a rule the active filler lacks, the 4 above included, is reported as `no checker` by doctor and curation. It is never dropped, as `SKILL.md:96-99` already does for a missing checker. |
| `rigger-core/tools/token-audit/tests/test_archive.py` (checks `lessons_guard.py`'s rule ids against `shell_guard.py`, `test_archive.py:19,241-242,316`) | rewritten against a stub checker module | It checks the checker interface, not one filler's rule list. |
| commit shadow rule inside `shell_guard.py`, `stop_shadow.py`, `turn_evidence.py`, `.claude/gates.json` format (`INSTALL.md` "Shadow gates") | **rigger-testing** `tools/test-evidence/` | These ask whether a check ran after the last edit, a testing question, not a command-shape one. The commit rule is extracted from `shell_guard.py` into its own handler on `Bash(*commit*)` and `PowerShell(*commit*)`. `tests/test_repo.py` handler counts change. |
| `tools/gate-lab/` (replay and report of the shadow gates) | **rigger-testing** `tools/gate-lab/` | It measures the gates above. The `CLAUDE.md:21` pitfall path follows. |
| `tools/safe-edit/safe-edit.ps1` (red run in place) | **rigger-testing** `tools/safe-edit/` | pwsh runs on all 3 OSes. Its references `tests.md:13`, `defects.md:37` and `testing/SKILL.md:85` become `{{tool:…}}` with `requires: plugin:rigger-testing`. Porting it to Python is the builder's call. |
| `tools/drift-lint/` (dangling skill, agent, path references) | **rigger-core** `tools/drift-lint/` | It becomes a reader of manifests too, so a trigger `load` naming a missing skill is a drift finding. `CONTRIBUTING.md:36` path follows. |
| `tools/mem-check/` (Windows commit-charge headroom) | **rigger-platform** | `run_tests.py:89` path follows. |
| skills `windows-dev-env`, `node-versions`, `php-herd-tooling` | **rigger-platform** | Trigger rows carry `requires` on the OS (`os:windows` for `windows-dev-env`). `php-herd-tooling` and `node-versions` serve macOS too. The local docs folder `<config dir>/rigger-windows/herd-docs/` (`php-herd-tooling/SKILL.md:113`) becomes `<config dir>/rigger-platform/herd-docs/`, and the skill reads the old path for one release. |
| `CONTRIBUTING.md:86-90` (this repo's `.claude/gates.json` opt-in, pointing at rigger-windows' INSTALL) | points at rigger-testing's `tools/test-evidence/` docs | The `gates.json` content does not change. |
| `rules/windows.md` (PowerShell is the default shell, scripts over 10 lines via a file) | **rigger-platform**, `requires: os:windows` | The `Plugin root:` preamble goes, because `{{tool:}}` replaces root-relative names. |
| `rules/windows-skills.md` | **rigger-platform** manifest `triggers` | The table merged into the one table in phase 2. |
| 5 evals (`bash-in-powershell`, `commit-message-file-path`, `rmtree-readonly-objects`, `utf8-env-hides-bug`, `worktree-with-junctions`) | **rigger-platform** `evals/` | They test the platform skill and rules, not the guard. |
| `hooks.json` `Stop` → `stop_shadow.py` | **rigger-testing** | async, as today (`tests/test_repo.py:270`). |
| `bin/py.sh` copy | each new module gets the byte-identical copy | `tests/test_repo.py:101` |

Wording that says "with rigger-windows installed" changes to name the platform module or the
testing module through placeholders or conditional wording:

- `e2e-playwright-speed/SKILL.md:152`;
- `git-workflow/SKILL.md:21-22`;
- `orchestrating-subagents/SKILL.md:47-51`;
- `stack-tooling/SKILL.md:72-73`;
- `testing/SKILL.md:85`;
- `implementer-brief.md:61,68-69`;
- `README.md`.

The doctor's Windows requirement (`doctor.sh:489`) becomes "recommends `rigger-platform` and a
`shell-guard` filler".

**Exit criterion:**

- the four modules pass their own tests and contracts on 3 OSes;
- with the released shellwise binary installed, `rigger-shellwise` fills `shell-guard`:
  - a stub run shows a `break` command denied and a `warn` command run with its note;
  - with the binary removed, the session gets the install line and commands run unguarded;
  - doctor reports the failure mode `closed`;
- `rigger-windows` is removed from `marketplace.json`;
- the full gate and a stub run pass;
- `rigger-review` reviews this repository's last release diff with every in-scope file covered and
  every quote anchored.

**Migration:**

- **Windows:**
  1. One `claude plugin install <plugin>@rigger` call per plugin: `rigger-review`,
     `rigger-testing`, `rigger-platform`, `rigger-shellwise` (a second plugin named in one call is
     dropped without a word, with exit 0). Their `dependencies` install or re-enable rigger-core
     but leave an installed one at its version, so also run
     `claude plugin update rigger-core@rigger`. Phase 3a ships the first two.
  2. Install the shellwise binary as its README says. If you had added its hook to `settings.json`
     by hand, remove that entry: `harness_start.py` reports it as a handler that runs twice.
  3. `claude plugin disable rigger-windows@rigger`, then uninstall it.
  4. Run `/rigger-core:doctor`.
- **Windows, from the first phase-3 release:** `harness_start.py` prints one line while rigger-windows
  is still enabled. If both rigger-windows and rigger-shellwise are enabled, the double-filler line
  of §10.2 says which to disable.
- **Gates file:** a repository's `.claude/gates.json` keeps its format, and its rows move to
  rigger-testing's data folder (`CLAUDE_PLUGIN_DATA` changes with the plugin), so old rows stay
  where they were. Gate-lab takes `--data` for either folder.
- **macOS:** install `rigger-testing` and `rigger-review`, one call each, and update rigger-core
  (doctor and the session's install lines say so), and optionally
  `rigger-shellwise` with the shellwise binary.
  The guard is new on macOS, a behaviour change listed in the release notes. Nothing to remove.

**Estimate:** 28 h:

| Part | Code | Tests |
|---|---|---|
| rigger-review (`review_scope.py` 250, skill and checklists 300 lines of Markdown counted as code, contract glue 150, agent 50) | 750 | 900 |
| commit-rule extraction into rigger-testing | 250 | 300 |
| `rigger-shellwise` (binary lookup and version check 120, hook launcher 40, rule checker 120, manifest and README 70) | 350 | 400 |
| path and wording updates | 200 | 200 |
| manifests | 150 | 0 |
| **Total** | **about 1,700** | **about 1,800** |

About 6,000 lines are moved unchanged and not counted. There is no corpus hand-over to shellwise
and no wait for a shellwise release.

**Simpler version:**

- **What it keeps:** the new modules are built, but rigger-windows stays as the shell-guard filler
  for one more release.
- **What it drops:** `rigger-shellwise` and the cross-platform guard for this release. It keeps the
  Python guard, Windows-only.

### Phase 4: the company overlay mechanism

This phase delivers the generic overlay mechanism and proves it on a fixture private marketplace in
Rigger's own tests. Migrating an existing company fork (renamed copies of every Rigger plugin) to
modules is a separate plan in that fork's own private repository, not part of this design. Rigger
provides two things for it: a documented migration path, and a doctor check for a fork installed
next to Rigger.

**Scope:**

- `layer: company` handling in production;
- level-2 config;
- `lessons.target` and the lesson-target list in the snapshot;
- `leak_guard.py` with per-target denylists and the client-context rule;
- `curate-lessons` reading targets from the snapshot (`SKILL.md:49-55` rewritten);
- **Fixture marketplace:** `tests/fixtures/company-marketplace/`, a directory marketplace with
  three `layer: company` modules: a hosting-provider deploy module, a ticket-intake module and an
  accessibility-audit module. It holds generic names only and a fixture denylist of invented terms.
  The contract test loads it through `RIGGER_MODULE_DIRS` (§6.1).
- **Migration path doc:** `docs/company-overlay.md` explains how a fork turns into modules on top of
  Rigger:
  - one manifest per module with `layer: company`;
  - copied rules files reduced to the sections they change, which then replace general ones by id;
  - renamed copies of Rigger plugins removed in favour of the originals;
  - the module's denylist kept in the private repository;
  - `assemble.py --check` run in that repository's CI against a released Rigger.
- **Doctor check for a fork next to Rigger:** `harness_start.py` and `/rigger-core:doctor` report
  handler pairs where an enabled non-Rigger plugin and a Rigger plugin run the same script basename
  with the same arguments on the same event. That is the duplicate rule of `harness_start.py:1-11`,
  extended to name both plugins. They also report rule sections that print twice under different
  plugin names, found by equal section text.

**Exit criterion:** the fixture marketplace's check passes:

- `assemble.py --check` on Rigger plus the fixture marketplace passes;
- the fixture overlay replaces, adds and drops sections exactly as §8 says, with one test per case;
- two fixture modules setting one key produce the conflict line and the L1 value;
- the leak guard refuses with a missing denylist and with an unplantable one, and blocks a planted
  fixture term bound for `rigger`;
- a client-context lesson is offered only the fixture company's target;
- a fixture "fork" plugin that copies a Rigger handler and a rules file is reported by the doctor
  check.

**Migration:**

- **Windows and macOS, both:** no change for people without a company overlay.
- **People with a fork:** they follow `docs/company-overlay.md` when their own repository's plan
  ships. Until then, doctor tells them which handlers and sections run twice.

**Estimate:** 10 h, about 650 lines of code, about 900 lines of tests (public side only).

**Simpler version:**

- **What it keeps:** company layer and level-2 config only.
- **What it drops:** lesson targets beyond personal and the public draft, and the per-target
  denylist union. The leak guard then runs the person's denylist alone, which misses another
  company's terms.

### Phase 5: panel Settings tab

**Scope:**

- **Settings pane:** a fifth pane `rigger-settings` in `register.tsx` (`PANE_TITLES`, `:49-54`). It
  shows:
  - the slot table with the source level of each value;
  - the trigger rows with their module;
  - every rule section with its id and its source layer (decision 3);
  - the notices of the last assembly, all read from the snapshot.
- **Editing:** it edits level 3 (`<config dir>/rigger/config.json`) and level 4
  (`.claude/rigger.json` of the session folder) and nothing else. It writes only keys of schema 1.
  It keeps unknown keys and key order, and writes UTF-8 with LF.
- **Write safety:** before writing it re-reads the file and refuses with a line when the file
  changed since it was read. After writing it says "applies at the next session start or /clear",
  because config is read once.
- **Source of truth:** the file stays the source of truth. A CLI user edits the same file by hand
  and gets the same result.
- **Write API:** the panel today only reads (`$.fs.read`, `register.tsx:117`). Spike S6: does the
  function-hook engine offer a file write, and is it confined? If not, the pane sends this session
  a request to make the edit with the Edit tool and shows the diff it asked for. The button says
  "asked", never "saved", as the Issues pane does for starts (`rigger-panel/README.md:93-94`).

**Exit criterion:**

- a round-trip test: a file written by the pane equals the file a person writing the same values
  by hand would produce, after `json.loads` comparison and with unknown keys kept;
- a stale-file write is refused;
- the pane shows a planted company override's layer;
- `claude plugin test plugins/rigger-panel` and the full gate pass.

**Migration:**

- **Windows and macOS, both:** update rigger-panel. Nothing to configure. A person without the
  panel loses nothing.

**Estimate:** 12 h, about 700 lines of TypeScript, about 900 lines of tests.

**Simpler version:**

- **What it keeps:** a read-only Settings pane that shows layers, sources and notices.
- **What it drops:** editing from the panel. People edit the file, which they must be able to do
  anyway.

### Totals and the simpler design

| Phase | Hours | Code | Tests |
|---|---|---|---|
| 1 | 16 | 1,250 | 1,600 |
| 2 | 12 | 800 | 1,100 |
| 3 | 28 | 1,700 | 1,800 |
| 4 | 10 | 650 | 900 |
| 5 | 12 | 700 | 900 |
| **All** | **78** | **5,100** | **6,300** |

**Simpler design overall (about 30 h):**

- **What it keeps:**
  - manifests;
  - rule layers by id;
  - trigger assembly;
  - two config levels (personal and project);
  - the leak guard with the person's denylist.
- **What it drops:**
  - slots and contracts (agents stay named in skills, and no external reviewer);
  - level-2 company defaults (a company ships rules only);
  - lesson targets beyond personal and the public draft;
  - the Settings tab;
  - the rigger-windows split (it stays as it is).

## 14. Risks

| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| Parallel `SessionStart` handlers reorder or merge parts (S1). | medium | rules arrive in a different order | Parts are self-contained (root line per part). The golden test compares per part. Spike before any code. |
| Plugin options do not reach the assembler (S2). | high | `talk.md` placeholders lose user values | §9.6 fallback handler. Decided after the spike, before phase-1 code. |
| `installPath` stale for directory marketplaces (S3). | known for dev installs | a developer sees old rules | Read the marketplace `source` for directory marketplaces. A test from a copy laid out like the cache (`CLAUDE.md:35`). |
| Re-implementing Claude Code's enabled-plugin logic diverges from it (S4). | medium | a module's rules print while the plugin is off, or the reverse | Read every scope. Compare with the stub's init `plugins` list in a test. Doctor prints both lists when they differ. |
| Python missing breaks rules that today need only `sh`. | low (rigger-core already needs Python) | no rules | Loud one-line failure plus a doctor check. Release notes. |
| Startup cost grows (K Python starts). | medium on Windows | slower session start | Measure in phase 1 (median under +300 ms). If over, parts 2..K read part 1's cache when it is newer than every input (builder's call). |
| A project file sets a command slot in a cloned repository. | medium | runs a command the person did not choose | Trust filter (§7.4). Argv with no shell. Test with a planted project file. |
| Section ids change when a heading is reworded and overrides silently stop applying. | medium | a company rule no longer replaces the general one | An orphan override fails the check. Doctor names it at startup. |
| Two company modules conflict on config. | low | unexpected value | Neither applies, one line names both (§7.3). |
| A leak guard that reports clean on an empty denylist. | was real for gates | private term published | Fail closed plus a planted-term control per run (§11.4). |
| The released shellwise binary changes its hook reply or `check --json` shape. | low | `rigger-shellwise` misreads it | Pin a minimum version (S7), and test against a recorded reply of that version. shellwise itself is never changed for Rigger. |
| Users relied on the 4 Python-guard rules shellwise lacks. | medium on Windows | those mistakes are no longer caught | Release notes name them. `live:` lessons for them show `no checker` and stay. Proposing the rules to shellwise is a separate item. |
| External reviewer output looks valid but reviews nothing. | medium | false "no findings" | Coverage must equal `review_scope.py`'s list. Quotes must anchor. Empty findings need `shortcuts_check`. |
| Public text gains a private term through examples. | medium | leak | Generic examples only. Sweep with the maintainer's denylist at release (`gate.py --release`). The contract test refuses `lessons.denylist` in public manifests. |

## 15. Open points left to the builder

Each of these is marked **builder's call**:

1. **Default K:** how many part handlers there are (6 and 4 proposed), and whether parts 2..K
   recompute or read a cache written by part 1. Decide from S1 and the startup measurement.
2. **Snapshot cleanup:** the snapshot's retention (14 days proposed) and whether cleanup runs at
   startup or in doctor.
3. **Reviewer JSON in a reply:** how an agent reviewer's reply carries the JSON (one fenced block
   at the end is proposed), and whether `slot_check` also accepts the Markdown form. **Settled:**
   the last fenced `json` block of the reply; no Markdown form.
4. **Platform module name:** `rigger-platform` or another name for the module that inherits the
   Windows and toolchain skills.
5. **safe-edit:** whether `safe-edit.ps1` is ported to Python in phase 3 or stays pwsh.
6. **Generated-file detection:** the exact markers and path lists `review_scope.py` uses, beyond the
   reasons listed. **Settled:** the markers, lists and order in §13 phase 3, `review_scope.py`.
7. **Panel writes:** whether the panel writes directly or asks the session, decided by S6.
8. **Reviewer checklists:** the first set of per-language checklists and their order. **Settled:**
   `python.md` (`.py`), `typescript.md` (`.ts .tsx .js .jsx .mjs .cjs`), `php.md` (`.php`) and
   `shell.md` (`.sh .bash`, and an extensionless file whose first line starts `#!` and names `sh` or
   `bash`), at most 25 bullets each; the review skill loads at most 3, by how many paths to review
   each matches, ties alphabetical.
9. **Slug edge cases:** headings that are only a placeholder, or two headings in one file with the
   same slug. The latter is a duplicate and fails the check. The question is only how the message
   reads.
10. **shellwise version check (S7):** how `rigger-shellwise` reads the binary's version, since no
    `--version` flag is documented. Options: a version flag if the binary has one, or the minimum
    behaviour probed with a known `check --json` input. Spike it in phase 3 before the version pin
    is written.
11. **Core trigger rows:** core's rows in `rigger-defaults.json` carry `section` (§7.5), placed in
    the rigger-rules table in today's order during phase 1.
