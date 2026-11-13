# Lessons case archive

One file per case, `YYYY-MM-DD-<slug>.md`: the frontmatter below, then the body (Symptom, Cause,
Fix). Search the archive before debugging: `rg -i '<error text or pattern>' <archive>`.

Start an archive by copying this README and `classes.txt` into an empty folder. After adding or
editing a case, run the lint (one `path: problem` line each; exit 1 on any problem, 2 when the
folder is missing or holds no case file):

```
python "<lessons plugin>/tools/lessons_lint.py" --archive <archive> [--classes <file>] [--repo <dir> ...] [--rule-checker <file> ...]
```

Test guards resolve against each `--repo` in turn, by default the archive's parent folder. Rule
checkers are each `--rule-checker` file plus every repo's `plugins/*/tools/*/lessons_guard.py` (the
rigger-windows plugin ships `tools/shell-guard/lessons_guard.py`).

## Frontmatter

| key | value |
|---|---|
| `date` | `YYYY-MM-DD`, the same as the file name |
| `project` | project the case happened in |
| `stack`, `tools` | `[flow, lists]`, may be empty `[]` |
| `signature` | what to `rg` for: a code pattern, a command or error text |
| `error_text` | optional; literal tool or error output only, never a code pattern (see below) |
| `kind` | `env-defect` (the machine or a tool misbehaves) · `knowledge` (a platform, library or vendor fact was not known) · `judgment` (a wrong call with the facts at hand) · `shortcut` (a known rule or check was skipped); with `rule_loaded: yes` the rule was known, so `shortcut`, not `judgment` or `knowledge` |
| `class` | a slug from `classes.txt` |
| `guard` | `none` · `proposed` · `live:<rule id>` · `live:<file>::<test name>` |
| `probe` | optional, only with `live:<rule id>` (not a test guard): a minimal real command or input of the failure shape that the rule catches |
| `guard_reason` | only with `guard: none`: why no deterministic check can catch the class; required when the case is a repeat |
| `status` | `rule` (lives in the file `rule` names) · `archived` (covered elsewhere or dropped) · `fixed` (the cause was removed from the environment) |
| `rule` | `<file path>#<start of heading>`, or `none` |
| `scope` | `global` · `skill:<name>` · `project:<name>` (the case's own `project`): where the rule lives. A `global` rule path is `<config dir>/rigger/rules.d/lessons.md` or another file in the config dir (`~/.claude/`, `$CLAUDE_CONFIG_DIR/` or a home folder's `.claude/`; a project's own `.claude/` is the project's), and under no `skills/` folder; a `skill:<name>` rule path lies under `skills/<name>/`; a `project:<name>` rule path lies outside the config dir and outside a harness repo. A `global` or `skill:<name>` rule path never lies in an installed copy (`plugins/cache/<marketplace>/<plugin>/<version>/`, `plugins/marketplaces/<marketplace>/`): the next update replaces it, so the rule goes to the plugin's source repo. A global or skill case in a class that has a project-scoped case fails while all the class's cases come from one project |
| `repeat_of` | file name of the latest earlier case of the same class, or `none` |
| `rule_loaded` | `yes` · `no` · `unknown`: was the rule that covers this case in the context when it happened; `unknown` when the case does not say |
| `session` | the transcript session id (a UUID), or `unknown`; never guess one |

A value that starts with `"` is read as a JSON string (escapes `\"`, `\\`), one that starts with `[`
as a `[flow, list]`; quote a string that starts with either character. Every other value is read as
written.

## Rules

- **`class`**: reuse the slug that names the same failure, even if the tool or project differs. A
  new slug needs its own `slug: meaning` line in `classes.txt` first.
- **`repeat_of`**: when the class already has a case, point to the latest one (the lint checks it is
  the latest earlier case of the class). Two files written about one incident are not a repeat, and
  neither is a case whose own check (a hit count, an assert, a positive control) caught the failure
  before harm: `repeat_of: none`. A repeat is a process failure: its `guard` is `proposed` or
  `live`; `none` only with a `guard_reason` that says why the class is a judgement no deterministic
  check can catch.
- **`guard: live:…`** only for a check that exists and runs on the failure. A check that exists
  but lets the case's real input through (a known gap) is not live: the case stays `proposed`.
  - test: `live:<repo-relative file under tests/>::<test name>`; the lint checks that the file
    holds `def <test name>(`, `check("<test name>"` or `Test-Case '<test name>'` on a line that is
    not a comment.
  - a shell hook rule, in a case whose `tools` name `powershell`, `bash` or `git-bash`, or any rule
    id a loaded rule checker claims (its `RULES`), whatever `tools` say: `live:<rule id>` with a
    `probe`; a loaded rule checker must confirm that the hook denies the probe with that rule in a
    shell `tools` names (no checker loaded is a problem in a shell case).
  - any other check (a lint rule): `live:<rule id>`, accepted as written.
- **`guard: proposed`**: the body has a line of its own `Proposed (not built): <mechanism>` naming
  the check that would catch the failure. Anything not built yet, or built only on an unmerged
  branch, is `proposed`.
- **`error_text`**: a fragment of the last error line of the failing output that only this case's
  failure prints: at least 15 characters once digits, paths, ids and secrets are masked (counted
  without the `<n>`-style placeholders), the tool's English output where it has one, and in a form
  that does not depend on the locale. A generic message (an exit code, "access denied") is no
  `error_text`.
