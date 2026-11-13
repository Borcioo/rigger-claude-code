# Contributing to Rigger

Bug reports, rule proposals and pull requests are welcome. Open an issue first for anything larger
than a fix, so we can agree on the shape before you write it.

## Ground rules

- Everything under `plugins/` ships to other machines (macOS, Linux, Windows): no absolute paths,
  no personal, company or client names, no private hosts.
- A rule changes in its rules file (`plugins/<plugin>/rules/*.md`), never in a `CLAUDE.md`. A rule
  holds only what changes what the reader does. Each rules file is printed by one `SessionStart`
  handler and stays under 9,000 bytes with LF endings (the hook text cap is 10,000 characters);
  the tests enforce it.
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
| `--verify <status.txt>` | runs nothing; checks that the status file's pass line is about the current tree |

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
