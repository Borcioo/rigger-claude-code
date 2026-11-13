---
name: stack-tooling
description: >-
  Load BEFORE adding, changing or upgrading a project's lint, format, type-check, static-analysis
  or test tooling (ESLint, Prettier, Biome, tsc, PHPStan, phpcs/WPCS, PHPUnit, Pest, wp-env) in a
  TypeScript/React/Vite/Astro, Laravel or WordPress repo, or when such a gate is slow or times
  out, or `npm install` fails after a tool change.
---

# Stack tooling

Versions, compatibility limits and source URLs: `references/research-2026-10.md` (50 KB, 14
numbered sections: grep the tool's name, never read the whole file). Read it before
pinning a version, when two tools refuse to coexist (TS 7 has no JS API, WPCS needs PHPCS 3, WP
tests need PHPUnit <= 9.6), or when a claim here needs its source.

**Rules for any change of tooling**
- Measure on the project before and after (same files, warm caches noted), and keep the numbers in
  the PR. A tool that is faster but drops rules the project relies on is not an upgrade.
- Replace generic tools (ESLint core, Prettier, tsc); keep the ones that encode framework rules
  (WPCS, `@wordpress/scripts`, Larastan) until a replacement provably covers them.
- A toolchain switch or a major upgrade is its own PR, never mixed with feature work; the user
  approves it.
- Every gate runs with its cache: phpcs `--cache`, PHPStan result cache (`tmpDir`), knip `--cache`,
  PHPUnit result cache, Pint/PHP-CS-Fixer cache files.
- A static-analysis upgrade never deletes a runtime check on data from WordPress, the database, a
  filter or a request (`WP_Comment_Query` results, an unset `WP_Filesystem` property), even when
  PHPStan reports it redundant: a green gate proves nothing while no test feeds the bad value.
  Narrow the type at the source (a helper that filters to the real type) and add a test that feeds
  the bad value.
- After any change to a manifest or lockfile, run the documented install command from a clean state
  (at minimum `npm install --dry-run`) and fix the docs or the dependency in the same change; a
  checkout that already has `node_modules` cannot show an ERESOLVE failure.

## TypeScript / React / Vite (Laravel + Inertia frontends, Node tools, Astro)

| Job | Use | Instead of | Notes |
|---|---|---|---|
| Lint + format | **Biome** (`biome migrate eslint --write`, `biome migrate prettier --write`, then `biome check`) | ESLint + Prettier | React, TS, JSX, JSON, CSS fully supported; Vue, Svelte, Astro experimental. |
| Lint, when ESLint plugin rules or type-aware rules are needed | **oxlint** (`npx @oxlint/migrate`) + oxfmt (beta) | ESLint | ESLint JS plugins run as `jsPlugins` (alpha); type-aware rules need `oxlint-tsgolint` + TS 7. |
| Type-check | **TypeScript 7 `tsc`** for plain TS packages | TS 6 `tsc` | TS 7 has no JS API yet: typescript-eslint, `vue-tsc`, `astro check`, ts-node need TS 6. Side by side: `"typescript": "npm:@typescript/typescript6@^6.0.2"` + `"@typescript/native": "npm:typescript@^7.0.2"`. Before switching check removed options (`baseUrl`, `moduleResolution: node10`, `types` now defaults to `[]`). |
| Unused files, exports, deps | **knip** (`knip --cache`) | manual cleanup | Periodic audit; a CI gate only once the repo is clean. Plugins for Vite, Vitest, Astro, Playwright, laravel-vite-plugin; no Inertia plugin (set entries by hand). |
| Project-specific guards (a mistake made twice) | **Biome GritQL plugins** in Biome repos; **ast-grep** (`sgconfig.yml` + rule tests) elsewhere and for PHP | review comments | |
| Tests | Vitest, Playwright | Jest | See `rigger-core:e2e-playwright-speed` for browser suites. `node --test` takes a glob or file list (`node --test "test/*.test.mjs"`) or no argument, never a bare directory: Node 22 reads it as one failing file. |

## Laravel / PHP applications

| Job | Use | Notes |
|---|---|---|
| Format | **Pint** (keep) | `--dirty` or `--diff=<branch>` in gates; `--parallel` is still marked experimental. |
| Static analysis | **PHPStan 2.3 + Larastan** — add where missing | Start at level 5 with a baseline, raise later. PHPStan 2.3 runs in parallel by default. Larastan boots the app (needs a bootable `.env`). |
| Upgrades and refactors | **Rector + rector-laravel** (`withComposerBased(laravel: true)`) | PHP and Laravel version upgrades, dead code and code-quality sets; review the diff like any PR. |
| Tests | **Pest 4** (PHP 8.3, PHPUnit 12) or **Pest 5** (PHP 8.4, PHPUnit 13) | `--parallel` uses ParaTest (Pest docs say use WSL on Windows: try native first, fall back to WSL). Locally `--order-by=defects --stop-on-defect` (result cache on by default). Coverage: PCOV for line coverage, Xdebug only when branch/path coverage is needed, with the filter limited to `src/`/`app/`. |
| Extra fast checks | **Mago** (`mago lint`, `mago analyze`, Laravel integration rules) | A pre-check beside PHPStan, not a replacement: it cannot load Larastan or other PHPStan extensions. Its `laravel` formatter preset mirrors Pint. |

## WordPress plugins and themes

| Job | Use | Notes |
|---|---|---|
| PHP lint | **PHPCS 3.13 + WPCS ≥ 3.4.1 + PHPCompatibilityWP** (keep) | Always `--cache=<tmp>/phpcs-cache-<project>`. Stay on PHPCS 3 (WPCS does not support PHPCS 4 yet). WPCS 3.4.1 is a security fix (command execution when scanning untrusted code): never older. `--parallel` does nothing on Windows (no pcntl); parallel only in WSL/CI. |
| Static analysis | **PHPStan 2.3 + phpstan-wordpress + wordpress-stubs** | phpstan-wordpress carries an "may be abandoned" notice; the stubs are current. `johnbillion/wp-compat` (PHPStan extension) catches calls newer than the supported WP version. |
| Unit tests (no WordPress loaded) | **Brain Monkey + Mockery** | Milliseconds per test: put logic in classes testable this way so integration tests cover only what needs WordPress loaded. WP_Mock pins PHPUnit 9.6. |
| Integration tests | **`WP_UnitTestCase` on wp-env** (Docker), PHPUnit ≤ 9.6 + `yoast/phpunit-polyfills` | The WP test suite caps PHPUnit at 9.6. No official parallel runner: shard by directory across separate wp-env environments (`--config`, `WP_ENV_PORT`), or try wp-browser's internal `parallel-run`. Playground CLI (no Docker) needs the `WP_TESTS_SKIP_INSTALL=1` workaround and runs on SQLite: smoke tests only. |
| Upgrades | `fsylum/rector-wordpress` (community, optional) | WP deprecation sets up to 7.0. |
| Scoped vendor | **PHP-Scoper** | Test the build alone and beside an unscoped copy of the same packages; exclude `function_exists`-guarded helper files (polyfills, deprecation-contracts) and list their functions in `exclude-functions`. |
| Block / admin JS | **`@wordpress/scripts`** (keep: ESLint + wp-prettier) | oxfmt cannot produce WordPress's spaces-in-parens style; Biome's `delimiterSpacing` might, oxlint might run `@wordpress/eslint-plugin` as JS plugins: both unverified: spike before switching (`npx @oxlint/migrate` with `@wordpress/eslint-plugin` in `jsPlugins`; Biome `delimiterSpacing` diffed against wp-prettier output on the repo). |
| Extra fast checks | **Mago** with the WordPress integration | 8 security rules (`no-unescaped-output`, `nonce-verification`, `prepared-sql`, `validated-sanitized-input`, …) as a pre-check; its formatter is not WordPress style, and it does not replace WPCS. |

## Shell, CI and data files

Run `actionlint` before committing a workflow change, `shellcheck` on bash scripts and hooks, and
`jq`/`yq` for JSON/YAML; on Windows with rigger-windows installed, details in
`rigger-windows:windows-dev-env`.

## Open spikes (do one when you next touch that project)

- WordPress block or admin JS in the spaces-in-parens style: oxlint `jsPlugins` with
  `@wordpress/eslint-plugin`, or Biome `delimiterSpacing`; both unverified, spike before switching
  a project off `@wordpress/scripts`.
- WP integration tests in parallel: try ParaTest 6.x `TEST_TOKEN` with one database per process;
  unconfirmed for WordPress, so spike it before recommending it.
