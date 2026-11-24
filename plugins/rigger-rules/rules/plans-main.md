## Plans: main session

- A brief or full requirements never waive brainstorming or planning (with superpowers installed:
  {{slot:process.brainstorm}}, then {{slot:process.plans}}), the thinking pass (goal, reader,
  what exists, what can go wrong).
- Every spec and plan states its estimate in agent working hours, lines of code and of tests,
  the simpler version and what it drops.
- Plan size decides the process: more than 5 tasks, or touching security, data, migrations or
  shared infrastructure (auth, routing, build, schema): a reviewer subagent checks the plan first
  and execution is subagent-driven unless the work is mechanical; otherwise the main session
  implements it. Review once, at the end, by a strongest-model subagent; per task only for
  security, data, money and shared infrastructure.
- After a plan is approved, a plan with independent parts follows `/rigger-core:coordinate` unless
  I say to do it here; the rest runs here. Write `plan: <plan path>` into the state file
  frontmatter: the plan is my OK up to merges into the integration branch (where parts merge
  first). Fix in-scope findings; list others as follow-ups; a test that cannot pass blocks only
  its part. Ask me nothing else; stop only for these: set `stop:` to `main` (any merge into the
  default branch), `release`, `force-push`, `prod-data`, `secrets`, `goal-unreachable` or
  `app-refused`, then ask; clear `stop:` once I answer. The final summary sets `stop: done` and
  asks once about deleting branches; my answer, my stop or a new request removes `plan:` and
  `stop:`.
- Plan task headings: `### Task <integer>: …` (no letters or dots).
- **Lo-fi means no styling, not no design.** Design each screen from its role's task and decisions
  (what they must do, what they see, what comes next), with the UI that task needs: statuses,
  filters, history, empty and error states.
