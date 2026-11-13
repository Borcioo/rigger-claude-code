# variant-run — run claude with a modified global CLAUDE.md

Lets an eval run `claude` (and its subagents without `omitClaudeMd: true`) with a copy of the global
CLAUDE.md from which sections were removed, so the value of a rule can be measured. The source is
`--source`, by default `CLAUDE.md` in the config dir in effect (`CLAUDE_CONFIG_DIR`, else
`~/.claude`). The source is only read, never changed.

## Mechanism

In the work dir, `prepare` writes the copy as a project CLAUDE.md and adds the source path to
`claudeMdExcludes` in `<workdir>/.claude/settings.local.json` (other keys kept). Claude started in
the work dir then loads the copy and not the source.

- Copy location: `<workdir>/CLAUDE.md`; `<workdir>/.claude/CLAUDE.md` when the work dir has its own
  `CLAUDE.md` (an eval fixture's), which stays untouched. `.claude/variant-run.json` records the
  location, so a second `prepare` overwrites the same copy. A work dir that has both `CLAUDE.md`
  and `.claude/CLAUDE.md` is refused, and so is a `variant-run.json` that is not valid or records
  any other location.
- Guard (links resolved, case-insensitive on Windows, trailing separator ignored): exit 2 and
  nothing written when the work dir is the home dir or an ancestor of a guarded dir, a write target
  lies outside the work dir or under a guarded dir, or a target is the source. The guarded dirs are
  `~/.claude` and, when set, `${CLAUDE_CONFIG_DIR}`.
- Does not work: `--setting-sources project,local` alone (the user file still loads).
- Does not work: `claudeMdExcludes` in a file passed with `--settings` (ignored).
- Token cost of injected text: paired `claude -p --output-format json` runs (same model, same
  prompt, repeated pairs), compared only at the same `shapeHash` (another shape differs by 25k
  tokens with no change); `--strict-mcp-config` and a sentinel, because CLAUDE.md is read from every
  ancestor of cwd; `api-request`/`api-request-blob` in the transcript hold the real request text; a
  marker search stays inside the run's project folder (the calling session's transcript holds it too).

## Usage

```
python -I tools\variant-run\variant_run.py prepare <workdir> [--source <CLAUDE.md>] [--drop-section "<heading>"]... [--marker <text>]
python -I tools\variant-run\variant_run.py run <workdir> [prepare options] [--drop-rule <name>]... -- <command...>
python -I tools\variant-run\variant_run.py verify <workdir> [--claude <exe>] [--agents <abs json>] [--drop-rule <name>]...
```

- `--drop-section` takes the heading text without the `#`s; it removes that heading and everything up
  to the next heading of the same or a higher level (`#` lines inside code fences are not headings).
  A heading that is missing or occurs twice exits 2 and writes nothing.
- `--marker X` appends `When asked for the variant marker, print X.`
- `run` prepares, then runs the command in the work dir and exits with its exit code.
- `verify` prepares a full copy with a random marker, runs `claude -p` (haiku, `--max-budget-usd 0.2`,
  about $0.03) asking the `rigger-core:worker` subagent for the marker, and exits 0 only when the
  subagent's reply holds it: the Agent call's tool_result, or for a backgrounded agent the
  `handback_report` of its `task_notification`. It fails when the marker leaked into the brief.
  A pass proves the copy reaches the subagent, not that the live file is excluded: a full copy
  loaded next to the live file passes too.
- `--agents` must be an absolute path: claude runs in the work dir. To override the agent, the JSON
  key is `rigger-core:worker`, the name `verify` asks for, and the definition must not set
  `omitClaudeMd: true`. A bare `worker` key adds a second agent that `verify` never calls.
- `--drop-rule <name>` (repeatable, `run` and `verify`) names what the rules hooks skip: a rules-file
  basename (`talk.md`) or the text of a `## ` heading. See the contract below.

## `HARNESS_RULES_DROP` contract

`run` and `verify` set `HARNESS_RULES_DROP` in the child's environment to the `--drop-rule` values
joined with `;`. Without the flag the variable is absent in the child, also when the parent has it.

- Entries are separated by `;` or a newline.
- Each entry is trimmed.
- Match is exact: no globs, no regex.

A value that is blank or holds `;` or a newline exits 2 before anything is written or started.

## Gate for a CLAUDE.md rule

1. Same day, same eval, three runs, each in a fresh work dir through `prepare`, so only the section
   differs: base ×2 (full copy, no `--drop-section`) and variant ×1 (`--drop-section "<rule heading>"`).
2. Coverage first: the eval must contain a case the rule is meant to change. Without one the
   ablation is "not measurable", whatever the numbers say.
3. Keep the rule when the variant's pass rate drops below min(base1, base2). Remove it only when the
   variant's pass rate is not below min(base) and the token saving is larger than the base-to-base
   difference (the noise band); a saving inside the band counts as none.

## Test

```
python -I tools/variant-run/tests/test_variant_run.py
```
