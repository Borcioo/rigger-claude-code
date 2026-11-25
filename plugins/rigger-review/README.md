# rigger-review

The review module of Rigger: the `reviewer` agent that fills rigger-core's `reviewer` slot, and the
`review` skill that holds its procedure. It depends on `rigger-core`, whose `review_scope.py`
lists the files in scope and whose `slot_check.py reviewer` checks the agent's closing JSON.

Install: `claude plugin install rigger-review@rigger` (with the `rigger` marketplace added, as
rigger-core's README says). The install adds rigger-core when it is missing but does not update an
installed one: coming from a release whose rigger-core shipped the reviewer, also run
`claude plugin update rigger-core@rigger`, then `/rigger-core:doctor`. Without this plugin the
`reviewer` slot stays empty, each session prints the install line, and an orchestrated review is
reported as blocked, never skipped.

## Agent

`rigger-review:reviewer` reviews or verifies code from a self-contained brief with read tools and a
shell, and never edits the reviewed tree. Its reply ends with one `rigger.reviewer/1` JSON block:
findings with quotes copied from the files, and coverage of every path in scope.

## Skill

`rigger-review:review`, loaded by the agent first:

- runs rigger-core's `review_scope.py` for the paths in scope and their verdicts (`review` or
  `skip: <reason>`);
- reads the per-path rules files that `review.paths` in config names, as the files the tool wrote;
- loads up to three language checklists from `checklists/` (Python, TypeScript and JavaScript,
  PHP, shell), chosen by the paths in scope.

## Tests

`tests/test_review_module.py` pins the agent, the skill, the checklist table and the manifest.
`tests/contract/reviewer/` holds the `rigger.reviewer/1` contract fixtures, run by the repository's
`tests/test_module_contracts.py` through rigger-core's `slot_check.py`.
