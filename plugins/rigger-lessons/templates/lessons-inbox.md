# Lessons inbox

Lessons captured during sessions, waiting for curation with `/rigger-lessons:curate-lessons`.
Append one line per lesson under Entries, in English, with the Edit tool:

`- YYYY-MM-DD · scope · project · what happened · proposed rule · signal_type: <user-correction|test-failure|review-finding|agent-observation> · source_ref: <session#tool use id, PR URL or file:line> · target: <rules file or skill> · why: <stated reason> · review: YYYY-MM-DD`

`scope` is `global`, `skill:<name>` or `project:<name>`. Curation removes the entries it applied.
review is optional. An agent-observation can only propose a rule.
Curation adds ` · curated: YYYY-MM-DD` to an entry it leaves here, which then waits behind the others.

## Entries

