# rigger-lessons

The rigger-lessons plugin keeps a running inbox of lessons learned while working with Claude Code.

## What it does

At the start of every session its Python hook prints the inbox path and the number of open entries (it creates the inbox from a template when it is missing) and adds `Lessons due for review: N` when entries carry a `review:` date that has come. It also names 5 or more waiting curation proposals and 3 or more failed batch runs in a row (both only exist when background batches are on, below), in every folder. The capture rule comes from `rules/lessons.md`, injected on startup, clear and compact; subagents get a one-line rule from `rules/lessons-subagent.md` to report lessons in their report.

The `curate-lessons` skill walks through merging, sharpening and routing those entries into the rules where they belong. A `global` rule goes to `<config dir>/rigger/rules.d/lessons.md`, which the rigger-rules plugin prints at session start (install rigger-rules for the rule to take effect). `tools/inbox_lint.py <inbox>` checks the entry fields that curation relies on, and `--slice <inbox>` prints the 15 entries one curation run reviews. A PreToolUse hook on Edit and Write refuses a new inbox entry without those fields; an entry already in the file stays unchecked when it is changed in one Edit, a Write that changes it stays refused, and any error in the hook allows the edit. `HARNESS_LESSONS_INBOX` moves the inbox (a leading `~` is expanded); the hook starts only for a file named `lessons-inbox.md`, so an inbox under another name is not guarded. A PostToolUse hook on the Skill tool adds the entries scoped `skill:<name>` to the context of the skill just loaded, in a subagent too; a skill typed as a slash command does not call the tool, so it gets none.

`HARNESS_LESSONS_ARCHIVE` names an optional folder of curated case files. The `curate-lessons` skill (step 4) writes to it, `tools/lessons_lint.py` checks it, and the verify rule in rigger-rules searches it for an error text before debugging; nothing reads it at session start. Unset, there is no archive.

## Background batches (off by default)

Set `RIGGER_LESSONS_BATCH=1` in the environment (for example in the `env` block of `settings.json`) to turn them on. Then, when the inbox holds 5 or more entries without a `curated:` mark, the SessionStart hook starts `tools/lessons_batch.py` detached, at most every 2 hours (every 24 hours after 3 failed runs in a row), and prints nothing for it.

**Cost.** Each batch runs `claude -p` up to twice (triage on sonnet, then rule proposals on opus when triage wants one), billed to your Claude plan usage or API credit like any other session. The caps are $2 for triage and $3 for proposals per batch; a typical batch costs about $0.5 and takes about 1 minute. With the variable unset no batch ever starts.

A batch triages 5 entries and writes these files only: the inbox (a repeat's sources added to the older entry, ` · curated: <date>` on what it parks), `lessons-archive.md` next to the inbox (entries a rule or skill already covers, and the merged repeats) and `lessons-proposals.md` next to the inbox (rule and skill edits waiting for `curate-lessons`), plus its state dir `<config>/lessons-batch/` (stamp, lock, failure count, `attempts.json`, last failed log, `logs/` including `logs/spawn.log`, run records, inbox backups). It never edits rules, skills, hooks or a CLAUDE.md. Its model calls read the rules and skills in use, including `<config>/rigger/rules.d/`, from a temp copy `lessons-batch-<random>/corpus.md` in the system temp dir, removed after the run; each `claude -p` child also writes `.claude.json` and `backups/` in the config dir, as any `claude` command does.

`python "<plugin dir>/tools/lessons_batch.py" restore <run id>` undoes one run (the run id heads its archive section and its proposal blocks). When 5 or more proposals wait, the SessionStart hook says so, and after 3 failed runs in a row it prints `Lessons batch failed N runs in a row; last log: <path>.`
