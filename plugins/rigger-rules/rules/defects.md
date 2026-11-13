## Recurring defect classes — check while writing, not after review

- **Fail closed:** if a guard can be bypassed (filter skipped, hook suppressed, header missing),
  the result is deny/empty, never "everything". A null/false from a regex or decode is a refusal,
  never an empty value. Call a guard fail-closed only after listing the paths around it (another
  client, a followed redirect, an overridable function, the browser) and testing each.
- **Read authority ≠ write authority:** widening who can see something must not widen who can
  change it. Re-check every write path when visibility changes. A permission "must never" gets one
  matrix test: every entry point, including an existing handler that gains an effect, × every
  role/grant combination.
- **An identifier shown to a user is reserved:** every minting path honours it, or mint it eagerly.
- **Compare the way the framework compares:** match routes/paths as the router does
  (case, trailing slash, default ports, encoding); prefer identifying a route by its handler over
  a regex on the path.
  A test proves such a gate only when it probes a spelling the framework still accepts (changed
  case, trailing slash, alternative route form, percent-encoding), never the canonical one alone.
- **User-supplied URLs** used later for links or redirects: same-site check on input and on use.
- **Secrets:** hash at rest; HMAC with domain-separated keys; constant-time compare; bind signed
  links and proof tokens to something that changes when the subject is recreated or the credential
  they prove changes.
- **Stored raw text → every output path escapes**, including the platform's own render paths
  (feeds, admin lists, public APIs, search). Prove each with a test. Never derive a public handle
  from a private identifier (e-mail, login); sweep every output channel for it.
- **Pin and restore global state** (locale, timezone, env, hooks/filters, cwd, monkeypatches) in
  `finally`; a failure seen only in the full run → look for leaked global state first.
- **Cache-bust per file:** each asset's or module's version derives from that file, never from a
  sibling's hash; one test proves the URL changes when only that file does.
- **Each data migration gets its own version**, recorded only after its last step's result is
  checked (the table exists, the taxonomy is registered), as any "done" marker.
  Never file two changes under one version: sites that already ran it never run the second.
- **A phased script saves its state before the first check of a step,** and a precondition on state
  an earlier phase changes applies only to the phases before that change.
- **Every await inside a time budget has its own bound;** its test stubs a never-settling promise.
- **Text encoding is explicit:** never rely on the platform default when a script writes a file;
  pass UTF-8, and match text in Unicode mode. Source edits go through the Edit tool or
  `safe-edit.ps1 -Find -Replace -Count` (literal, counted; needs rigger-windows), never a heredoc or regex script.
  Decode a file that came from someone else or an e-mail strictly as UTF-8 and look for "?" where
  letters belong before using it. Never write a `\uXXXX` escape through the Write/Edit tool
  expecting a literal — build it at runtime or match the decoded text — and make every scripted
  replacement state the hit count it expects and fail on a mismatch.
- A script over ~10 lines, or one with nested quotes, goes through the Write tool into a scratch
  folder outside the repo (the session scratchpad when there is one) and runs by path, both calls
  in one message; a heredoc saves nothing and breaks on quoting.
