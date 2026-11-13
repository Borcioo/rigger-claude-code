---
tags: [native]
runs: 1
max_turns: 4
allowed_tools: [Skill]
---

I wrote a one-shot PowerShell script for our release: it runs gh pr create for the version bump, then gh pr merge with the PR number parsed from the output, then tags the merge. The create step failed once with an error and the script went on anyway. What must the script do differently? Answer in at most five sentences.
