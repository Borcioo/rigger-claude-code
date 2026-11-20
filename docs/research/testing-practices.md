# Software testing practices: a verified reference

A reference built only from authoritative sources: engineering books and blogs of large
engineering organisations, recognised experts' own sites, official tool documentation and
peer-reviewed papers. Every claim carries a source key in brackets, resolved in [Sources](#sources)
(title, author or organisation, date, URL). All URLs were fetched on 2026-11-16.

How to read the marks:

| Mark | Meaning |
|---|---|
| **WA** | Widely agreed: two or more independent organisations or authors say it. |
| **1** | One organisation's practice or one author's view. It may still be good advice. |
| ⚖ | The sources disagree; each side is given, none is picked silently. |
| Check `V` / `S` (in Sources) | `V`: the passage was read in context on the fetched page. `S`: the quoted snippet was found on the fetched page by a script (case- and punctuation-insensitive match); the surrounding context was read by the research pass, not re-read. |

Numbers are the sources' own, for their own codebases and dates; they are not benchmarks for
other projects. Preprints are labelled as such. What could not be verified is listed in
[Not verified](#not-verified).

Contents: [1 Principles](#1-principles) · [2 Test types](#2-test-types) ·
[3 What to test and what not](#3-what-to-test-and-what-not) · [4 Test doubles](#4-test-doubles) ·
[5 Flakiness](#5-flakiness) · [6 Speed and parallelism](#6-speed-and-parallelism) ·
[7 Test data and fixtures](#7-test-data-and-fixtures) · [8 Metrics: coverage and mutation score](#8-metrics-coverage-and-mutation-score) ·
[9 Tooling per stack](#9-tooling-per-stack) · [10 AI coding agents and tests](#10-ai-coding-agents-and-tests) ·
[Not verified](#not-verified) · [Coverage of this reference](#coverage-of-this-reference) ·
[Sources](#sources)

## 1. Principles

### 1.1 What a test is for

- A failing test does not itself help the user; only the bug fix does. A testing strategy is
  judged by how well it lets developers find, fix and prevent bugs, not only by how many bugs it
  reports. **1** [GTB-e2e-2015]
- The ideal feedback loop is fast, reliable and isolates the failure to a small amount of code.
  **WA** [GTB-e2e-2015] [SWE-11] [F-pyramid]
- Tests can be too many: "You are testing too much if you can remove tests while still having
  enough", or if small code changes force long test changes. **1** [F-coverage]
- Kent Beck (2008): he tests as little as possible to reach a given level of confidence, skips
  kinds of mistakes he does not make, and tests more where logic is complicated or where the team
  has erred before. **1** [Beck-SO-2008]
- Google's "Beyoncé Rule": test everything you do not want to break, including failure handling,
  performance and security. **1** [SWE-11]
- ⚖ How much to test. Beck's "as little as possible for a given confidence" and Google's "test
  everything you don't want to break" pull in different directions. (Our synthesis, not a
  source's: both spend tests where breakage would hurt.) [Beck-SO-2008] [SWE-11]

### 1.2 Properties of a good test

- Kent Beck's Test Desiderata lists twelve properties: isolated, composable, deterministic, fast,
  writable, readable, behavioural, structure-insensitive, automated, specific, predictive,
  inspiring. Some support each other and some interfere (more predictive of production makes
  tests slower). **1** [Beck-desiderata]
- Microsoft's list for unit tests: fast (milliseconds), isolated, repeatable, self-checking,
  timely; four of the five overlap with Beck's list. **WA** [MS-dotnet] [Beck-desiderata]
- The ideal test is unchanging: it changes only when requirements change. A brittle test fails on
  an unrelated change that introduces no bug; pure refactorings should need no test changes.
  **WA** [SWE-12] [Beck-desiderata] (structure-insensitive) [Dodds-write]
- Tests should be clear on inspection: no logic (loops, conditionals) in tests; needing a test to
  verify your test means "something has gone wrong". **1** [SWE-12]
- DAMP over DRY in test code: readable, self-contained tests are worth some duplication; DAMP
  complements DRY rather than replacing it. Tests state the values they care about directly.
  **1** (Google, book and blog) [SWE-12] [GTB-damp-2019]
- A good failure message states the desired outcome, the actual outcome and the relevant
  parameters, so the failure can be diagnosed without opening the test. **1** [SWE-12]

### 1.3 Size and scope

- Google separates two dimensions: **size** (resources a test needs) and **scope** (code paths it
  verifies). Small = one process, medium = one machine (network only to localhost), large = may
  span machines. **1** [SWE-11]
- Google picked size because speed and determinism matter most, and encourages small tests
  "whenever possible, regardless of the scope". **1** [SWE-11]
- Google's 2010 size table enforces limits (time limit 60 / 300 / 900+ s; small tests have no
  network, database, file system or sleep). **1** [GTB-sizes-2010]
- Microsoft also classifies tests by their dependencies and run time (levels L0–L4, alongside the
  names unit and functional tests). (Our reading: Google and Microsoft both classify by what a test
  needs.) **1** [MS-shiftleft]
- Microsoft: write tests at the lowest possible level; favour tests with the fewest external
  dependencies. Vocke (Thoughtworks) says the same; Spotify and Dodds disagree, see 1.4. **WA**
  [MS-shiftleft] [Vocke-2018]

### 1.4 The shape debate: pyramid, trophy, honeycomb

| Shape | Says | Source |
|---|---|---|
| Pyramid | Many fast unit tests, fewer integration, few end-to-end; push tests "as far down the test pyramid as you can"; don't duplicate across layers. | [F-pyramid] [Vocke-2018] [GTB-e2e-2015] [SWE-11] |
| Ice-cream cone (anti-pattern) | Mostly end-to-end, few unit tests. | [GTB-e2e-2015] [SWE-11] [F-pyramid] |
| Hourglass (anti-pattern) | Many unit and many end-to-end, few integration tests. | [GTB-e2e-2015] [SWE-11] [GTB-hourglass-2020] |
| Honeycomb (Spotify, microservices) | Mostly integration tests, few "implementation detail" tests, very few integrated tests; the pyramid "can be actively harmful" for microservices. | [Spotify-2018] |
| Trophy (Kent C. Dodds, frontend) | Static analysis at the base, then unit, a large integration layer, few end-to-end; "Write tests. Not too many. Mostly integration." | [Dodds-trophy] [Dodds-write] |

- ⚖ **The split.** Google's 2015 blog suggests 70/20/10 (unit/integration/e2e) as a first guess;
  Google's 2020 book says roughly 80/15/5 by test count. Both say each team's mix differs.
  [GTB-e2e-2015] [SWE-11]
- ⚖ **The shape.** Google and Vocke defend the pyramid; Spotify calls it harmful for
  microservices; Dodds' trophy favours integration, and he says he drew it for his own codebase
  and never considered whether it applied to microservices or backend services. [Spotify-2018]
  [Dodds-trophy]
- **Fowler's resolution (2021):** the camps use "unit" differently. Honeycomb and trophy "unit"
  means a solitary (mocked) test and their "integration" is what Fowler calls a sociable unit
  test, which "makes the pyramid versus honeycomb discussion moot". He quotes Justin Searls:
  debating percentages is a distraction; write expressive tests with clear boundaries that run
  fast and fail for useful reasons. **1** (Fowler; Dodds agrees definitions vary) [F-shapes-2021]
  [Dodds-trophy] [F-unit]
- ⚖ **How much end-to-end.** Google: rely mostly on e2e tests is a bad idea in practice.
  Fowler's own footnote: the pyramid assumes broad tests are slow and brittle; if they are fast,
  reliable and cheap to change, lower-level tests are not needed; he links an account (LMAX) where
  e2e tests played a large, valuable role. Dodds: confidence per test grows toward e2e.
  [GTB-e2e-2015] [F-pyramid] [Dodds-write]
- When a high-level test finds a bug, first reproduce it with a lower-level test; the high-level
  test is a second line of defence. **1** (Fowler and Vocke, both Thoughtworks) [F-pyramid]
  [Vocke-2018]
- **Evidence.** No controlled study comparing the shapes (defects found per cost) was found.
  Dodds quotes Tim Bray that testing tenets are not scientific knowledge; a 2022 multivocal review
  of test-automation maturity found few practices evaluated with formal empirical methods (STVR
  2022). (Our conclusion: treat every shape as a heuristic.) [Dodds-trophy] [Wang-MLR-2022]

## 2. Test types

Vocabulary first: "unit" and "integration" have no single meaning. Fowler distinguishes
**solitary** unit tests (collaborators replaced by doubles) from **sociable** ones (real
collaborators), and **narrow** integration tests (only the code that talks to one external
service, with a double for that service) from **broad** ones (live versions of all services).
A document or brief should say which it means. **1** [F-unit] [F-integration]

### 2.1 Unit tests

- Low-level, small scope, expected to be much faster than other tests; "one tenth of a second is
  considered slow for unit tests" at Google. **WA** [F-unit] [GTB-e2e-2015] [MS-dotnet]
- They isolate failures well but cannot show that units work together; they are "limited by the
  imagination of the engineer writing them". **1** (Google, two sources) [GTB-e2e-2015] [SWE-14]

### 2.2 Integration tests

- An integration test of a small group of units (often two) catches most "do they work
  together" bugs without an e2e test. **1** [GTB-e2e-2015]
- Narrow integration tests (one integration point, doubles for the rest) plus contract tests give
  faster, more independent tests than broad ones. **1** (Thoughtworks authors) [F-integration]
  [Vocke-2018]
- Risk of narrow tests: the double may not be faithful; pair them with contract tests. **WA**
  [F-integration] [Pact]
- Google: a team moved from an hourglass to a pyramid by adding medium-sized integration tests
  where e2e tests were "slow, unreliable, and difficult to debug". **1** [GTB-hourglass-2020]

### 2.3 End-to-end tests

- Pay off for user-level confidence and for what smaller tests cannot see (configuration,
  emergent behaviour); configuration changes are Google's number one cause of major outages. **1**
  [SWE-14]
- Cost: slow feedback, flakiness, failures that are hard to localise ("that bug could be
  anywhere in the product"), and upkeep. **WA** [GTB-e2e-2015] [F-pyramid] [Spotify-2019]
- Keep them few: one per important use case or error class, only for what smaller tests cannot
  check. **1** [GTB-e2e-good-2016]
- Larger tests need clear ownership or they rot. **1** [SWE-14]
- Beyond pre-release tests Google also uses A/B diff tests, probers against production and canary
  analysis. **1** [SWE-14]

### 2.4 Contract tests

- A contract test checks that a test double still behaves like the real external service, and
  detects contract changes that would break production. **WA** [F-contract] [Pact]
- Fowler: contract tests run on the rhythm of the external service (often daily is plenty), and a
  failure need not break the build the way a normal test failure does. They check the contract of
  calls, not necessarily the exact data. **1** [F-contract]
- Consumer-driven contracts (Pact): the contract is generated while the consumer's tests run.
  Pact presents it as an advantage that only the parts of the interface consumers actually use get
  tested, so provider behaviour no consumer uses is free to change without breaking tests. (Our
  inference: such behaviour is then not covered by the contract.) **1** [Pact]
- ThoughtWorks Technology Radar: consumer-driven contract testing is an essential part of a
  mature microservice test portfolio; writing Pact tests alone does not mean you have
  consumer-driven contracts. **1** [TW-CDC]

### 2.5 Property-based tests

- You state a property that must hold for all inputs in a described range; the library generates
  random inputs, including edge cases you did not think of. **WA** [Hypothesis] [fast-check]
  [QuickCheck] fast-check also shrinks a failure to the smallest input that still reproduces it;
  Hypothesis' API reference shows it shrinks generated examples (explicit `@example` cases are
  not shrunk). **1** per tool [fast-check] [Hypothesis-api]
- Pays off where example-based tests rely on the author imagining failures; even experienced
  developers miss edge cases. **1** [fast-check-why]
- Cost: non-trivial data needs custom generators (QuickCheck paper); in Hypothesis, explicit
  `@example` cases are not shrunk. **1** [QuickCheck] [Hypothesis-api]

### 2.6 Snapshot tests

- Catch unexpected changes in serialisable output (UI trees, API responses, logs, error
  messages). Commit snapshots and review them as code. **WA** [Jest-snapshot] [Vitest-snapshot]
  Keep them short and focused, and make them deterministic. **1** [Jest-snapshot]
- Failure mode: developers regenerate a failing snapshot instead of finding the cause; Jest's docs
  say to "fight against the habit"; Dodds quotes a critique that most developers just "nuke the
  snapshot", and says snapshots longer than a few dozen lines suffer major maintenance problems.
  **WA** [Jest-snapshot] [Dodds-snapshot]
- Snapshots are not visual regression tests: they compare serialised values, not pixels. **1**
  [Jest-snapshot]
- ⚖ Tool docs promote snapshots with discipline rules; the expert critique says the discipline
  erodes; neither side gives measured data. [Jest-snapshot] [Dodds-snapshot]

### 2.7 Mutation tests

See [8.2](#82-mutation-testing): mutation testing measures whether tests detect injected faults.
(This reference treats it as a quality signal for tests; that grouping is ours.)

## 3. What to test and what not

### 3.1 Test behaviour, not lines or methods

- One method can show many behaviours and one behaviour can span methods; a 1:1 mapping of tests
  to methods is harmful; a test name that mirrors the method name is a bad sign. Write one test
  per behaviour. **1** (Google, two sources) [GTB-behaviors-2014] [SWE-12]
- Test through public APIs, the way callers use the code. **WA** [SWE-12] [GTB-behavior-impl-2013]
  [MS-dotnet] [Vocke-2018] [Dodds-impl] [TL-principles]
- Private methods are implementation details; test them through public methods. Needing to test
  one directly hints at a design problem. **WA** [MS-dotnet] [Vocke-2018]
- An implementation-detail class reached only through a public API usually needs no tests of its
  own, unless it is complex or hard to reach through the API. **1** [GTB-public-2015]
- Testing implementation details gives false negatives (tests break on a refactor) and false
  positives (tests stay green when the app is broken). **WA** (Dodds; same idea in Google and
  Spotify) [Dodds-impl] [SWE-12] [Spotify-2018]
- Spotify keeps "implementation detail tests" for parts that are naturally isolated and
  internally complex (for example parsing). **1** [Spotify-2018]

### 3.2 Tests that should not exist

- **Change-detector tests**: a test that restates the implementation (for example a mock that
  verifies each internal call in order) breaks on any change, verifies no behaviour and has
  "negative value"; rewrite or delete it. **1** (term is Google's) [GTB-change-detector-2015]
- **Tests for coverage's sake**: copy-pasted or low-value tests written to hit a number create
  maintenance cost. **WA** [GTB-cov-2020] [F-coverage] Google's mutation paper, citing earlier
  work, says tests written merely to raise coverage tend to be ineffective at finding faults (a
  secondhand statement here). [GMut-ICSE-2021]
- **Tests for unproductive mutants**: tests that pin values nobody cares about (Google's example:
  a collection's initial capacity) become brittle change detectors. **1** [GMut-TSE-2021]
  [GTB-mutation-2021]
- **Tests chasing 100% coverage** end up testing logic-free code and implementation details. **WA**
  [Dodds-write] [GTB-cov-2020] [F-coverage]

### 3.3 Cover more behaviour with less test code

- Push a test as far down as it can go while still proving the behaviour; avoid duplicating the
  same check at several layers. **WA** [Vocke-2018] [MS-shiftleft]
- Parameterised tests (data providers, datasets, `parametrize`) run one behaviour over many
  inputs in one test body. **WA** [PHPUnit-writing] [Pest-datasets] [pytest-parametrize]
- Property-based tests replace many hand-picked examples with one property (see 2.5). [Hypothesis]
  [fast-check]
- Separate behaviours into separate tests so a new behaviour adds a test instead of growing an
  old one. **1** [GTB-behaviors-2014]

## 4. Test doubles

### 4.1 Vocabulary (Meszaros, via Fowler and Google)

| Double | What it does |
|---|---|
| Dummy | Passed but never used. |
| Stub | Returns canned answers; "no logic". |
| Spy | A stub that records how it was called. |
| Mock | Pre-programmed with expectations; verifies calls (behaviour verification). |
| Fake | A working, simplified implementation (for example in-memory). |

**WA** [F-mocks] [F-double] [GTB-doubles-2013] [Meszaros]

### 4.2 What to prefer

- Google prefers a real implementation when it is fast, deterministic and has simple
  dependencies; when it is not feasible, a fake is often the best option; interaction testing is
  to be avoided where possible. **1** (Google) [SWE-13] Fowler's "classical" school likewise uses
  real objects where possible. [F-mocks]
- Prefer testing state over interactions; test interactions only when how the result is produced
  matters, and in general only for state-changing functions. **1** [SWE-13] [GTB-state-2013]
- Overusing mocks or stubs leaks implementation details into tests and makes them unclear and
  brittle while assuring less. **1** [SWE-13] [GTB-mocks-2013]
- Fakes must keep fidelity to the real API contract, need their own tests against the real
  implementation, and are best written by the team that owns the real code. **1** [SWE-13]
  [GTB-fake-2013]
- Avoid overspecification: assert only what the behaviour under test needs. **1** [SWE-13]
- ⚖ **Schools.** Fowler describes two legitimate styles: classical (real objects where possible,
  sociable tests) and mockist (a mock for any object with interesting behaviour, solitary tests).
  Google sides with classical. Testcontainers' vendor docs: test against the same type of service
  as production, "without mocks or in-memory services". Laravel ships framework fakes
  (`Http::fake`, `Queue::fake`). (Placing these on one scale is our reading.) [F-mocks] [SWE-13]
  [Testcontainers-guide] [Laravel-http-client] [Laravel-queues]

### 4.3 Real dependencies

- Testcontainers runs throwaway databases, brokers and browsers in Docker so tests use real
  dependencies. **1** [Testcontainers]
- Laravel: `Http::preventStrayRequests()` makes any request without a fake throw instead of
  reaching the network. **1** [Laravel-http-client]
- Laravel advises against mocking the `Request` or `Config` facades: pass input to the HTTP test
  helpers, use `Config::set`. **1** [Laravel-mocking]
- Playwright: do not test third-party sites you do not control; mock them through the network
  API, or replay recorded traffic (HAR). **1** [PW-bp] [PW-mock]
- pytest: `monkeypatch` changes are undone after the test; patching builtins such as `open` is not
  recommended; make dependencies explicit in your own code. **1** [pytest-monkeypatch]

## 5. Flakiness

### 5.1 Size of the problem

- Google (2016): about 1.5% of all test runs report a flaky result; almost 16% of tests have some
  flakiness; about 84% of observed pass-to-fail transitions involve a flaky test; the flake
  insertion rate is about the same as the fix rate. **1** [GTB-flaky-2016]
- Google (2017): about 63,000 of 4.2 million tests had a flaky run in a week (under 2%). **1**
  [GTB-flaky-2017]
- Google's book: at 0.1% flakiness per test and 10,000 tests a day, about ten flakes a day need
  investigating; near 1% flakiness, tests begin to lose value. **1** [SWE-11]
- Developers learn to dismiss failures as flaky, including real ones. **WA** [GTB-flaky-2016]
  [Meta-flaky-2020] [MS-Lam-2019]
- Microsoft (ISSTA 2019): a few distinct flaky tests can cause a substantial share of failing
  builds, and ignoring flaky failures is dangerous because some are real faults. **1**
  [MS-Lam-2019]
- The cost is lost confidence: once flaky tests were tolerated, "a red build did not mean anything
  any more" (Thoughtworks); Spotify calls lost confidence the real cost. **WA** [TW-go-2012]
  [Spotify-2019]
- A survey of 76 papers (ACM TOSEM 2022) cites a developer survey in which 59% deal with flaky
  tests monthly, weekly or daily. **1** (figure quoted secondhand by the survey) [Parry-2022]

### 5.2 Causes

- Open-source study of 201 fix commits (Luo et al., FSE 2014): the top three causes are **async
  wait** (45% of classified commits), **concurrency** and **test order dependency**; 78% of flaky
  tests were flaky when first written. **1** (one study) [Luo-2014] Asynchrony as the leading
  cause is confirmed in Microsoft's projects (ICSE 2020); the TOSEM survey names asynchrony and
  concurrency as leading causes and order-dependent tests as a common test-side type. **WA**
  [MS-Lam-2020] [Parry-2022]
- Most flaky tests are flaky from the start: 75% of 245 flaky tests in 55 Java projects were flaky
  when added (OOPSLA 2020), close to Luo et al.'s 78%. **WA** [Lam-OOPSLA-2020] [Luo-2014]
- Unfaked external resources, time above all, make tests non-hermetic (Dropbox). **WA**
  [Dropbox-2019] [F-nondet]
- Fowler's five causes of non-determinism: lack of isolation, asynchronous behaviour, remote
  services, time, resource leaks. **1** [F-nondet]
- Size predicts flakiness: at Google 0.5% of small, 1.6% of medium and 14% of large tests were
  flaky in a week; binary size (r² 0.82) and RAM use (r² 0.76) correlate with flakiness. Some
  tools have higher rates (WebDriver, Android emulator), but size predicts flakiness better than
  the tool. **1** [GTB-flaky-2017]
- Larger tests use more infrastructure and are flakier; sleeps and timeouts fail when the machines
  running tests are overloaded. **WA** [SWE-14] [GTB-flaky-2017]

### 5.3 Fixes

- Replace sleeps with waiting for the condition (poll for the state transition, `waitFor`,
  auto-waiting assertions). In Luo et al., 54% of async-wait flakes were fixed with a `waitFor`.
  **WA** [F-nondet] [SWE-11] [Luo-2014] [PW-actionability] [PW-bp] [TL-async]
- Fix order dependency by cleaning shared state between tests (74% of such fixes in Luo et al.);
  make every test hermetic: it sets up and tears down its own environment and assumes nothing
  about order. **WA** [Luo-2014] [SWE-11] [PW-bp]
- Control time: wrap the clock so tests can freeze it. **WA** [F-nondet] [Laravel-mocking]
- Reduce scope: minimising flakiness starts with reducing the scope of the test. **WA** [SWE-14]
  [GTB-flaky-2017] [Spotify-2019] (e2e tests "are flaky by nature. Write fewer of them.")
- Detect before merge: run new and changed tests many times before they join the suite
  (Playwright `--repeat-each`, Spotify's Flakybot). Detectors on added or modified tests catch 85%
  of flaky tests; the other 15% turn flaky through other changes, so whole-suite reruns are still
  needed. Adding CPU and memory stress during reruns raises detection. **WA** [PW-cli]
  [Spotify-2019] [Lam-OOPSLA-2020] [Parry-2022]
- Find order dependence by shuffling: PHPUnit `--order-by=random` with `--random-order-seed`,
  Vitest `sequence.shuffle`, Jest `--randomize` (within a file), pytest-randomly (on by default once
  installed, reseeds `random` per test and prints the seed). **WA** [PHPUnit-config] [PHPUnit-cli]
  [Vitest-sequence] [Jest-cli] [pytest-randomly]
- Make flakiness visible: Spotify cut flakiness from 6% to 4% in two months by showing teams a
  table of their flaky tests, and built a tool to rerun a test many times in a PR. **1**
  [Spotify-2019]
- Measure, don't classify: Meta treats every test as flaky to some degree and scores how flaky.
  **1** [Meta-flaky-2020]
- Microsoft (ICSE 2020): some developer "fixes" did not reduce failure frequency when measured;
  measure after a fix. **1** [MS-Lam-2020]

### 5.4 Retries and quarantine

- ⚖ **Retries.** Google's book: rerunning trades CPU for engineering time and only delays the
  root-cause fix. Google 2016: a "flaky" mark that fails only after three failures in a row
  reduces false alarms but lets owners ignore flakiness. Meta: retries are tolerable for small
  tests but too costly for end-to-end. Playwright: retries are off by default; when enabled, a
  test that fails then passes is reported as "flaky", not hidden; making tests isolated is
  usually better. [SWE-11] [GTB-flaky-2016] [Meta-flaky-2020] [PW-retries]
- ⚖ **Quarantine.** Fowler recommends moving non-deterministic tests to a quarantine so they stop
  damaging trust in the healthy suite, then fixing them fast. Google automates quarantine and
  warns it "could easily mask a real race condition". Thoughtworks' Go team removed quarantined
  tests from the build but time-boxed the quarantine ("completely useless" if months old); Dropbox
  quarantines automatically when a test flaps across hosts on rerun; Azure Pipelines by default
  only marks flaky tests for troubleshooting and suppressing them is opt-in. [F-nondet]
  [GTB-flaky-2016] [TW-go-2012] [Dropbox-2019] [Azure-flaky]
- ⚖ **A pass on retry: detector or green?** Gutenberg and Azure Pipelines use a retry that passes
  to *label* a test flaky (Gutenberg retries at most twice in CI and reports it on the PR);
  Thoughtworks counts any fail-then-pass without a code change as a defect ("Random Success"), and
  Playwright's `--fail-on-flaky-tests` fails the run. In Gutenberg and Azure (by default) the
  build still passes; the test is only labelled. [WP-gutenberg-testing] [Azure-flaky]
  [TW-go-2012] [PW-cli]

## 6. Speed and parallelism

- Speed is one of the two qualities Google values most in a suite (with determinism). **1**
  [SWE-11]
- Integration tests slow down as dependencies grow (Google's example: 5 s per dependency becomes
  five minutes). **1** [SWE-11]
- Run tests in parallel by file or process, and give each test its own data: Vitest and
  Playwright run files in parallel by default; Laravel and Pest run in parallel on request
  (ParaTest), Laravel creating one test database per process; pytest-xdist distributes tests over
  CPU cores. **WA** [Vitest-parallel] [PW-parallel] [Laravel-testing] [Pest-optimizing] [ParaTest]
  [xdist-dist]
- Parallel tests must be isolated and independent of order; they race on shared state outside
  the runner (backend records, database). **WA** [PW-parallel] [Pest-optimizing] Playwright:
  derive unique ids per test; serial mode exists but "is not recommended". **1** [PW-parallel]
- Shard across machines when one machine is not enough (`--shard=x/y`; merge blob reports; only
  tests that can run in parallel can be sharded, and `fullyParallel: true` balances shards). **1**
  [PW-shard]
- Playwright: derive backend ids from `testInfo.testId` and file paths from
  `testInfo.outputPath()`; never rely on another test having run first; serialise tests that touch
  a non-concurrent shared resource with a named lock; workers are shut down after a failure so later
  tests start clean. **1** [PW-parallel]
- Per-worker identity for shared backends: pytest-xdist's `worker_id` fixture; session-scoped
  fixtures run once per worker, not once per run (the docs show a file-lock workaround). Laravel's
  parallel databases persist between runs (`--recreate-databases`) and `ParallelTesting` hooks
  prepare shared resources per process. **1** [xdist-howto] [Laravel-testing]
- ⚖ **Isolation vs speed.** Vitest isolates each test file by default and lets you switch isolation
  off for speed; Jest's docs say sequential runs (`--runInBand`) can be up to 50% faster on some
  Docker/CI setups; Vitest says to disable file parallelism for shared resources such as a
  database. (We found no source claiming parallel is always faster.) [Vitest-perf] [Jest-troubleshooting]
  [Vitest-parallel]
- pytest-xdist needs the same tests in the same order on every worker; `--dist loadscope` or
  `loadfile` keeps expensive shared fixtures on one worker. **1** [xdist-limitations]
  [xdist-dist]
- For CI failures, Playwright recommends traces (recorded on the first retry) over videos and
  screenshots. **1** [PW-bp] [PW-trace]

## 7. Test data and fixtures

- Every test owns its data and state; no test relies on another's leftovers or on run order. **WA**
  [PW-bp] [SWE-11] [Pest-optimizing] [Laravel-db]
- Google's book distinguishes seeded data (pre-initialised into the system under test) from test
  traffic (sent by the test while it runs), and notes that data copied from production is much
  more faithful to reality than handcrafted data. **1** [SWE-14]
- Factories: Laravel model factories define default attributes per model, with states and
  sequences for variations; pytest's "factory as fixture" returns a function so a test can create
  several objects. **WA** [Laravel-factories] [Laravel-db] [pytest-fixtures]
- Object Mother vs Test Data Builder: an Object Mother is a factory of canned example objects;
  Fowler names its benefit (the team gets familiar with the examples) and its fault (many tests
  depend on the exact data in the mothers). Nat Pryce found it copes badly with variation and grows
  bloated, and proposed builders that default every field to a safe value with chainable
  overrides, so a test states only the values it cares about and constructor changes do not break
  tests. Google's Testing on the Toilet also recommends builders over helpers that grow a parameter
  per variation, and adds that tests must never rely on a helper's default values. **WA**
  (builders: Pryce, Google) [F-objectmother] [Pryce-2007] [GTB-test-data-2018]
- Building valid objects inline in every test fills tests with details unrelated to the behaviour
  and lets a constructor change break many tests. **1** [Pryce-2007]
- ⚖ **Where data is created.** Google's DAMP advice prefers data built inside the test, even with
  duplication; fixtures, factories and builders centralise it. (Our reading: both keep the values
  a test asserts on visible in the test; Google says tests should state such values directly, and
  Pryce's builders let a test set only the values that matter.) [GTB-damp-2019] [SWE-12]
  [pytest-fixtures] [Pryce-2007]
- Fixture scope: pytest's default is per test; wider scopes (module, session) suit expensive setup
  such as a network connection. Keep each fixture to one state-changing action so a failed setup
  leaves nothing behind; teardown goes after `yield`. **1** [pytest-fixtures]
- Laravel `RefreshDatabase` runs each test in a transaction, so data does not leak; tests that do
  not use it can leave records. **1** [Laravel-db]
- PHPUnit data providers hold scalars, immutable value objects or test stubs, not services or
  large object graphs. The PHPUnit manual: there are few good reasons to share fixtures between
  tests (a database connection is one); sharing is usually a sign of an unresolved design
  problem. **1** [PHPUnit-writing] [PHPUnit-fixtures]
- WordPress core: every test method starts from an empty WordPress install and builds its own
  fixtures with factories (`self::factory()->post->create()`); each test runs inside a MySQL
  transaction that is rolled back, and the page points to MySQL's list of statements that commit
  implicitly (the limit of that isolation). Identical fixtures for a whole class go in
  `wpSetUpBeforeClass()`. WordPress's rationale for a real database: state lives there, so mocking
  fixtures and settings without one is "almost impossible". **1** [WP-writing-tests]
- **Real database plus fakes.** Both WordPress and Laravel test against a real database with
  per-test transaction rollback; Laravel adds facade fakes for events, queues and HTTP, which
  switch real behaviour off: `Event::fake()` runs no listeners at all, including model events
  that factories rely on, so call it after the factories. **1** [WP-writing-tests] [Laravel-db]
  [Laravel-events]

## 8. Metrics: coverage and mutation score

### 8.1 Code coverage

- Coverage is good at finding untested code and poor as a number for how good tests are: it shows
  that a line ran, not that its result was checked. **WA** [F-coverage] [GTB-cov-2020] [SWE-11]
- When coverage becomes a target, people reach it with low-quality tests (assertion-free tests are
  the extreme case); targets turn into checkboxes. **WA** [F-coverage] [GTB-cov-2020]
- Evidence: in 31,000 test suites over five Java systems, coverage correlated only low to
  moderately with effectiveness once suite size was controlled, and stronger coverage criteria
  added no insight; the authors say coverage should not be a quality target. Caveat: they measured
  effectiveness with mutants. **1** (peer-reviewed, ICSE 2014) [Inozemtseva-2014]
- Google: whether coverage alone reduces defects is an open research question; teams that
  prioritise it tend to treat testing as first class. **1** [GTB-cov-2020]
- What is not covered matters more than the percentage; showing uncovered lines in code review
  made reviews faster. **1** [GTB-cov-2020]
- Integration and e2e coverage is partly incidental (code ran without being checked), unlike unit
  coverage. **1** [GTB-cov-2020]
- At Google, 45% of surveyed developers used coverage often or very often when authoring a change
  (40% when reviewing). **1** [GCov-2019]
- ⚖ **Thresholds.** Google: no ideal number; 60% "acceptable", 75% "commendable", 90%
  "exemplary"; returns beyond that are logarithmic; per-commit 99% is reasonable with 90% as a
  lower bound; project-wide goals above 90% probably not worth it; no top-down mandates. Fowler:
  below half is a sign of trouble, thoughtful testing lands in the upper 80s or 90s, and 100% is
  suspicious. Dodds suggests about 70% and says he made the number up. Inozemtseva and Holmes: do
  not use coverage as a quality target at all. No source gives an evidence-based universal
  threshold. [GTB-cov-2020] [F-coverage] [Dodds-write] [Inozemtseva-2014]

### 8.2 Mutation testing

- Mutation testing injects small faults (mutants) and reruns the tests; a mutant that no test
  catches points to a missing or weak assertion. **WA** [Stryker] [PIT] [Infection]
- Mutation score = detected / valid mutants (Stryker: detected = killed + timeout; compile and
  runtime errors excluded). Infection reports MSI and a "covered code MSI"; its worked example
  shows 67% coverage next to 47% MSI. **1** [Stryker-metrics] [Infection]
- Google recommends mutation testing as the better check of whether covered lines are really
  asserted on. **1** [GTB-cov-2020]
- Developers exposed to mutation testing at Google wrote more tests and improved them over time;
  mutants are coupled with real high-priority faults. The authors note it is not a fully
  randomised experiment. **1** (ICSE 2021) [GMut-ICSE-2021]
- **Cost.** Full mutation analysis is computationally expensive. **WA** [GMut-2018] [PIT-faq]
  Google's answer: mutate only changed lines during code review, skip uncovered and "arid" lines,
  cap mutants per line and per review, and pick operators by past usefulness; this
  produced orders of magnitude fewer mutants and was used by more than 24,000 developers. **1**
  [GMut-TSE-2021] [GMut-2018]
- Tool-side mitigations: Infection can mutate only files changed in a pull request; mutmut runs only
  the tests that cover a mutant, remembers earlier work and runs in parallel (on Windows only inside
  WSL). **1** [Infection-cli] [mutmut]
- **Equivalent and unproductive mutants.** Some mutants cannot change behaviour (equivalent); there
  is no definitive automatic way to find them, so do not expect 100%. **WA** [Stryker-equivalent]
  [PIT-basic] Google reports the bigger problem is *unproductive* mutants (equivalent, or
  detectable but not worth a test): 85% of reported mutants were first classed unproductive, and
  feedback-based filtering raised productive mutants from 15% to 89%; Google notes its filters fit
  its own codebase. **1** [GMut-TSE-2021]
- ⚖ **Score or individual mutants?** The tools report a mutation score (Stryker) or MSI (Infection)
  as their main quality number, and PIT calls mutation testing the gold standard of test coverage. Google computed the score
  and chose not to use it at scale: too expensive per commit and "neither concrete nor actionable";
  it shows a few surviving mutants in code review instead. [PIT] [Infection] [Stryker-metrics]
  [GMut-TSE-2021]

## 9. Tooling per stack

Official documentation of each tool; these are one organisation's practice by definition (the tool
vendor's), marked **WA** only where several tools' docs agree.

### 9.1 Playwright

- Test what the end user sees, not internal code; isolate every test (own storage, cookies, data);
  only test what you control. **1** [PW-bp]
- Locate by user-facing attributes (role, label, text), not XPath or CSS. **WA** with Testing
  Library [PW-bp] [TL-queries]
- Use web-first assertions, which wait until the condition holds (default 5 s), instead of
  non-awaited manual checks such as `isVisible()`; actions auto-wait for actionability (visible,
  stable, receives events, enabled). **1** [PW-bp] [PW-assert] [PW-actionability]
- Sign in once in a setup project and reuse `storageState`; never commit the auth file; use one
  account per worker when tests change server-side state. **1** [PW-auth]
- Files run in parallel by default; each worker gets its own browser context, but backend records
  still race, so derive unique ids per test; shard with `--shard`. **1** [PW-parallel] [PW-shard]
- Retries are off by default; a test that fails then passes on retry is reported "flaky". **1**
  [PW-retries]
- Fixtures are on-demand and composable; worker-scoped fixtures run once per worker. **1**
  [PW-fixtures]
- Replay recorded network traffic (HAR) instead of calling a live API. **1** [PW-mock]

### 9.2 TypeScript and React (Testing Library, Vitest, Jest)

- Tests give more confidence the more they resemble the way the software is used. **WA**
  (Testing Library, Playwright) [TL-principles] [PW-bp]
- Query priority: role, label, placeholder, text, display value first; test ids last, because
  users cannot see them; if an element cannot be found by role, the UI may be inaccessible. **1**
  [TL-queries]
- Prefer `user-event` to `fireEvent`: it simulates whole interactions, not single DOM events. **1**
  (Testing Library and its author) [TL-user-event] [Dodds-rtl]
- `findBy*` = `getBy*` + `waitFor` for elements that appear asynchronously (default timeout
  1000 ms); use `query*` only to assert absence; keep side effects out of `waitFor`. **1**
  [TL-async] [TL-queries] [Dodds-rtl]
- Vitest: mocks and global state (fake timers, stubbed env) persist between tests unless cleared or
  restored; `vi.mock` only replaces access from other modules, calls inside the same module keep
  the real function. **1** [Vitest-mocking]
- Vitest Browser Mode runs tests in a real browser; spying on module exports does not work there.
  **1** [Vitest-browser]
- Vitest runs files in parallel and isolates each file by default; Jest's `--maxWorkers` defaults
  to cores minus one. **1** [Vitest-parallel] [Vitest-perf] [Jest-cli]

### 9.3 Python (pytest, Hypothesis, pytest-xdist)

- Use a `src` layout with an editable install and unique test file names; the `importlib` import
  mode avoids the default mode's drawbacks. **1** [pytest-good-practices]
- Fixtures: function scope by default; teardown after `yield`; one state-changing action per
  fixture; return values cached per test; "factory as fixture" for several objects. **1**
  [pytest-fixtures]
- `tmp_path` gives each test its own directory; `monkeypatch` undoes its changes after the test;
  `parametrize` runs one test body over many inputs. **1** [pytest-tmp-path] [pytest-monkeypatch]
  [pytest-parametrize]
- Hypothesis for property-based tests (section 2.5); pytest-xdist for parallel runs (section 6).
  [Hypothesis] [xdist-dist]

### 9.4 PHP: PHPUnit, Pest, Laravel, WordPress

- PHPUnit: a stub returns configured values, a mock verifies expectations; `final`, `private` and
  `static` methods cannot be doubled. Data providers are public static and hold scalars, immutable
  value objects or stubs. PHPUnit warns about implicit dependencies between test methods. **1**
  [PHPUnit-doubles] [PHPUnit-writing]
- Pest: datasets run one test per data set; architecture tests assert structural rules (strict
  types, base classes, no debug calls); `--parallel` requires isolated, order-independent tests that
  do not share database resources. **1** [Pest-datasets] [Pest-arch] [Pest-optimizing]
- Laravel: `RefreshDatabase` wraps each test in a transaction; model factories build test data;
  `artisan test --parallel` creates one database per process; fakes for HTTP, queues and other
  services, `preventStrayRequests()` to block unfaked HTTP calls; `freezeTime`/`travel` pin time; do
  not mock `Request` or `Config`. **1** [Laravel-db] [Laravel-factories] [Laravel-testing]
  [Laravel-http-client] [Laravel-queues] [Laravel-mocking]
- WordPress core PHPUnit: run against a separate database; group tests with `@group` (`@ticket`
  is an alias). **1** [WP-phpunit]
- PHPUnit isolation switches: `backupGlobals` / `backupStaticProperties` (opt-in) restore global
  and static state around each test; `processIsolation` runs each test in its own PHP process;
  `executionOrder` (default `default`, not random) can be `random`, `defects`, `duration` and
  others. **1** [PHPUnit-fixtures] [PHPUnit-config]
- Laravel HTTP tests simulate the request inside the application (no real network). `assertJson`
  passes when the given fragment is present (extra keys pass too); `assertExactJson` checks the
  whole body. `Queue::fake([...])` can fake only named jobs; `Event::fake()` disables every
  listener; `Http::allowStrayRequests([...])` lets listed URL patterns through while the rest
  throw. **1** [Laravel-http-tests] [Laravel-queues] [Laravel-events] [Laravel-http-client]
- WordPress plugin tests: `wp scaffold plugin-tests` generates the PHPUnit config, a bootstrap that
  finds the test library through `WP_TESTS_DIR`, and an install script for a test database whose
  data "will be deleted"; the PHPUnit version depends on both the PHP and WordPress versions. **1**
  [WP-cli-scaffold] [WP-cli-plugin-tests]
- wp-env ships PHPUnit, Composer and WP-CLI in its containers; the `cli` and `wordpress`
  containers share one database; for test isolation run a separate environment with `--config`
  (own containers and data). **1** [WP-env]
- WordPress e2e: `@wordpress/e2e-test-utils-playwright` adds `admin`, `editor`, `pageUtils` and
  `requestUtils` fixtures; `requestUtils` sets up state through the REST API. Gutenberg's handbook:
  set up and clear state through `requestUtils.rest` rather than the UI; keep e2e tests for
  behaviour that needs a complete WordPress site; add explicit assertions (for example
  `toBeVisible` before a click). **1** [WP-e2e-utils] [WP-gutenberg-e2e] [WP-gutenberg-testing]
- Gutenberg's Vitest setup resets mocks, spies, stubbed globals and fake timers between tests, but
  not imported module state (`vi.resetModules()`). Plain Jest does not reset mocks unless
  `clearMocks`/`restoreMocks` is switched on. **1** [WP-gutenberg-testing] [Jest-config]

### 9.5 Guards against tests that cannot fail

| Tool | Guard | Default |
|---|---|---|
| PHPUnit | A test with no assertions (and no mock expectations) is marked risky; risky tests add no coverage. | Marked risky by default, but `failOnRisky` is false, so the run still exits 0. |
| Jest | `expect.hasAssertions()` / `expect.assertions(n)` fail a test whose assertions never ran (async callbacks). | Off; per test. |
| Vitest | `expect.hasAssertions()` / `expect.assertions(n)`; with concurrent tests use the test context's `expect`. | Off; per test. |
| Playwright | `--fail-on-flaky-tests` fails the run when a test passed only on retry. | Off. |

[PHPUnit-risky] [PHPUnit-config] [Jest-expect] [Vitest-expect] [PW-cli]

### 9.6 Pinning time

- Vitest `vi.useFakeTimers()` (built on @sinonjs/fake-timers; does not fake `nextTick` or
  `queueMicrotask` unless asked) and `vi.setSystemTime()` (alone it mocks only `Date` and
  `Temporal.Now` and fires no timers); Jest fake timers, restored with `jest.useRealTimers()`.
  **1** [Vitest-vi] [Jest-timers]
- Playwright `page.clock`: `setFixedTime` is the recommended way to pin time; `install` must come
  before any other clock call. **1** [PW-clock]
- Python: freezegun mocks the `datetime` module; time-machine's `travel()` keeps ticking by default
  and freezes only with `tick=False`. time-machine's own comparison page calls freezegun slow in
  large projects and unable to affect C extensions; that is a competing author's view. **1**
  [freezegun] [time-machine-usage] [time-machine-comparison]
- Laravel: `freezeTime()` and `travel()`. **1** [Laravel-mocking]

## 10. AI coding agents and tests

Most sources here are 2024–2026 and many are preprints or practitioner reports; the area moves
fast. Marks follow the same rules as above.

### 10.1 What goes wrong

- **Gaming the tests.** Reward hacking in coding means hard-coding expected values or
  special-casing to make tests pass. Anthropic reports Claude Sonnet 3.7 did this "particularly in
  agentic coding settings such as Claude Code"; Claude 4 models do it less. On deliberately
  impossible Claude Code tasks (a bug in the test, a missing dependency) classifier-flagged hack
  rates were 47% (Opus 4), 45% (Sonnet 4) and 78% (Sonnet 3.7) without an anti-hack instruction.
  **WA** (Anthropic and OpenAI both report test gaming) [SC-Claude4] [OpenAI-CoT-2025]
- OpenAI observed frontier reasoning models ending tests early or making a `verify` function always
  return true. **1** (paper) [OpenAI-CoT-2025]
- **Deleting, skipping or weakening tests.** Kent Beck saw an agent, when stuck, delete assertions,
  delete whole tests and fake implementations; GitHub's review guide lists tests "deleted or
  skipped, instead of fixed" as a pitfall to look for; Anthropic's harness and docs forbid removing
  or editing tests. Reported widely, never with a rate. **WA** [Beck-genie-2025] [GH-review]
  [A-harness-2025] [A-prompting]
- **Declaring success without testing.** Without explicit prompting, Claude marked features done
  without end-to-end testing in Anthropic's long-running harness; in a Thoughtworks experiment the
  model sometimes declared success while tests failed. **WA** [A-harness-2025] [TW-autonomy-2025]
- **Tests that encode the bug (oracle problem).** LLM test generators mostly capture the code's
  actual behaviour rather than the intended one (preprint); tools that keep only tests passing on
  the current code "may inadvertently reinforce existing bugs" (preprint). **WA**
  [Konstantinou-2026] [Mathews-2024] An AIware 2025 paper notes that existing (pre-LLM) generators
  mostly produce regression oracles tied to the implemented behaviour. [Bodicoat-2026]
- **Weak tests that pass wrong code.** SWE-Bench's own tests let wrong agent patches pass: 345
  erroneous patches affecting 40.9% of SWE-Bench Lite and 24.4% of Verified leaderboard entries
  (ACL 2025); one in five "solved" Verified patches of the top 30 agents is semantically incorrect
  (preprint); SWE-bench's validation counts 7.8% of patches as correct although they fail the
  developer-written suite, and its weaknesses together inflate resolution rates by 6.2 points. **WA** [UTBoost-2025] [SWE-ABS-2026] [Wang-Pradel-2025]
- **Coverage without fault detection.** Some LLM-generated suites reach 100% coverage with a 4%
  mutation score (IEEE TSE, accepted); coverage correlates weakly with bug detection for generated
  tests. **WA** with section 8 [MutGen-2026] [MuTAP-2023]
- **Over-testing and test smells.** Böckeler reports agent-written tests that are verbose and
  redundant (new test functions instead of assertions in existing ones, duplicated assertions);
  studies of LLM-generated unit tests find Assertion Roulette, Magic Number Tests, Duplicated
  Asserts and Empty Tests (TOSEM, EMSE, EASE). **WA** [Bockeler-skills-2025] [Ouedraogo-TOSEM-2026]
  [Ouedraogo-EMSE-2026] [Siddiq-2024]
- **Over-mocking.** Agent commits add mocks more often than other commits (36% vs 26%, MSR 2026).
  **1** [Hora-2026]
- **More agent-written tests, same outcome.** On SWE-bench, more agent-written tests did not change
  resolved-task totals significantly; agents wrote many more value-printing statements than
  assertions (preprint). **1** [Chen-2026]
- **Reviewers that always find something.** Anthropic: a reviewer agent asked to find problems
  "will always find something"; fixing every finding yields extra abstraction layers and tests for
  cases that cannot happen. **1** [CC-docs-bp]

### 10.2 What the sources recommend

- **Give the agent a check it can run, and ask for evidence** (the command and its output), not a
  bare "all tests pass". **1** [CC-docs-bp]
- **Test-first with human-owned tests.** Anthropic's workflow: write tests from expected
  input/output, say it is TDD so the agent avoids mock implementations, confirm the tests fail,
  commit them, then implement while "instructing it not to modify the tests", optionally with
  independent subagents checking the code is not overfitting to the tests. A bug-fix variant
  commits the failing test first and enforces no test edits with a hook. **1**
  [A-research-bp-2025] [A-sdlc-2026]
- **Tell the agent tests are not the specification.** Anthropic's prompt guidance: tests verify
  correctness rather than define the solution; report incorrect tests instead of working around
  them; do not hard-code test cases. Such an instruction cut hack rates on impossible tasks to 5%
  (Opus 4) and 10% (Sonnet 4), but not for Sonnet 3.7 (80%). **1** [A-prompting] [SC-Claude4]
- **Hidden or held-out tests** catch hard-coding that visible tests reward: Anthropic evaluates with
  tests hidden from the model; SWE-bench studies show fuller developer suites catch patches the PR
  tests miss. **WA** [SC-Claude4] [Wang-Pradel-2025] [UTBoost-2025]
- **Filter generated tests mechanically.** Meta's TestGen-LLM keeps a generated test only if it
  builds, passes reliably (five runs) and adds coverage; 75% built, 57% passed reliably, 25% added
  coverage, and engineers accepted 73% of the recommendations. **1** [Meta-TestGen-2024]
- **Use mutants, not coverage, as the target.** Meta's later ACH generates issue-specific mutants
  and tests that kill them (73% accepted) and notes that targeting coverage is inadequate to kill
  mutants; Thoughtworks moved mutation testing to Adopt because AI-generated tests make coverage
  easy to inflate and leave "perpetually green" tests. **WA** [Meta-ACH-2025] [Meta-mutation-2025]
  [TW-mutation] [MutGen-2026]
- **Review generated tests like code**: do not accept them without reviewing their logic, do not
  test only happy paths, add missing scenarios. **1** (GitHub, two pages) [GH-write-tests]
  [GH-coverage]
- **Watch the agent's intermediate output** for disabled or deleted tests, and require all tests
  passing before a commit. **1** [Beck-augmented-2025]
- **Keep established practice.** Thoughtworks keeps "complacency with AI-generated code" on Hold
  and recommends reinforcing TDD and static analysis. **1** [TW-complacency]
- **Constrain the form of agent-written tests.** Thoughtworks (Assess) and a Thoughtworks author
  propose tests as data tables, so agents produce less test code that is easier to review. **1**
  (early experience) [TW-tests-as-data] [Vaccari-2026]
- **Write contracts first.** A Google workshop paper: having the agent write pre- and
  post-conditions before tests raised bug detection by 9.8 points on production Google bugs. **1**
  [Google-SpecOps-2026]
- **Mocking guidance in agent config files** (such as CLAUDE.md) is the over-mocking study's
  suggestion. **1** [Hora-2026]
- ⚖ **TDD inside the agent loop.** Anthropic calls TDD "even more powerful with agentic coding"
  and Thoughtworks' Radar recommends reinforcing it. Böckeler's small exploratory eval (quality
  judged by an LLM, "far from comprehensive") found no clear benefit when the agent does TDD fully
  inside its own loop: TDD slightly worse overall and no meaningful difference in mutation scores.
  She argues a red test proves little when the agent both writes and confirms it, and stopped
  asking agents to write tests first, relying on mutation testing instead. The sides differ on who
  owns the test: Anthropic's workflow has a human review and commit the tests before the
  implementation, which Böckeler lists as a different usage from TDD inside the loop.
  [A-research-bp-2025] [TW-complacency] [Bockeler-tdd-2026]
- ⚖ **Coverage as the acceptance criterion.** TestGen-LLM (2024) accepts tests that add coverage;
  Meta's later ACH, MuTAP and MutGen say coverage is a weak proxy and target mutants instead.
  [Meta-TestGen-2024] [Meta-ACH-2025] [MuTAP-2023] [MutGen-2026]

## Not verified

What this reference could not confirm, and what it therefore does not claim:

- **Test shapes:** no controlled study comparing pyramid, trophy and honeycomb by defects found or
  cost was found. Section 1.4 reports opinions and practice only.
- **Thoughtworks Radar:** no blip exists for the test pyramid, the testing trophy, Pact as a tool,
  snapshot testing or AI-generated tests (their URLs fall back to the Radar index); property-based
  testing has only a 2014 Assess entry, so it is not cited. Playwright (Adopt 2023) and
  Testcontainers (Adopt 2024) are not on the current edition and are not cited.
- **Microsoft flaky-test papers** (ISSTA 2019, ICSE 2020) and **OOPSLA 2020**: only the abstracts
  were readable; figures inside the papers are not used. The Microsoft Engineering blog post on
  flaky-test management returned 404.
- **Google:** no Testing on the Toilet post titled "Don't put logic in tests" could be fetched
  (404); that advice rests on the SWE book chapter alone. No Google Testing Blog post on
  AI-generated tests was found; the only Google source on agents and tests is a workshop paper.
- **Netflix:** no Netflix Tech Blog post on flaky tests was found; conference slides exist but are
  not used here.
- **Venues not shown on the fetched page:** MuTAP (arXiv 2308.16557), Wang/Pradel/Liu (arXiv
  2503.15223) and the QuickCheck copy (no venue or year printed). Labelled preprints: Chen 2026,
  Konstantinou 2026, Mathews 2024, SWE-ABS 2026; Meta ACH is marked "submitted".
- **Copies instead of publisher pages:** Luo et al. (ACM returns 403 to scripts) and QuickCheck are
  read from course-hosted copies; Beck's answer is read through the Stack Exchange API.
- **Marks:** **WA** was checked for independent organisations by an independent reviewer pass;
  Testing Library and Kent C. Dodds, Fowler and Vocke (Thoughtworks), and two pages of one
  organisation are not counted as independent.
- **Dates:** Kent C. Dodds' trophy and "Write tests" posts carry no date in the fetched text;
  dates written as year-month come from the URL path.
- **Anthropic:** the older engineering URL for Claude Code best practices now redirects to the
  Claude Code docs, which no longer carry the TDD workflow; that workflow is cited from the
  research page that still carries it.
- **Practitioner evals:** Böckeler's TDD-in-the-loop result is an exploratory eval judged by an
  LLM; no controlled study of TDD with coding agents was found.
- **Over-mocking by agents:** one study (MSR 2026); no second peer-reviewed study was found.
- **Tool behaviour** was read from current documentation (PHPUnit 12.0, Laravel 12.x, current
  Playwright, Vitest and Jest docs); older versions may differ and were not checked.

## Coverage of this reference

Distinct sources cited per section, and how many of them were read in context (`V`); the rest
were checked by snippet match (`S`). A source cited in several sections counts in each.

| Section | Sources | Read in context (V) |
|---|---|---|
| 1. Principles | 19 | 8 |
| 2. Test types | 21 | 9 |
| 3. What to test and what not | 22 | 10 |
| 4. Test doubles | 16 | 8 |
| 5. Flakiness | 27 | 13 |
| 6. Speed and parallelism | 14 | 7 |
| 7. Test data and fixtures | 16 | 7 |
| 8. Metrics: coverage and mutation score | 18 | 7 |
| 9. Tooling per stack | 59 | 14 |
| 10. AI coding agents and tests | 36 | 11 |
| **All (distinct)** | **183** | **61** |

Independent check: a separate reviewer pass re-read 157 claims across all sections against the
live pages; 131 held as written, and the other 26 (mostly **WA** marks not earned by independent
sources, unmarked synthesis, and three misattributed claims) were corrected before publication.

## Sources

All URLs fetched and answering HTTP 200 on 2026-11-16 (Stack Overflow through its API).
Dates written as year-month come from the URL path where the page shows none.

| Key | Source | URL | Check |
|---|---|---|---|
| A-harness-2025 | "Effective harnesses for long-running agents" — Anthropic Engineering, 2025-11-26 | <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents> | V |
| A-prompting | "Prompting best practices" (section on passing tests and hard-coding) — Anthropic, Claude Platform Docs, undated living page | <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices> | S |
| A-research-bp-2025 | "Claude Code: Best practices for agentic coding" — Anthropic, published 2025-04-18 (page shows later updates) | <https://www.anthropic.com/research/claude-code-best-practices> | V |
| A-sdlc-2026 | "The AI-native SDLC playbook" — Louis Claxton, Claude by Anthropic, 2026-08-21 | <https://claude.com/resources/articles/the-ai-native-sdlc-playbook> | S |
| Azure-flaky | "Manage flaky tests" — Microsoft Learn (Azure Pipelines), undated | <https://learn.microsoft.com/en-us/azure/devops/pipelines/test/flaky-test-management> | S |
| Beck-augmented-2025 | "Augmented Coding: Beyond the Vibes" — Kent Beck, 2025-06-25 | <https://newsletter.kentbeck.com/p/augmented-coding-beyond-the-vibes> | S |
| Beck-desiderata | "Test Desiderata" — Kent Beck, undated | <https://testdesiderata.com/> | S |
| Beck-genie-2025 | "Genie Wants to Leap" — Kent Beck, 2025-05-12 | <https://newsletter.kentbeck.com/p/genie-wants-to-leap> | S |
| Beck-SO-2008 | "How deep are your unit tests?", answer by Kent Beck — Stack Overflow, 2008-09-30 (read through the Stack Exchange API; the page returns 403 to scripts) | <https://stackoverflow.com/questions/153234/how-deep-are-your-unit-tests> | S |
| Bockeler-skills-2025 | "The role of developer skills in agentic coding" — Birgitta Böckeler (Thoughtworks), martinfowler.com, 2025-03-25 | <https://www.martinfowler.com/articles/exploring-gen-ai/13-role-of-developer-skills.html> | S |
| Bockeler-tdd-2026 | "TDD inside the agent loop - theater or actual value?" — Birgitta Böckeler (Thoughtworks), martinfowler.com, 2026-09-02 | <https://martinfowler.com/articles/exploring-gen-ai/tdd-in-the-agent-loop.html> | V |
| Bodicoat-2026 | "Understanding LLM-Driven Test Oracle Generation" — Bodicoat, Jahangirova, Terragni, AIware 2025, arXiv 2026-01-09 | <https://arxiv.org/abs/2601.05542> | S |
| CC-docs-bp | "Best practices for Claude Code" — Anthropic, Claude Code Docs, undated living page (the old engineering URL redirects here) | <https://code.claude.com/docs/en/best-practices> | V |
| Chen-2026 | arXiv 2602.07900 (agent-written tests on SWE-bench) — Chen et al., preprint, 2026-02-08 (v3 2026-11-09) | <https://arxiv.org/abs/2602.07900> | S |
| Dodds-impl | "Testing Implementation Details" — Kent C. Dodds, 2020-08-17 | <https://kentcdodds.com/blog/testing-implementation-details> | S |
| Dodds-rtl | "Common mistakes with React Testing Library" — Kent C. Dodds, 2020-05-04 | <https://kentcdodds.com/blog/common-mistakes-with-react-testing-library> | S |
| Dodds-snapshot | "Effective Snapshot Testing" — Kent C. Dodds, 2017-10-30 | <https://kentcdodds.com/blog/effective-snapshot-testing> | S |
| Dodds-trophy | "The Testing Trophy and Testing Classifications" — Kent C. Dodds, date not recorded | <https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications> | V |
| Dodds-write | "Write tests. Not too many. Mostly integration." — Kent C. Dodds, date not recorded | <https://kentcdodds.com/blog/write-tests> | S |
| Dropbox-2019 | "Athena: Our automated build health management system" — Utsav Shah, Dropbox Tech Blog, 2019-05-22 | <https://dropbox.tech/tech/2019/05/athena-our-automated-build-health-management-system> | S |
| F-contract | "ContractTest" — Martin Fowler, 2011 (revised 2018) | <https://martinfowler.com/bliki/ContractTest.html> | V |
| F-coverage | "TestCoverage" — Martin Fowler, 2012-04-17 | <https://martinfowler.com/bliki/TestCoverage.html> | S |
| F-double | "TestDouble" — Martin Fowler, 2006-01-17 | <https://martinfowler.com/bliki/TestDouble.html> | S |
| F-integration | "IntegrationTest" — Martin Fowler, 2018-01-16 | <https://martinfowler.com/bliki/IntegrationTest.html> | S |
| F-mocks | "Mocks Aren't Stubs" — Martin Fowler, date not shown on the fetched text | <https://martinfowler.com/articles/mocksArentStubs.html> | V |
| F-nondet | "Eradicating Non-Determinism in Tests" — Martin Fowler, 2011-04-14 | <https://martinfowler.com/articles/nonDeterminism.html> | V |
| F-objectmother | "ObjectMother" — Martin Fowler, 2006-10-24 | <https://martinfowler.com/bliki/ObjectMother.html> | S |
| F-pyramid | "TestPyramid" — Martin Fowler, 2012-05-01 | <https://martinfowler.com/bliki/TestPyramid.html> | V |
| F-shapes-2021 | "On the Diverse And Fantastical Shapes of Testing" — Martin Fowler, 2021-06-02 | <https://martinfowler.com/articles/2021-test-shapes.html> | V |
| F-unit | "UnitTest" — Martin Fowler, 2014-05-05 | <https://martinfowler.com/bliki/UnitTest.html> | S |
| fast-check | Introduction / What is Property-Based Testing? — fast-check docs, undated | <https://fast-check.dev/docs/introduction/> | S |
| fast-check-why | Why Property-Based Testing? — fast-check docs, undated | <https://fast-check.dev/docs/introduction/why-property-based/> | S |
| freezegun | FreezeGun README — spulec/freezegun, undated | <https://github.com/spulec/freezegun> | S |
| GCov-2019 | "Code Coverage at Google" — Ivanković, Petrović, Just, Fraser, ESEC/FSE 2019 | <https://storage.googleapis.com/gweb-research2023-media/pubtools/5172.pdf> | V |
| GH-coverage | "Increasing test coverage in your company with GitHub Copilot" — GitHub Docs, undated | <https://docs.github.com/en/copilot/tutorials/roll-out-at-scale/drive-downstream-impact/increase-test-coverage> | S |
| GH-review | "Review AI-generated code" — GitHub Docs, undated living page | <https://docs.github.com/en/copilot/tutorials/review-ai-generated-code> | V |
| GH-write-tests | "Writing tests with GitHub Copilot" — GitHub Docs, undated living page | <https://docs.github.com/en/copilot/tutorials/write-tests> | S |
| GMut-2018 | "State of Mutation Testing at Google" — Petrović, Ivanković, ICSE-SEIP 2018 (abstract page) | <https://research.google/pubs/state-of-mutation-testing-at-google/> | V |
| GMut-ICSE-2021 | "Does mutation testing improve testing practices?" — Petrović, Ivanković, Fraser, Just, ICSE 2021 | <https://arxiv.org/pdf/2103.07189> | V |
| GMut-TSE-2021 | "Practical Mutation Testing at Scale: A view from Google" — Petrović, Ivanković, Fraser, Just, IEEE TSE (arXiv 2102.11378; abstract also at research.google/pubs/practical-mutation-testing-at-scale-a-view-from-google) | <https://arxiv.org/pdf/2102.11378> | V |
| Google-SpecOps-2026 | "Grounding AI Agents in Contracts: An Empirical Evaluation of Spec-Driven Test Generation" — McClure, Cambronero et al. (Google), SpecOps '26 workshop | <https://research.google/pubs/grounding-ai-agents-in-contracts-an-empirical-evaluation-of-spec-driven-test-generation/> | S |
| GTB-behavior-impl-2013 | "Testing on the Toilet: Test Behavior, Not Implementation" — Andrew Trenk, Google Testing Blog, 2013-08 | <https://testing.googleblog.com/2013/08/testing-on-toilet-test-behavior-not.html> | S |
| GTB-behaviors-2014 | "Testing on the Toilet: Test Behaviors, Not Methods" — Erik Kuefler, Google Testing Blog, 2014-04-14 | <https://testing.googleblog.com/2014/04/testing-on-toilet-test-behaviors-not.html> | V |
| GTB-change-detector-2015 | "Testing on the Toilet: Change-Detector Tests Considered Harmful" — Alex Eagle, Google Testing Blog, 2015-01-27 | <https://testing.googleblog.com/2015/01/testing-on-toilet-change-detector-tests.html> | V |
| GTB-cov-2020 | "Code Coverage Best Practices" — Carlos Arguelles, Marko Ivanković, Adam Bender, Google Testing Blog, 2020-08-07 | <https://testing.googleblog.com/2020/08/code-coverage-best-practices.html> | V |
| GTB-damp-2019 | "Testing on the Toilet: Tests Too DRY? Make Them DAMP!" — Derek Snyder, Erik Kuefler, Google Testing Blog, 2019-12-03 | <https://testing.googleblog.com/2019/12/testing-on-toilet-tests-too-dry-make.html> | S |
| GTB-doubles-2013 | "Testing on the Toilet: Know Your Test Doubles" — Andrew Trenk, Google Testing Blog, 2013-07 | <https://testing.googleblog.com/2013/07/testing-on-toilet-know-your-test-doubles.html> | S |
| GTB-e2e-2015 | "Just Say No to More End-to-End Tests" — Mike Wacker, Google Testing Blog, 2015-04-22 | <https://testing.googleblog.com/2015/04/just-say-no-to-more-end-to-end-tests.html> | V |
| GTB-e2e-good-2016 | "Testing on the Toilet: What Makes a Good End-to-End Test?" — Adam Bender, Google Testing Blog, 2016-09 | <https://testing.googleblog.com/2016/09/testing-on-toilet-what-makes-good-end.html> | S |
| GTB-fake-2013 | "Testing on the Toilet: Fake Your Way to Better Tests" — Jonathan Rockway, Andrew Trenk, Google Testing Blog, 2013-06 | <https://testing.googleblog.com/2013/06/testing-on-toilet-fake-your-way-to.html> | S |
| GTB-flaky-2016 | "Flaky Tests at Google and How We Mitigate Them" — John Micco, Google Testing Blog, 2016-05-27 | <https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html> | V |
| GTB-flaky-2017 | "Where do our flaky tests come from?" — Jeff Listfield, Google Testing Blog, 2017-04-17 | <https://testing.googleblog.com/2017/04/where-do-our-flaky-tests-come-from.html> | V |
| GTB-hourglass-2020 | "Fixing a Test Hourglass" — Alan Myrvold, Google Testing Blog, 2020-11 | <https://testing.googleblog.com/2020/11/fixing-test-hourglass.html> | S |
| GTB-mocks-2013 | "Testing on the Toilet: Don't Overuse Mocks" — Andrew Trenk, Google Testing Blog, 2013-05 | <https://testing.googleblog.com/2013/05/testing-on-toilet-dont-overuse-mocks.html> | V |
| GTB-mutation-2021 | "Mutation Testing" — Goran Petrović, Google Testing Blog, 2021-04 | <https://testing.googleblog.com/2021/04/mutation-testing.html> | S |
| GTB-public-2015 | "Testing on the Toilet: Prefer Testing Public APIs Over Implementation-Detail Classes" — Andrew Trenk, Google Testing Blog, 2015-01 | <https://testing.googleblog.com/2015/01/testing-on-toilet-prefer-testing-public.html> | S |
| GTB-sizes-2010 | "Test Sizes" — Simon Stewart, Google Testing Blog, 2010-12-13 | <https://testing.googleblog.com/2010/12/test-sizes.html> | S |
| GTB-state-2013 | "Testing on the Toilet: Testing State vs. Testing Interactions" — Andrew Trenk, Google Testing Blog, 2013-03 | <https://testing.googleblog.com/2013/03/testing-on-toilet-testing-state-vs.html> | V |
| GTB-test-data-2018 | "Testing on the Toilet: Cleanly Create Test Data" — Ben Yu, Google Testing Blog, 2018-02-20 | <https://testing.googleblog.com/2018/02/testing-on-toilet-cleanly-create-test.html> | S |
| Hora-2026 | "Are Coding Agents Generating Over-Mocked Tests?" — Hora, Robbes, MSR 2026, arXiv 2026-01-30 | <https://arxiv.org/abs/2602.00409> | S |
| Hypothesis | Hypothesis documentation (home) — Hypothesis project, undated | <https://hypothesis.readthedocs.io/en/latest/> | S |
| Hypothesis-api | Hypothesis API reference — Hypothesis project, undated | <https://hypothesis.readthedocs.io/en/latest/reference/api.html> | S |
| Infection | Infection guide (MSI) — Infection PHP, undated | <https://infection.github.io/guide/> | S |
| Infection-cli | Infection command-line options — Infection PHP, undated | <https://infection.github.io/guide/command-line-options.html> | S |
| Inozemtseva-2014 | "Coverage is not strongly correlated with test suite effectiveness" — Laura Inozemtseva, Reid Holmes, ICSE 2014 | <https://cs.ubc.ca/~rtholmes/papers/icse_2014_inozemtseva.pdf> | V |
| Jest-cli | Jest CLI options — Jest (OpenJS Foundation), undated | <https://jestjs.io/docs/cli> | S |
| Jest-config | Configuring Jest — Jest, undated | <https://jestjs.io/docs/configuration> | S |
| Jest-expect | Expect — Jest, last updated 2026-08-28 | <https://jestjs.io/docs/expect> | V |
| Jest-snapshot | Snapshot Testing — Jest, undated | <https://jestjs.io/docs/snapshot-testing> | S |
| Jest-timers | Timer Mocks — Jest, last updated 2026-08-28 | <https://jestjs.io/docs/timer-mocks> | S |
| Jest-troubleshooting | Troubleshooting — Jest, undated | <https://jestjs.io/docs/troubleshooting> | V |
| Konstantinou-2026 | "Can we find bugs using LLM-generated oracles?" — Konstantinou, Degiovanni, Papadakis, preprint, arXiv v2 2026-10-05 | <https://arxiv.org/abs/2410.21136> | S |
| Lam-OOPSLA-2020 | "A Large-Scale Longitudinal Study of Flaky Tests" — Lam, Winter, Wei, Xie, Marinov, Bell, OOPSLA 2020 (abstract page) | <https://2020.splashcon.org/details/splash-2020-oopsla/78/A-Large-Scale-Longitudinal-Study-of-Flaky-Tests> | S |
| Laravel-db | Database Testing — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/database-testing> | S |
| Laravel-events | Events (testing) — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/events> | S |
| Laravel-factories | Eloquent Factories — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/eloquent-factories> | S |
| Laravel-http-client | HTTP Client (testing) — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/http-client> | V |
| Laravel-http-tests | HTTP Tests — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/http-tests> | S |
| Laravel-mocking | Mocking — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/mocking> | V |
| Laravel-queues | Queues (testing) — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/queues> | S |
| Laravel-testing | Testing: Getting Started (parallel testing) — Laravel 12.x docs, undated | <https://laravel.com/docs/12.x/testing> | S |
| Luo-2014 | "An Empirical Analysis of Flaky Tests" — Luo, Hariri, Eloussi, Marinov, FSE 2014 (DOI 10.1145/2635868.2635920; ACM returns 403 to scripts, full text read from a course-hosted copy) | <https://huang.isis.vanderbilt.edu/cs8395/paper/flakytest.pdf> | V |
| Mathews-2024 | arXiv 2412.14137 (LLM test generators validating bugs) — Mathews, Nagappan, preprint, 2024-12-18 | <https://arxiv.org/html/2412.14137> | V |
| Meszaros | "Test Double" — Gerard Meszaros, xunitpatterns.com, undated | <http://xunitpatterns.com/Test%20Double.html> | S |
| Meta-ACH-2025 | "Mutation-Guided LLM-based Test Generation at Meta" — Foster et al. (Meta), arXiv 2025-01-22 (submitted to FSE 2025 Industry Track) (full text) | <https://arxiv.org/html/2501.12862> | V |
| Meta-flaky-2020 | "Probabilistic flakiness: Towards a measure of test reliability" — Mateusz Machalica, Engineering at Meta, 2020-12-10 | <https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/> | V |
| Meta-mutation-2025 | "LLMs are the key to mutation testing and better compliance" — Mark Harman, Engineering at Meta, 2025-09-30 | <https://engineering.fb.com/2025/09/30/security/llms-are-the-key-to-mutation-testing-and-better-compliance/> | S |
| Meta-TestGen-2024 | "Automated Unit Test Improvement using Large Language Models at Meta" — Alshahwan et al. (Meta), FSE 2024, arXiv 2402.09171 (full text; abstract at arxiv.org/abs/2402.09171) | <https://ar5iv.labs.arxiv.org/html/2402.09171> | V |
| MS-dotnet | "Unit testing best practices for .NET" — Microsoft Learn, undated | <https://learn.microsoft.com/en-us/dotnet/core/testing/unit-testing-best-practices> | S |
| MS-Lam-2019 | "Root Causing Flaky Tests in a Large-Scale Industrial Setting" — Lam, Godefroid, Nath, Santhiar, Thummalapenta (Microsoft), ISSTA 2019 (abstract page only) | <https://www.microsoft.com/en-us/research/publication/root-causing-flaky-tests-in-a-large-scale-industrial-setting/> | S |
| MS-Lam-2020 | "A Study on the Lifecycle of Flaky Tests" — Lam, Muslu, Sajnani, Thummalapenta (Microsoft), ICSE 2020 (abstract page only) | <https://www.microsoft.com/en-us/research/publication/a-study-on-the-lifecycle-of-flaky-tests/> | S |
| MS-shiftleft | "Shift testing left with unit tests" — Microsoft Learn (DevOps), undated | <https://learn.microsoft.com/en-us/devops/develop/shift-left-make-testing-fast-reliable> | S |
| MuTAP-2023 | "Effective Test Generation Using Pre-trained LLMs and Mutation Testing" (MuTAP) — Dakhel et al., arXiv 2023-08-31 (journal venue not shown on the arXiv page) | <https://arxiv.org/abs/2308.16557> | S |
| MutGen-2026 | "Mutation-Guided Unit Test Generation with a Large Language Model" — Wang, Xu, Briand, Liu, accepted IEEE TSE, arXiv v8 2026-04-15 | <https://arxiv.org/abs/2506.02954> | S |
| mutmut | mutmut documentation — mutmut, undated | <https://mutmut.readthedocs.io/en/latest/> | S |
| OpenAI-CoT-2025 | "Monitoring Reasoning Models for Misbehavior and the Risks of Promoting Obfuscation" — Baker et al. (OpenAI), arXiv 2025-03-14 (the openai.com post returns 403 to scripts) | <https://arxiv.org/html/2503.11926v1> | S |
| Ouedraogo-EMSE-2026 | arXiv 2407.00225 (216,300 LLM-generated tests vs EvoSuite) — Ouédraogo et al., EMSE (accepted 2026-03-03) | <https://arxiv.org/abs/2407.00225> | S |
| Ouedraogo-TOSEM-2026 | "On the Diffusion of Test Smells in LLM-Generated Unit Tests" — Ouédraogo et al., accepted ACM TOSEM, arXiv v3 2026-08-01 | <https://arxiv.org/abs/2410.10628> | S |
| Pact | Pact documentation (introduction) — Pact Foundation, undated | <https://docs.pact.io/> | V |
| ParaTest | ParaTest README — paratestphp, undated | <https://github.com/paratestphp/paratest> | S |
| Parry-2022 | "A Survey of Flaky Tests" — Parry, Kapfhammer, Hilton, McMinn, ACM TOSEM 31(1), 2022 (DOI 10.1145/3476105; accepted version) | <https://eprints.whiterose.ac.uk/id/eprint/230095/1/parry2021.pdf> | V |
| Pest-arch | Arch Testing — Pest docs, undated | <https://pestphp.com/docs/arch-testing> | S |
| Pest-datasets | Datasets — Pest docs, undated | <https://pestphp.com/docs/datasets> | S |
| Pest-optimizing | Optimizing Tests (parallel) — Pest docs, undated | <https://pestphp.com/docs/optimizing-tests> | V |
| PHPUnit-cli | The Command-Line Test Runner — PHPUnit 12.0 manual, Sebastian Bergmann | <https://docs.phpunit.de/en/12.0/textui.html> | S |
| PHPUnit-config | The XML Configuration File — PHPUnit 12.0 manual | <https://docs.phpunit.de/en/12.0/configuration.html> | V |
| PHPUnit-doubles | Test Doubles — PHPUnit 12.0 manual | <https://docs.phpunit.de/en/12.0/test-doubles.html> | S |
| PHPUnit-fixtures | Fixtures — PHPUnit 12.0 manual | <https://docs.phpunit.de/en/12.0/fixtures.html> | S |
| PHPUnit-risky | Risky Tests — PHPUnit 12.0 manual | <https://docs.phpunit.de/en/12.0/risky-tests.html> | V |
| PHPUnit-writing | Writing Tests for PHPUnit — PHPUnit 12.0 manual | <https://docs.phpunit.de/en/12.0/writing-tests-for-phpunit.html> | V |
| PIT | PIT home page — pitest.org, undated | <https://pitest.org/> | S |
| PIT-basic | PIT basic concepts — pitest.org, undated | <https://pitest.org/quickstart/basic_concepts/> | S |
| PIT-faq | PIT FAQ — pitest.org, undated | <https://pitest.org/faq/> | S |
| Pryce-2007 | "Test Data Builders: an alternative to the Object Mother pattern" — Nat Pryce, 2007-08-27 (the https address does not answer) | <http://www.natpryce.com/articles/000714.html> | S |
| PW-actionability | Auto-waiting — Playwright docs (Microsoft), undated | <https://playwright.dev/docs/actionability> | S |
| PW-assert | Assertions — Playwright docs, undated | <https://playwright.dev/docs/test-assertions> | S |
| PW-auth | Authentication — Playwright docs, undated | <https://playwright.dev/docs/auth> | S |
| PW-bp | Best Practices — Playwright docs, undated | <https://playwright.dev/docs/best-practices> | V |
| PW-cli | Command line — Playwright docs, undated | <https://playwright.dev/docs/test-cli> | S |
| PW-clock | Clock — Playwright docs, undated | <https://playwright.dev/docs/clock> | S |
| PW-fixtures | Fixtures — Playwright docs, undated | <https://playwright.dev/docs/test-fixtures> | S |
| PW-mock | Mock APIs — Playwright docs, undated | <https://playwright.dev/docs/mock> | S |
| PW-parallel | Parallelism — Playwright docs, undated | <https://playwright.dev/docs/test-parallel> | V |
| PW-retries | Retries — Playwright docs, undated | <https://playwright.dev/docs/test-retries> | V |
| PW-shard | Sharding — Playwright docs, undated | <https://playwright.dev/docs/test-sharding> | S |
| PW-trace | Trace viewer — Playwright docs, undated | <https://playwright.dev/docs/trace-viewer-intro> | S |
| pytest-fixtures | How to use fixtures — pytest docs, undated | <https://docs.pytest.org/en/stable/how-to/fixtures.html> | S |
| pytest-good-practices | Good Integration Practices — pytest docs, undated | <https://docs.pytest.org/en/stable/explanation/goodpractices.html> | S |
| pytest-monkeypatch | How to monkeypatch/mock modules and environments — pytest docs, undated | <https://docs.pytest.org/en/stable/how-to/monkeypatch.html> | S |
| pytest-parametrize | How to parametrize fixtures and test functions — pytest docs, undated | <https://docs.pytest.org/en/stable/how-to/parametrize.html> | S |
| pytest-randomly | pytest-randomly README — pytest-dev, undated | <https://github.com/pytest-dev/pytest-randomly> | S |
| pytest-tmp-path | How to use temporary directories and files — pytest docs, undated | <https://docs.pytest.org/en/stable/how-to/tmp_path.html> | S |
| QuickCheck | "QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs" — Koen Claessen, John Hughes (copy hosted at Tufts; venue not printed in the copy) | <https://www.cs.tufts.edu/~nr/cs257/archive/john-hughes/quick.pdf> | V |
| SC-Claude4 | "System Card: Claude Opus 4 & Claude Sonnet 4", §6 Reward hacking — Anthropic, 2025-05 | <https://www-cdn.anthropic.com/6be99a52cb68eb70eb9572b4cafad13df32ed995.pdf> | V |
| Siddiq-2024 | "Using Large Language Models to Generate JUnit Tests: An Empirical Study" — Siddiq et al., EASE 2024 | <https://arxiv.org/abs/2305.00418> | S |
| Spotify-2018 | "Testing of Microservices" — Spotify Engineering, 2018-01 | <https://engineering.atspotify.com/2018/1/testing-of-microservices> | V |
| Spotify-2019 | "Test Flakiness – Methods for identifying and dealing with flaky tests" — Jason Palmer, Spotify Engineering, 2019-11-18 | <https://engineering.atspotify.com/2019/11/test-flakiness-methods-for-identifying-and-dealing-with-flaky-tests> | V |
| Stryker | "What is mutation testing?" — Stryker Mutator docs, undated | <https://stryker-mutator.io/docs/> | S |
| Stryker-equivalent | Equivalent mutants — Stryker Mutator docs, undated | <https://stryker-mutator.io/docs/mutation-testing-elements/equivalent-mutants/> | S |
| Stryker-metrics | Mutant states and metrics — Stryker Mutator docs, undated | <https://stryker-mutator.io/docs/mutation-testing-elements/mutant-states-and-metrics/> | S |
| SWE-11 | "Software Engineering at Google", ch. 11 Testing Overview — Adam Bender; eds. Winters, Manshreck, Wright; O'Reilly, 2020-03 | <https://abseil.io/resources/swe-book/html/ch11.html> | V |
| SWE-12 | "Software Engineering at Google", ch. 12 Unit Testing — Erik Kuefler, 2020-03 | <https://abseil.io/resources/swe-book/html/ch12.html> | V |
| SWE-13 | "Software Engineering at Google", ch. 13 Test Doubles — Andrew Trenk, Dillon Bly, 2020-03 | <https://abseil.io/resources/swe-book/html/ch13.html> | V |
| SWE-14 | "Software Engineering at Google", ch. 14 Larger Testing — Joseph Graves, 2020-03 | <https://abseil.io/resources/swe-book/html/ch14.html> | V |
| SWE-ABS-2026 | "SWE-ABS: Adversarial Benchmark Strengthening Exposes Inflated Success Rates on Test-based Benchmark" — Yu et al., preprint, arXiv 2026-02-28 | <https://arxiv.org/abs/2603.00520> | V |
| Testcontainers | Testcontainers home page — Testcontainers (Docker), undated | <https://testcontainers.com/> | S |
| Testcontainers-guide | "Getting started with Testcontainers for Java" — Testcontainers, undated | <https://testcontainers.com/guides/getting-started-with-testcontainers-for-java/> | V |
| time-machine-comparison | Comparison — time-machine docs (Adam Johnson), v3.6.0 | <https://time-machine.readthedocs.io/en/latest/comparison.html> | S |
| time-machine-usage | Usage — time-machine docs (Adam Johnson), v3.6.0 | <https://time-machine.readthedocs.io/en/latest/usage.html> | S |
| TL-async | Async Methods — Testing Library docs, undated | <https://testing-library.com/docs/dom-testing-library/api-async> | S |
| TL-principles | Guiding Principles — Testing Library docs, undated | <https://testing-library.com/docs/guiding-principles/> | V |
| TL-queries | About Queries — Testing Library docs, undated | <https://testing-library.com/docs/queries/about> | V |
| TL-user-event | user-event Introduction — Testing Library docs, undated | <https://testing-library.com/docs/user-event/intro/> | S |
| TW-autonomy-2025 | "How far can we push AI autonomy in code generation?" — Birgitta Böckeler (Thoughtworks), martinfowler.com, 2025-08-05 | <https://www.martinfowler.com/articles/pushing-ai-autonomy.html> | S |
| TW-CDC | "Consumer-driven contract testing" — Thoughtworks Technology Radar (Adopt, 2016-11) | <https://www.thoughtworks.com/radar/techniques/consumer-driven-contract-testing> | V |
| TW-complacency | "Complacency with AI-generated code" — Thoughtworks Technology Radar (Hold; 2024-10, 2025-04, 2025-11) | <https://www.thoughtworks.com/radar/techniques/complacency-with-ai-generated-code> | S |
| TW-go-2012 | "No more flaky tests on the Go team" — Thoughtworks Insights blog, 2012-09-25 | <https://www.thoughtworks.com/insights/blog/no-more-flaky-tests-go-team> | S |
| TW-mutation | "Mutation testing" — Thoughtworks Technology Radar (Trial 2026-04, Adopt 2026-11) | <https://www.thoughtworks.com/radar/techniques/mutation-testing> | V |
| TW-tests-as-data | "Tests as data over tests as code" — Thoughtworks Technology Radar (Assess, 2026-11) | <https://www.thoughtworks.com/radar/techniques/tests-as-data-over-tests-as-code> | S |
| UTBoost-2025 | "UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench" — Yu, Zhu, He, Kang, ACL 2025 | <https://arxiv.org/abs/2506.09289> | S |
| Vaccari-2026 | "Tests as data" — Matteo Vaccari (Thoughtworks), martinfowler.com, 2026-11-11 | <https://martinfowler.com/articles/tests-as-data.html> | S |
| Vitest-browser | Browser Mode — Vitest docs, undated | <https://vitest.dev/guide/browser/> | S |
| Vitest-expect | expect API — Vitest docs, undated | <https://vitest.dev/api/expect.html> | S |
| Vitest-mocking | Mocking — Vitest docs, undated | <https://vitest.dev/guide/mocking> | S |
| Vitest-parallel | Parallelism — Vitest docs, undated | <https://vitest.dev/guide/parallelism> | V |
| Vitest-perf | Improving Performance — Vitest docs, undated | <https://vitest.dev/guide/improving-performance> | S |
| Vitest-sequence | sequence config — Vitest docs, undated | <https://vitest.dev/config/sequence> | S |
| Vitest-snapshot | Snapshot — Vitest docs, undated | <https://vitest.dev/guide/snapshot> | S |
| Vitest-vi | vi API (useFakeTimers, setSystemTime) — Vitest docs, undated | <https://vitest.dev/api/vi> | S |
| Vocke-2018 | "The Practical Test Pyramid" — Ham Vocke, martinfowler.com, 2018-02-26 | <https://martinfowler.com/articles/practical-test-pyramid.html> | V |
| Wang-MLR-2022 | "Improving Test Automation Maturity: a Multivocal Literature Review" — Wang, Mäntylä et al., Journal of Software: Testing, Verification and Reliability 2022 (arXiv 2022-02-18) | <https://arxiv.org/abs/2202.09076> | S |
| Wang-Pradel-2025 | arXiv 2503.15223 (SWE-bench patch validation) — Wang, Pradel, Liu, arXiv 2025-03-19 (venue not shown on the arXiv page) | <https://arxiv.org/abs/2503.15223> | S |
| WP-cli-plugin-tests | Plugin Integration Tests — WP-CLI handbook, WordPress.org, updated 2025-07-01 | <https://make.wordpress.org/cli/handbook/misc/plugin-unit-tests/> | S |
| WP-cli-scaffold | wp scaffold plugin-tests — WP-CLI commands, WordPress.org, undated | <https://developer.wordpress.org/cli/commands/scaffold/plugin-tests/> | S |
| WP-e2e-utils | @wordpress/e2e-test-utils-playwright — Block Editor Handbook, WordPress.org, undated | <https://developer.wordpress.org/block-editor/reference-guides/packages/packages-e2e-test-utils-playwright/> | S |
| WP-env | @wordpress/env — Block Editor Handbook, WordPress.org, updated 2026-11-13 | <https://developer.wordpress.org/block-editor/reference-guides/packages/packages-env/> | V |
| WP-gutenberg-e2e | End-to-End Testing — Block Editor Handbook (Gutenberg), WordPress.org, updated 2026-11-11 | <https://developer.wordpress.org/block-editor/contributors/code/e2e/> | S |
| WP-gutenberg-testing | Testing Overview — Block Editor Handbook (Gutenberg), WordPress.org, updated 2026-11-12 | <https://developer.wordpress.org/block-editor/contributors/code/testing-overview/> | S |
| WP-phpunit | PHPUnit — Make WordPress Core handbook, WordPress.org, undated | <https://make.wordpress.org/core/handbook/testing/automated-testing/phpunit/> | S |
| WP-writing-tests | Writing PHP Tests — Make WordPress Core handbook, WordPress.org, updated 2024-05-09 | <https://make.wordpress.org/core/handbook/testing/automated-testing/writing-phpunit-tests/> | V |
| xdist-dist | Running tests across multiple CPUs — pytest-xdist docs, undated | <https://pytest-xdist.readthedocs.io/en/stable/distribution.html> | S |
| xdist-howto | How-tos — pytest-xdist docs, undated | <https://pytest-xdist.readthedocs.io/en/stable/how-to.html> | S |
| xdist-limitations | Known limitations — pytest-xdist docs, undated | <https://pytest-xdist.readthedocs.io/en/stable/known-limitations.html> | V |
