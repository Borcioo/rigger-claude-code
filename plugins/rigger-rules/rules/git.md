## Git (procedure and naming: `rigger-core:git-workflow`)

- Never commit on the default branch or force-push it, and nothing reaches it without my OK; the
  procedure is in `rigger-core:git-workflow`: a main session loads it before the first git write; a
  subagent gets its name in the brief.
- Every change that ends in a commit is made in its own worktree under the worktree root the
  SessionStart line names (unset: next to the repo, never inside it), on a `feature/`, `fix/` or
  `chore/` branch from the default branch, and reaches it through a pull request (a squash merge
  where the repo has no remote); the main checkout stays on the default branch and is only read and
  merged into. Exception: a project whose running app or container serves the main checkout (its
  CLAUDE.md or `.claude/environments.json` says so) is edited there, one writer at a time.
  Remove the worktree after the merge.
