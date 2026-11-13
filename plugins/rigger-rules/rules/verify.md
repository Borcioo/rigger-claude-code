## Verify in the real environment early, and show me

- After each task that changes what I see (a coherent visible change, not every edit), do a short
  hands-on check in the real app (browser, real data, my locale; look at caches, fonts, scroll,
  theme CSS, permissions), then send me a screenshot and one short question ("is this what you
  want?"). A subagent does the check and reports; the main session sends the screenshot.
- **A copy of production carries production keys.** Before the first test run or hands-on check on
  a copy of a live site or database (local or staging), and again after every sync or import into
  it, list the third-party integrations it can reach (analytics, e-mail marketing, webhooks,
  payment, SMTP), switch each to a test key or off and record it in the project's CLAUDE.md with
  the date of the copy; a new integration id or key goes to production only. Send test mail only
  to an address whose delivery you have confirmed (check the MX before trusting `+tag`).
- Evidence before cause: read the raw log, response or value where it happened, then diagnose.
  Before reporting something removed or fixed, check where it is loaded or served from now. Before
  blaming code for a before/after mismatch, list what you ran in between; snapshot the baseline
  before the first step. Async work (a queued job, a bulk API, a UI still filling) is judged after
  it settles: poll, don't peek. Before debugging an error, `rg -i` its text or the tool's name in
  the folder the `HARNESS_LESSONS_ARCHIVE` environment variable names, when it is set. To answer "did X ever happen", aggregate
  (count per day and per endpoint, files in explicit chronological order) and read the newest file
  on its own; never tail a concatenation.
