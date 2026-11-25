# Contributing to Rigger

Bug reports, rule proposals and pull requests are welcome. Open an issue first for anything larger
than a fix, so we can agree on the shape before you write it.

## Ground rules

- Everything under `plugins/` ships to other machines (macOS, Linux, Windows): no absolute paths,
  no personal, company or client names, no private hosts.
- A rule changes in its rules file (`plugins/<plugin>/rules/*.md`), never in a `CLAUDE.md`. A rule
  holds only what changes what the reader does. A new rules file is listed in its plugin's
  `rigger-module.json`. rigger-core's assembler prints every section; each section stays under 9,000
  bytes with LF endings (the hook text cap is 10,000 characters). The tests and
  `plugins/rigger-core/tools/assemble/assemble.py --check` enforce it.
- `bin/py.sh` exists once per plugin as byte-identical copies: change all of them in one commit.
- Every new test gets a red run: break the code it covers once and show the failing assertion.
- Run every `claude` command, including `claude plugin validate`, under a temporary
  `CLAUDE_CONFIG_DIR`, because it writes into the config directory. `CLAUDE.md` lists the other
  pitfalls of this repo; read it before your first test.

## Test

```
python -I scripts/run_tests.py
```

On Windows, also the Linux run under WSL (dash as `/bin/sh`, no `claude`):

```
wsl -d <distro> --cd <clone path as seen from WSL> -e python3 -I scripts/run_tests.py --no-claude
```

`--agent` prints only failures and totals and names a log with every file's whole output.
`--fix-round` runs only the files your change can affect (against the merge base with `main`), the
ones that failed first, and stops at the first failure; it never replaces the full suite. A change
to a rules file or a skill also runs `plugins/rigger-rules/tests/test_plugin_rules.py` and
`plugins/rigger-windows/tools/drift-lint/tests/test_drift_lint.py`.

`python -I scripts/run_tests.py --changed-modules [--base REV]` is a fast local pre-check for a
change inside one module's folder: it runs that module's tests, its dependents', the tests that
read it and four repository-wide files, then the sweep. Exit 2 means a shared file changed, so only
the full gate says anything. A pull request whose every changed path lies in module folders may
take `python -I scripts/gate.py --changed-modules [--base REV]` as its gate; a release, and a
change with a shared path, take the full gate.

When a test starts reading another module's files (a path, a module name, a loop over `plugins/`),
add it to `tests/read_by.json`: `{"<test file, or a test folder ending in />": ["<module>", …] or
["*"]}`. `tests/test_read_by.py` fails on a read the map does not declare; a key that matches no
test file makes the mode exit 2. A `tests/test_*.py` file the map does not list reads every module.

A plugin that fills a slot ships its contract fixtures in `tests/contract/<slot>/` and names the
slot in `tests.contract` of its `rigger-module.json`. Each folder holds `pass*` files that must pass
and one `fail-<rule id>*` file for every rule of that slot's contract
(`plugins/rigger-core/tools/slot-check/slot_check.py` `RULES`), each failing on that rule alone.
`tests/test_module_contracts.py` runs every fixture through `slot_check.py`, and fails when a rule
has no `fail-` file. The `reviewer` fixtures live in the review module,
`plugins/rigger-review/tests/contract/reviewer/`; a new `slot_check.py reviewer` rule gets its
`fail-` file there.

## The gate

`scripts/gate.py` runs the suite, its planted control, the sweep and its control at once, then, on
Windows, the suite again under WSL, and writes a `status.txt` you read instead of rerunning:

```
python -I scripts/gate.py
```

Every input is optional. Without `--denylist` the sweep runs with `--no-denylist` and the status
says `denylist: none`. Without `claude` on PATH the suite runs with `--no-claude` (its
`claude plugin validate` test is skipped) and the status says so. On Windows the WSL leg needs
`wsl` and a distribution with `python3` (Ubuntu unless `--distro` names another); without `wsl` on
PATH it prints `SKIP wsl: wsl not on PATH`. Skipped legs are counted on the pass line.

| Flag | What it adds |
|---|---|
| `--denylist <file>` | the sweep also looks for each term of your own denylist (one case-insensitive regex per line, kept outside the repo) |
| `--release` | requires `--denylist`; plants one term of it and requires the sweep to find it exactly once |
| `--distro <name>` | the WSL distribution of the Linux leg (default `Ubuntu`) |
| `--out <folder>` | where the logs and `status.txt` go (default: a `gate` folder in the temp directory, named in the first line) |
| `--changed-modules [--base REV]` | the gate of a module-only change: the changed modules' tests (and their readers) in place of the suite, here and under WSL, from one changed-file list the host writes; not with `--release` |
| `--verify <status.txt>` | runs nothing; checks that the status file's pass line is about the current tree, names the mode (`full` or `changed-modules: <modules>`), and refuses a changed-modules result once the change holds a shared path or its recorded merge base is not the merge base of `--into <branch>` (default `main`) and HEAD |

The last line is `# gate passed, <seconds> s[, skipped N], tree <id>` or `# gate failed: <legs>`.

## The sweep

```
python -I scripts/sweep.py --no-denylist
```

It must print `# files N, findings 0` with N > 0. It flags local paths (drive, home, WSL mount and
UNC forms), e-mail addresses, IPv4 addresses, tokens and private keys, hosts outside
`ALLOWED_DOMAINS`, binary files, files that are not UTF-8, denylisted terms, and `stale-allow` lines
in `scripts/sweep-allow.txt` that no longer suppress anything. A public documentation host you cite
gets an exact entry in `ALLOWED_DOMAINS`; an accepted exception is an exact `path<TAB>text` line in
`scripts/sweep-allow.txt`. The docstring of `scripts/sweep.py` has the details.

## Other tools

- `scripts/eval_suite_check.py --cases evals/CASES.md plugins/<plugin>` checks a plugin's `evals/`
  cases against `evals/CASES.md`; `scripts/eval_summary.py --plugin <name> --model <model>
  <aggregate-result.json>...` turns `claude plugin eval` results into a per-plugin table.
- `scripts/eval_stage.py plugins/<plugin> <scratch folder>` copies a plugin and the plugins it depends on
  into a scratch folder so an eval case's `plugins:` can load them; `eval_summary.py` refuses a result whose
  traces show a plugin that failed to load.
- `scripts/mutate_parse.py` is a library for mutation scripts: it reads a test run and says whether a
  mutant's run is red, green or invalid.
- `.claude/gates.json` opts this repo in to the shadow checks of `rigger-windows`' shell guard: with
  `"commit": "shadow"` each `git commit` logs whether a passing check (the `checks` patterns) ran
  after the newest file it commits, and at Stop whether a closing claim rests on a check after the
  last edit; neither ever blocks. `scope_commit` and `scope_stop` name the files that count. See
  "Shadow gates" in `plugins/rigger-windows/tools/shell-guard/INSTALL.md`.

## Pull requests

Branch off `main` (`feature/<slug>`, `fix/<slug>`, `chore/<slug>`), write commits as Conventional
Commits in English, and open a pull request to `main`. The description says why, and how you
checked it: the commands you ran and their last lines. A user-visible change adds a line under
`## [Unreleased]` in `CHANGELOG.md`. Pull requests are squash-merged; releases are tagged from
`main`.

## License

By contributing you agree that your contribution is licensed under the GNU General Public License
v3.0 or later, the license of this repository (`LICENSE`).
