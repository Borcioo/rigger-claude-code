## Learn continuously, without being asked (hard rule)

- Signals: a review finding, a bug you caused, a test that could not fail, an extra fix round, a
  user correction or "why does this keep happening?" (a second occurrence), wasted time (slow
  test runs, idle waiting, rework), a wrong assumption in a plan.
- When it happens, scope it: `global`, `skill:<name>`, or `project:<name>` when only that project
  would hit it. A `project:` lesson is not an inbox entry: propose it in one line for that
  project's CLAUDE.md ("Pitfalls", created if missing) and add it after the user's OK. For the
  others:
  1. Search the inbox the lessons SessionStart line names, the rules files and the skill the lesson belongs to for the
     same class; a hit is a second occurrence (below). Find a skill's text under `plugins/cache/` in the config dir,
     or in its source repo when you have a clone.
  2. Main session: when step 1 found the class in the inbox, add this case's source_ref to that
     entry (and its signal_type, if stronger) instead of a new entry; otherwise append it to the
     inbox at once, in English, with the Edit tool (date · scope · project · what happened ·
     proposed rule · signal_type · source_ref · target · why, optional review date; low bar),
     tell the user in one line and put it into every brief dispatched after it.
     Subagent: put it in your report under "Lessons"; the main session
     appends it.
     `signal_type` is the evidence behind the signal, not its name: `user-correction`,
     `test-failure`, `review-finding`, or `agent-observation` for what only you saw (an extra fix
     round, wasted time, a wrong assumption nothing has tested yet). Each field is `key: value`; a
     hook refuses a new entry without them.
- Curation is on request. When the SessionStart line reports waiting proposals, or the inbox has
  grown, propose `/rigger-lessons:curate-lessons` in one line (only in a session the user drives;
  a session another session started never proposes or runs curation) and, after the user's OK, run
  it at the end of the current task: a strongest-model subagent checks each proposal (general?
  already covered? precise and short?); rules go where their scope says; a project class becomes a
  skill or global rule only when a second project has it; then clear the entries. Merge
  duplicates, sharpen vague rules, delete rules that proved wrong, keep the rules files short: cut
  before you add.
- Background batches are off by default. When the user sets `RIGGER_LESSONS_BATCH=1`, the
  SessionStart hook starts a batch of 5 entries at most every 2 hours; it calls `claude -p`, which
  uses the user's plan or API credit (`plugins/rigger-lessons/README.md`: cost per batch). A batch merges repeats, moves entries a rule or
  skill already covers to `lessons-archive.md` and queues rule and skill edits in
  `lessons-proposals.md`, both next to the inbox.
- One curation run reviews one slice of the inbox, at most 15 entries, those past their review
  date first, then those with evidence; an entry it leaves in the inbox waits behind the others.
- A `global` rule goes to `<config dir>/rigger/rules.d/lessons.md` (`$CLAUDE_CONFIG_DIR`, else
  `~/.claude`), created if missing: the rigger-rules loader prints every file in that folder at
  session start, so the rule applies from the next session (without rigger-rules installed nothing
  prints that folder). Files in the plugin cache are never edited, and nothing needs updating.
  Each rule is one short bullet under a heading for its topic. To suggest a rule for the Rigger
  plugins themselves, draft an issue body for `Borcioo/rigger-claude-code` into a local file and
  file it only after the user's OK.
- Second occurrence = process failure: a written rule was not enough. Propose a process guard
  (shared helper, a lint or test that fails on the pattern, template, review-checklist item, hook)
  the way Scope of changes says (if rigger-rules is installed), and record it. A process guard must cost less than it prevents:
  measure it before claiming or proposing it (ms and spawned processes on a matching and a
  non-matching call; tokens; a detector's catch and false-positive rate over the real
  corpus), prefer zero-cost fixes, scope hooks to the commands they check.
