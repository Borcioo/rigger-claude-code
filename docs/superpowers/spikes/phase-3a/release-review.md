# Real review of the last release (phase 3a exit criterion)

The review module's `reviewer` instructions (`plugins/rigger-review/agents/reviewer.md` and the
`review` skill) were run on the last release diff, and its reply was checked with the module's
contract checker.

- Diff: `v0.5.0..v0.6.0`, base `1f1e204999b50896ca645957d8a44ee495cb6686`, head
  `66ab916ae4f841ee2f608eb92db85f85ef02d169`.
- Scope: `review_scope.py --base <base> --head <head>` printed 146 files: 144 to review, 2 skipped
  (deleted at head). The reply covers all 146: 144 reviewed, 2 skipped with a reason.
- The reviewer read every file from `git archive <head>` in a scratch folder, because the
  checkout's HEAD was a later commit.
- Check:

  ```
  python -I plugins/rigger-core/tools/slot-check/slot_check.py reviewer <reply file> \
    --base 1f1e204999b50896ca645957d8a44ee495cb6686 --head 66ab916ae4f841ee2f608eb92db85f85ef02d169
  slot-check: ok reviewer 4 findings
  ```

  Exit 0: the reply has the `rigger.reviewer/1` shape, every in-scope path is covered and every
  quote is anchored at head.

## Findings

| Id | Severity | Title |
|---|---|---|
| F1 | Important | An 0.5.0 rigger-core with 0.6.0 modules silently loses the Skill triggers table |
| F2 | Important | A command slot run from the repository root imports repository code through the interpreter's module path |
| F3 | Minor | A slot dropped by its own `requires` still satisfies another slot's `slot:` requirement |
| F4 | Minor | Neither containment check of `searchable` is tested on its own |

Each fix commit names its finding id.
