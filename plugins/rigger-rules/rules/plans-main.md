## Plans: main session

- A brief or full requirements never waive brainstorming or planning (with superpowers installed:
  `superpowers:brainstorming`, then `superpowers:writing-plans`): it is the thinking pass (goal,
  reader, what exists, what can go wrong), with questions only where needed. A side session (one
  started for a part of a plan, as `/rigger-core:coordinate` does) sends its questions and design
  to the session that started it, the coordinator, which brings me only a choice that changes
  scope, cost or what the user of the product gets.
- Estimates count agent working time, not manual hours. Every spec and plan states its estimate
  in hours and in lines of code and lines of tests separately (for a task whose checks are full
  under the Tests rules' risk bullet, tests are about as many lines as code), the simpler version
  and what that drops.
- Plan size decides the process: more than 5 tasks, or touching security, data, migrations or
  shared infrastructure (auth, routing, build, schema): a reviewer subagent checks the plan first
  and execution is subagent-driven unless the work is mechanical; otherwise the main session
  implements it. Review once, at the end, by a strongest-model subagent; per task only for
  security, data, money and shared infrastructure.
- After a plan is approved, do not ask me how to run it: a plan with independent parts follows
  `/rigger-core:coordinate` unless I say to do it here, and the rest you do here.
- Plan task headings: `### Task <integer>: …` (no letters or dots).
- **Lo-fi means no styling, not no design.** Design each screen from its role's task and decisions
  (what they must do, what they see, what comes next), with the UI that task needs: statuses,
  filters, history, empty and error states.
