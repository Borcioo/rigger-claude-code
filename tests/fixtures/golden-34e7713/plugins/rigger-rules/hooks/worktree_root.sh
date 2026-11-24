#!/bin/sh
# SessionStart: prints "Worktree root: <path>" when HARNESS_WORKTREE_ROOT is set and not empty,
# nothing otherwise. Always exits 0.
if [ -n "${HARNESS_WORKTREE_ROOT:-}" ]; then
  printf 'Worktree root: %s\n' "$HARNESS_WORKTREE_ROOT"
fi
exit 0
