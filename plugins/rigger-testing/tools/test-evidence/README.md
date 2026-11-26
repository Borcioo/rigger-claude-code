# test-evidence

The commit and Stop shadow gates of rigger-testing: `commit_shadow.py` (two `PreToolUse` handlers, Bash and
PowerShell, filtered with `if` on `*commit*`) and `stop_shadow.py` (an asynchronous `Stop` handler), both
reading transcripts through `turn_evidence.py`. They print nothing and never deny or block.

## Shadow gates

Two rules only write rows, they never deny: one at `git commit` (was a check passed after the newest committed file?), one at
Stop (does a closing claim rest on a check after the last edit?). A repo opts in with `.claude/gates.json`. With no file the
commit rule reads only the repo's `.git` and that path. The Stop rule learns the repos from the transcript, so on a closing
with a claim it opens the transcript and removes its row again, in the background, about 50 ms, no process started. Example:

```json
{
  "commit": "shadow",
  "checks": ["python -I scripts[\\\\/]run_tests\\.py"],
  "scope_commit": ["*.py", "src/**/*"],
  "scope_stop": ["*.py", "src/**/*"]
}
```

- `checks` are regexes on the command. Write every path separator as `[\\/]` (doubled in JSON): an agent types either.
- A glob with no slash matches the basename; `**/` spans folders, a bare `**` does not. `"commit": "shadow"` turns on the commit rule, `scope_stop` the Stop rule.
- Rows land under `CLAUDE_PLUGIN_DATA`: `commit-gate/rows/`, `turn-gate/rows/`. A commit waits for its row 2.5 s at most.
- Rows written before rigger-testing took the gates over stay in rigger-windows' data folder; point `--data` and
  `--from-shadow` at it to read them.
- A commit holding `# shellwise: allow` gets the verdict `marker`. `TEST_EVIDENCE=off` in the environment turns the two
  commit handlers off.
- Optional, to measure the gates: `report.py` in rigger-testing's `tools/gate-lab/` (run from the installed
  plugin, as `replay.py`'s docstring says) with `--data <the plugin's data folder>`
  copies `trials.template.json` to `trials.json` on its first run; `--due` lists the trials whose review date has come. The
  trials are yours to define; see `tools/gate-lab/replay.py` and `report.py` for the commands.
- `same_call` and `unknown/hidden` are not credit. Credit is `fresh` and `verified`.
- Known gaps: the reader parses shell text only as far as the rules need it. A zero-test run with exit 0, a failure in an
  earlier stage of a compound, a script a `checks` regex matches by name, a failed `cd`, curl exit 0 with an error body, or a
  shell edit after the check can read as credit. A hands-on check has no root filter. A subagent's closing writes no row.
