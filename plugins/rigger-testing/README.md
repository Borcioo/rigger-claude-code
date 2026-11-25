# rigger-testing

The testing module of Rigger: the `testing` skill that fills rigger-core's `testing` slot, and the
commit-time weak-test guard. It depends on `rigger-core`, whose skill triggers row for writing code
or tests loads the `testing` slot before the TDD skill.

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

## Tests

- `tools/weak-test-guard/tests/test_weak_test_guard.py`: the guard on real git repositories.
- `tests/test_testing_wiring.py`: both handlers of `hooks/hooks.json` deny a weakening commit
  through the shell Claude Code starts hooks with, and a stub `claude -p` session denies a real
  `git commit` from this plugin.

## Evals

`evals/testing-skill-loads/` and its negative control `evals/git-question-no-testing/`; the case
inventory is `evals/CASES.md` at the repository root.
