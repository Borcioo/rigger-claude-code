---
tags: [native]
runs: 1
max_turns: 4
allowed_tools: [Read]
---

My Python test harness wipes temporary git repos with shutil.rmtree and fails on Windows with PermissionError on files under .git/objects. How do I fix the cleanup? Answer in at most three sentences.
