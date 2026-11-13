---
name: e2e-playwright-speed
description: >-
  Load BEFORE writing or restructuring Playwright (or other browser) e2e tests, naming test gates
  in a brief, or when an e2e suite takes minutes, flakes in parallel, shards or runs
  --only-changed wrongly, shares one environment (wp-env, Docker, staging DB), returns 401/429, or
  several agents test on one machine.
---

# Fast e2e without losing coverage

## 1. Measure before changing anything

Run the full suite once with `--reporter=list,json` and record wall clock, global setup/teardown,
per project and per spec. Save it in the repo (`docs/knowledge/e2e-speed.md`), with Before and After sections. The
baseline is the proof of the speed-up.

Time almost never goes into the assertions. Look for:

| Where time hides | Typical cost | Fix |
|---|---|---|
| UI login in every test | 3–8 s per test | sign each role in once and save `storageState`; load it per test |
| CLI calls per test (WP-CLI, artisan, docker exec) | 3–4 s each (process + framework boot) | do data setup through the HTTP/REST API; batch the CLI work into one `eval` |
| `workers: 1`, `fullyParallel: false` | the whole suite runs in series | parallel projects (below) |
| global setup running steps one after another | tens of seconds | run independent setup steps concurrently (`Promise.all`) — but never several calls to a CLI that writes shared state (§5) |
| real third-party requests (avatars, CDNs, fonts) | slow and flaky | answer them locally in a base fixture, with a body of the right type (an image body for image URLs; a `text/plain` stub hangs image-inlining libraries) |
| video / trace on every test | disk and CPU | `trace: 'retain-on-failure'`, `screenshot: 'only-on-failure'`, no video |

## 2. Structure: parallel by default, serial by exception

- **Parallel projects** (`fullyParallel: true`, workers from an env var; default 2 on a shared dev
  machine — more only in CI or when nothing else runs — at most ~4 per backend: one PHP or DB server
  answers every worker, and more workers only queue).
- **Every test owns its data.** It creates its own pages, records and users, named with a marker
  unique to the project, the worker and the start time. Assertions read only that data. A count
  that spans the whole site becomes "one or more", never an exact number.
- **Per-test accounts where the product rate-limits per account.** A shared account in parallel hits
  the limit (429s that look like flakes).
- **Serial project** (`*.serial.spec.ts`, `workers: 1`, `dependencies` on the parallel projects) for
  specs that change what every test sees (settings, options, roles, limits, language) or whose
  measurement moves with other tests' data (layout bounds, global counts).
- **Find hidden coupling deliberately:** run the parallel projects several times with
  `--workers=4`. A spec that fails only there either gets its own data or moves to serial. Record
  the symptom, the cause and the fix in the doc.
- Keep **exactly one** spec that signs in through the real login form, so that path stays covered.
- **A project meant for another engine sets `browserName` itself.** `devices['iPhone 13']` sets
  only `defaultBrowserType`, and a `browserName` in the top-level `use` wins over it, so a
  "webkit-iphone" project can silently run Chromium. Check once that each project runs the
  engine its name says (print the `browserName` fixture in one run).

## 3. Gates: which command when

| When | Command | Runs |
|---|---|---|
| While working on a task | `e2e:changed` (`--only-changed=<base>`) | specs that import a changed file |
| Per task / per fix round | `e2e:quick` | parallel projects, one engine (chromium) |
| Checkpoint (merge, before a release, shared infrastructure touched) | full suite, or shards | everything, every engine |

Rules for agents and briefs:

- **Every dispatch brief names its gate commands.**
- A focused run that passes 15 minutes is stopped and reported, not waited on.
- The full multi-engine suite runs once per checkpoint (a merge of finished work, or before a
  release), started by the session that dispatches the agents, not by each agent.
- **Pass `--workers` on every local Playwright command.** Running one serial spec also pulls in its
  dependencies: add `--no-deps`, or the whole parallel suite runs at the default workers.
- **A changed-only selector must fail closed.** Playwright's `--only-changed` sees only files a spec
  imports. Code the browser or server loads (admin bundles, `build/`, PHP, translations, CSS) selects
  nothing. Map every shipped path to the specs that load it, run the full suite for an unmapped path,
  and keep a guard test that fails when a shipped file matches no rule.
- **Gate on every spec that exercises the changed module, never on a filename family.**
- **The map test must see uncommitted specs.** `git ls-files` lists a new spec only after it is
  committed, so the map test goes red one commit late: read staged and untracked files too
  (`git ls-files -co --exclude-standard`), and name "add the new spec to the map" in the brief's
  gates.

