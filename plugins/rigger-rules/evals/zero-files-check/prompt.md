---
tags: [native]
runs: 1
max_turns: 4
allowed_tools: [Read]
---

I wrote a small link checker for our docs folder. When I point it at a folder that does not exist, or at one with no markdown files, it prints nothing and exits 0, and our CI treats exit 0 as a pass. Is it ready to merge? Answer in at most five sentences.
