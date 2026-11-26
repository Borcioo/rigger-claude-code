# rigger-testing

The testing module of Rigger: the `testing` skill that fills rigger-core's `testing` slot, the
commit-time weak-test guard, the test-evidence shadow gates and the gate lab. It depends on
`rigger-core`, whose skill triggers row for writing code or tests loads the `testing` slot before
the TDD skill.

Install: `claude plugin install rigger-testing@rigger` (with the `rigger` marketplace added, as
rigger-core's README says). The install adds rigger-core when it is missing but does not update an
installed one: coming from a release whose rigger-core shipped the testing skill, also run
`claude plugin update rigger-core@rigger`, then `/rigger-core:doctor`. Until rigger-core is updated,
the weak-test guard runs twice (once from each plugin) and rigger-core's startup check reports the
duplicate; update rigger-core rather than disabling a plugin. Without this plugin the `testing` slot
stays empty, each session prints the install line, and the commit-time weak-test guard is off.

## Skill

`rigger-testing:testing`: how to meet the Tests rule with fewer, stronger tests (the level and the
boundary, expected values from the requirement, doubles, red runs, checks that can report nothing,
fixtures, flakiness).

## Commit guard

`tools/weak-test-guard/weak_test_guard.py` runs before a Bash or PowerShell `git commit` (two
`PreToolUse` handlers in `hooks/hooks.json`, filtered with `if` on `*commit*`). A commit whose diff
removes tests, adds a skip or drops assertions is denied with the findings unless its message holds
a `Test-change: <reason>` line.

## Shadow gates

`tools/test-evidence/commit_shadow.py` (two more `PreToolUse` handlers on `*commit*`) and
`tools/test-evidence/stop_shadow.py` (an asynchronous `Stop` handler) only write rows under the plugin's data
folder, in a repo that opts in with `.claude/gates.json`; they print nothing and never block. `TEST_EVIDENCE=off`
turns the commit handlers off. Read `tools/test-evidence/README.md`; `tools/gate-lab/` replays and scores the rows offline.
The Stop shadow's claim, negation, disclosure and status word lists are data in
`tools/test-evidence/phrases.json`; config key `test_evidence.phrases` (levels 2 and 3) adds words to them,
so no language is fixed in code. Between those levels a list replaces the lower level's list of the same
category and `null` removes it.

## Red run in place

`tools/safe-edit/safe_edit.py` breaks one file on purpose, runs your test command and always puts the file
back: `python safe_edit.py --path FILE --find TEXT --replace TEXT [--count N] [--run COMMAND --expect-ran REGEX]
[--working-directory DIR]`, or `--path FILE --restore` after a killed run. A value that starts with `-`
goes with `=` (`--find=-X`). Its header lists the exit codes.

## Tests

- `tools/weak-test-guard/tests/test_weak_test_guard.py`: the guard on real git repositories.
- `tests/test_testing_wiring.py`: both handlers of `hooks/hooks.json` deny a weakening commit
  through the shell Claude Code starts hooks with, and a stub `claude -p` session denies a real
  `git commit` from this plugin; both commit shadow handlers write one row for a commit in a repo
  that opts in, and none with `TEST_EVIDENCE=off`.
- `tools/test-evidence/tests/` and `tools/gate-lab/tests/`: the shadow gates, their transcript reader and the
  gate lab.
- `tools/safe-edit/tests/test_safe_edit.py`: the red run in place.

## Evals

`evals/testing-skill-loads/` and its negative control `evals/git-question-no-testing/`; the case
inventory is `evals/CASES.md` at the repository root.