## 4. Sharding pitfalls

- **Playwright runs a project's dependencies in full in every shard.** Sharding the whole config
  makes each shard repeat every parallel test. Run two invocations: the parallel projects sharded,
  then the serial projects sharded with `--no-deps`. The exit code fails if either half fails.
- **Each invocation needs its own `--output` folder.** Playwright empties the output folder at the
  start of every run, so a second invocation (or a parallel shard) deletes the first one's failure
  traces and screenshots.
- Shards on different environments test that environment's server code. They are valid only when
  that checkout is at the same commit.

## 5. Shared test environments

When two runs share one backend (two branches, two agents, a stream and a checkpoint):

- **Unique credential names per run.** If setup or teardown deletes "the e2e app password/token" by
  a fixed name, run A revokes run B's credentials, and B gets 401s that look like product bugs. Take
  the name from an env var, default it per shard, and delete only your own name.
- **Leftover purge is time-bounded.** Delete marked test data only when it is older than a cutoff
  (e.g. 2 h). Compare against the server's clock, not the host's.
- **Global state undo collides.** The first run to finish restores settings and language while the
  other still runs, so use one run per environment at checkpoints.
- Never edit product code, `git stash`, reset or check out another commit in a worktree a running
  suite reads.
- **Before trusting a borrowed environment, write down which code each process it runs serves**
  (CLI, web server, queue worker), and pin a test that reaches a process you cannot remount to your
  own environment. Check what the containers mount
  (`docker inspect <container> --format '{{json .Mounts}}'`). wp-env keys its instance by
  directory, so a run from a worktree reaches the main checkout's containers: its PHP comes from
  that checkout while assets come from the worktree, and shards mounted into `tests-cli` only still
  hit the owning checkout's web server — which may not even be running.
- **A CLI that keeps state in a read-modify-write file is not safe to call concurrently.** `wp-env`
  rewrites `~/.wp-env/<hash>/wp-env-cache.json` on every `wp-env run`; concurrent calls corrupt it
  (symptom: "Environment not initialized" with every container up; repair: write
  `"runtime":"docker"` back). Serialise `wp-env run` calls; before parallelising any tool's CLI,
  find where it writes state.
- Before trusting a red run on a shared environment, check whether another run is active on it.

## 6. Coverage must not move

- Do not delete, skip or loosen an `expect` to get speed. Do not add retries or `fixme` to hide a
  flake. Moving a check into a fixture or setup is fine, if setup fails the run when it fails.
- Prove it: diff `tests/e2e` and list every removed or changed `expect`. A reviewer checks the
  list.
- A stub that hides a product bug (e.g. a delay that makes a race disappear) is labelled temporary
  in the code and the doc, names the bug's task, and is scoped as narrowly as possible. When the
  bug is fixed, the stub goes and the test must pass without it.

## 7. Node notes

- Node's `fetch` reusing a keep-alive socket the server closed gives a one-off `ECONNRESET` on the
  first request. Retry the setup request, not the test.

## 8. Flakes: look before theorising

- Log what the test saw at the moment it read it (element rect, computed `display`, URL, count)
  before forming a theory.
- Never compute a point in one round trip and click it in another without asserting that the rect
  is the styled one; prefer locator actions, which re-resolve.
- **Measure a surface once it has settled.** Wait for the final content (the async preview loaded)
  or poll until two reads agree, then assert; prove it with a red on the old code.
- **Nothing animating.** Before axe, a screenshot or a measurement, wait until
  `document.getAnimations()` has nothing running — ignoring infinite ones (spinners), or the wait
  never ends.
- Before calling a parallel speed-up neutral, re-run pixel and layout specs on every viewport:
  other workers' seeded data (pages listed in a theme header) moves layout bounds. When changing
  shared fixtures, keep generated names the same length.

## 9. Shared machine

Budget (how many agents and workers): `workers=2` while other agents are busy; on Windows with
rigger-windows installed, `rigger-windows:windows-dev-env` "Machine load" adds the watchdog and
crash diagnosis. This section owns how one-run-at-a-time is enforced.

- **One run at a time is enforced by the run, not by a check.** Global setup takes a machine-wide
  lock file (exclusive create, pid and checkout inside; wait while the holder lives, take over a
  stale one). Heavy unit suites (jsdom) belong under the same lock.
