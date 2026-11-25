---
name: rigger-wp-traps
description: >-
  Load BEFORE writing or changing WordPress plugin or theme code that touches roles/capabilities,
  migrations, enqueues, wp_insert_post/wp_update_post with block content, a REST
  permission_callback, a consent gate, WooCommerce Store API or order attribution, block validity,
  locked post meta, page caching, WP-CLI probes of admin-only code, WooCommerce product
  duplication, or WP_UnitTestCase tests; and when an integration test fails only in the full
  suite.
---

# WordPress traps

Each entry: the fact, then what to do.

## Roles, capabilities and upgrade steps

- **A role slug that equals a capability slug is one entry.** WordPress keeps role membership and
  user-level grants in the same `capabilities` array and derives `WP_User::roles` from its keys
  with `is_role()`. A user-level `add_cap('x')` makes `roles` list role `x`, and `remove_cap('x')`
  after `set_role('x')` removes the role membership, leaving the account with no role. Never give a
  capability the slug of a role; where one already does, never grant or remove it at user level,
  and pin the behaviour in a test before building logic on `roles` or `has_cap`.
- **`$wp_roles` is null at `plugins_loaded`, and taxonomies and CPTs registered on `init` do not
  exist yet.** `wp-settings.php` creates `$wp_roles` after `plugins_loaded` and before `init`
  (WP-CLI's boot has the same order). Core reads the global itself: `WP_User_Query` with a
  capability argument calls `$wp_roles->for_site()` and fatals. Steps that touch roles,
  capabilities or users run at `init` or later, or call `wp_roles()` once first (it creates the
  global on demand). A step that reads a taxonomy or CPT registers it first or returns false to
  retry; otherwise it finds nothing and is recorded as done. Test every step from the hook it
  really runs on, and once against a real site through WP-CLI. Test each step alone from a fresh
  state with `$wp_roles`, rewrite rules, the current user and init-time post types removed (a step
  run earlier in sequence creates the roles object and hides the bug).
- **`option_page_capability_{$page}` also opens `wp-admin/options.php` without `action`** (WP 7.0+),
  which lists every site option, API keys included. Return the wider capability only for a POST
  with `action=update`; a role test GETs `options.php` with that `option_page`.
- In the final report's hunk list, a WordPress option counts as a setting, and a capability or nonce
  check as permissions or auth.

## Tests (WP_UnitTestCase / wp-env PHPUnit)

- **The rollback undoes table rows, nothing else.**
  - Globals and static caches survive: an earlier test's `set_permalink_structure()` left
    `$wp_rewrite` pretty while the option rolled back. Fixtures that need an address that always
    resolves use `?p=N` / `?page_id=N`.
  - DDL commits implicitly in MySQL: `ALTER TABLE`, `CREATE INDEX` and similar, even on a
    `TEMPORARY` table (`CREATE`/`DROP TEMPORARY TABLE` are the exception). WP_UnitTestCase turns
    `CREATE TABLE` into `CREATE TEMPORARY TABLE`, but a migration's `ALTER` still commits. Tests
    that run DDL, `TRUNCATE` or child processes use a plain TestCase with unique rows, explicit
    cleanup and their own group; a migration test kept in WP_UnitTestCase resets its lock/state
    rows at the start.
  - A failure only in the full run: after leaked globals (pin global state and restore it in
    `finally`), look for pre-DDL writes.
- **Prove that script cleanup runs after a failure.** Wrap the whole `wp eval-file` run in
  `try`/`finally` (do not rely on a shutdown function alone) and break the run once mid-way to see
  the fixtures go.
- **After another process writes the database, flush the whole object cache.** After a child
  process (`WP_CLI::runcommand`), a parallel runner or an HTTP request to the site writes,
  `clean_post_cache()` is not enough: call `wp_cache_flush()` before asserting.
- **MySQL 8 caches `information_schema` table stats** (`information_schema_stats_expiry` = 86400),
  so a "rows were created and deleted" proof reads an unchanged `AUTO_INCREMENT`. Run
  `SET SESSION information_schema_stats_expiry = 0` first, or count rows by a marker.
- **An invalidation test proves one trigger at a time.** Assert "stored" (0 queries) immediately
  before each trigger and "rebuilt" (1 query) after it, one mutation per hook: triggers run back
  to back stay green because the previous step's restore has already cleared the cache.

## Assets

- **Each enqueued file is versioned from itself.** `@wordpress/scripts`' `.asset.php` version
  hashes the files of the entry chunk, but wp-scripts' `style` cache group moves `style-*.css` into
  a chunk without JS, which the version skips. Version the stylesheet from its own file
  (`filemtime()` or `md5_file()`); prove it with a test that changes only that file and asserts its URL
  changes.

## Page caching

- **A no-cache test proves nothing on a URL the cache never stores.** Page caches skip query-string URLs (WP Fastest Cache passes only `?amp` and `wc-api=`), so fetch the page twice without a query string as two anonymous visitors and assert the second response belongs to the second. Under WP Fastest Cache, `DONOTCACHEPAGE` is not enough: also call `wpfc_exclude_current_page()` behind `function_exists`; purge one post with `do_action( 'wpfc_clear_post_cache_by_id', false, $id )`.

## Writing content and running scripts

- **`wp eval-file` runs the file through `eval()`.** `declare(strict_types=1)` is rejected, and
  `global $x;` inside a function reads nothing. Run such a file with `--use-include` when it needs
  either. It runs outside any request, so page conditionals (`is_order_received_page()`) are false:
  check rendered output over HTTP.
- **WP-CLI is not admin: `is_admin()` is false.** Plugins that load admin code only behind it
  (WPML's `IWPML_Backend_Action` classes) never run, so a probe of an admin notice or hook reports
  "nothing" with or without the cause. Run the probe with `--exec='define("WP_ADMIN",true);'`,
  first on the state that must show the defect.
- **A script is portable only after its selftest ran as the next environment.** A fixture with the
  local host hard-coded would have stopped it on DEV. Build fixture URLs from `home_url()` and run
  the selftest once with `pre_option_home` and `pre_option_siteurl` returning the next
  environment's address.
- **An all-digit page slug never survives.** `wp_unique_post_slug()` saves `2024` on a hierarchical
  post type as `2024-2`, while `/2024/` still answers 200 (the year archive), so a crawl misses it.
  Before a plan promises such a URL, run
  `wp eval 'echo wp_unique_post_slug("2024", 0, "publish", "page", 0);'` on the site, and check
  built pages by `post_name`, not status code.
- **`wp_update_post()` / `wp_insert_post()` unslash their input,** and so does
  `update_post_meta()`. Without `wp_slash()` on the data, block JSON loses its backslashes and
  markup leaks into the page as `u003cbru003e`.
- **WP-CLI writes as user 0, so kses strips iframes and `<style>` from post content.** A script
  that must store HTML byte for byte calls `kses_remove_filters()` (and `kses_init()` in
  `finally`), slashes every value (above) and reads each stored value back with
  `===`, the backup it keeps for restore included. A put-back reads the restored value back
  before it deletes the backup.
- **Publish hooks see the post as inserted.** SEO instant indexing, cache purge and menu auto-add
  run inside `wp_insert_post()` with `post_status => 'publish'`, before any later
  `update_post_meta()`, so a page meant to be noindex goes out indexable. Pass every meta in
  `meta_input` (or insert as draft, write the meta, then publish), and before a production publish
  check `nav_menu_options` `auto_add`, WPML "translate everything" and SEO auto-submission.
- **PCRE over UTF-8 takes `/u`.** Byte-mode `\R` also matches 0x85, the second byte of "Å"
  (C3 85), so `preg_split('/\R/')` cuts such text mid-character. Split lines on `"\n"` or
  use `/\R/u`.
- **`wp option patch update` fails on a key the stored array does not have** ("No data exists for
  key" — defaults are applied on read only). Use `wp option patch insert` for keys that may be
  missing, or read the option first.
- **Brace every variable part inside a double-quoted PHP string.** `"$k[$ri]"` is an array offset
  on `$k`, not a literal, and crashes with "Cannot access offset of type string on string": write
  `"{$k}[{$ri}]"`.
- **A phrase-replacement tool matches whole phrases.** `str_ireplace` over `post_content` with
  bare substrings leaves artifacts (replacing `foo` also rewrites `foobar`, and a replacement that
  adds a space leaves "x , y") and a stem can rewrite wording that must stay as written. Match
  whole phrases with word boundaries, keep a never-touch list for legal and label text, `wp_slash()` before `wp_update_post()` (above), and run
  `--dry-run` with the diff read before `--fix`.
- **Stored prose is not the prose you see.** A typography or locale plugin may insert U+00A0 (for
  example after one-letter words, as some languages' typography requires), so a pattern with a
  literal space matches nothing. Build the pattern with a space
  class (`\s|\x{00A0}|&nbsp;`), and require the exact expected hit count so a miss is reported
  instead of silently skipped.
- **A lock serialises only what is read after it is taken.** `get_post_meta()` serves a
  request-local cache primed before the lock (the post was loaded for the permission check). Call
  `wp_cache_delete( $id, 'post_meta' )` right after acquiring the lock, and make the concurrency test prime the cache, change the row behind
  it with direct SQL, then run the locked write.
- **An integrity check on edited HTML compares tokens, not strings.** Removing a span that opened an attribute, comment or `<textarea>` joins the halves into live markup (`onerror=`), and prefix/suffix checks pass. Compare `WP_HTML_Tag_Processor` token streams with the span on token boundaries, test with a span starting inside an attribute value, and parse the result again with `Dom\HTMLDocument` (SVG/MathML, CDATA).

## Blocks and content audits

- **A block that is valid only through a deprecation is "needs migration", not invalid.**
  `core/paragraph`'s last deprecation is a catch-all (`content` = whole innerHTML, `save` =
  `RawHTML`), so `<!-- wp:paragraph --><div>x</div>` migrates and `isValid` is true by design,
  while comparing every block with `validateBlock` against the current version also flags
  legitimate deprecated blocks. Classify a block by whether its current `save` reads back to the
  same block. Trace the code path that set a framework flag before writing a workaround or
  recording a cause (`isValid` is set in `parseRawBlock` → `applyBlockDeprecatedVersions`).
- **A text audit sweeps code as well as the database.** Copy also lives in `block.json` attribute
  `default`s (mu-plugins included), templates and translation files; sweep posts, meta, terms and
  those, and confirm every finding against the rendered HTML — stored copy can be dead.
- **A catalogue lookup keys by the whole identity.** Keying a `.po` file by `msgid` alone lets a
  `msgctxt` entry replace the plain one. Key by `msgctxt` + `msgid` and give the helper a unit test
  with a collision in both insertion orders.
- **A WXR mapping decides every field.** An export that read `wp:status` but not
  `wp:post_password` published a protected page's heading. List every element of the item
  (`wp:post_password`, `excerpt:encoded`, `wp:post_parent`, `wp:postmeta`…), record map, drop or
  block for each, and give the mapper a protected, a private and a draft item that must stay out.

## REST routes and feeds

- **Never answer a request from `rest_pre_dispatch`:** a later callback (ACF Pro) can return null
  and core dispatches again, so every write runs twice. Wrap with
  `rest_request_before_callbacks`/`rest_request_after_callbacks`; test beside a hostile neighbour
  on the same hook.
- **WordPress answers a conditional GET for a feed with 304 before the callback runs**
  (`get_lastpostmodified()`): a feed whose output changes without a post change unsets
  `If-Modified-Since`/`If-None-Match` on `parse_request` and drops `Last-Modified`/`ETag` in
  `wp_headers`; its check sends each conditional header in its own request.
- **Store API routes are open by design.** `Cart`, `CartAddItem`, `CartApplyCoupon` and `Batch`
  register `permission_callback => '__return_true'`, so anonymous POSTs create carts and orders.
  Before describing a REST surface as read-only for anonymous users, grep its routes for
  `__return_true`.
- **`WP_REST_Server` matches routes case-insensitively.** Gate by handler (compare the way the
  framework compares) or, where that is impossible, match with an anchored
  case-insensitive pattern; test every route spelling, `?rest_route=` included.
- **WpOrg\Requests follows redirects itself.** A `pre_http_request` allowlist runs once per call,
  so a 302 from an allowed host reaches a blocked one: check every hop on
  `requests-requests.before_redirect` too, and test it with an allowed host that answers 302 to a
  blocked one.

## Environment guards

- **`wp_get_environment_type()` returns `'production'` when nothing sets it,** so a guard built on
  it alone fails open on a copy with a fresh `wp-config.php`. A production-only switch for a real
  third-party id (analytics, Clarity, pixels) also requires the production host from `home_url()`;
  grep the project for other such guards, and test with the constant undefined on another host.
- **Proof that `templates/wp-outbound-block.php` holds is the active-plugin list plus a blocked `wp_remote_get()`, never "no requests seen":** plugins whose SDKs call curl or Guzzle directly (its header lists the gaps) are deactivated on the copy.

## WooCommerce and consent

- **A duplicated product keeps the original's meta, and `woocommerce_product_duplicate` fires only
  from the admin action.** `product_duplicate()` and the REST duplicate route skip that hook: hook
  `woocommerce_product_duplicate_before_save` instead. Add the plugin's own state meta (a
  "processed" mark) to `woocommerce_duplicate_product_exclude_meta`, or the copy arrives marked.

- **Consent gates decide in the browser.** CookieYes sets `cookieyes-consent` with
  `SameSite=Strict`, so PHP misses it after a cross-site click (the payment return), and a
  `<script src>` in the markup is fetched before any blocker removes it. Create the script in JS
  once the banner grants consent; test from cleared storage via a cross-origin click.
- **Order attribution tracks every visitor.** WooCommerce sets `sbjs_*` source cookies by default.
  On an EU shop without a consent platform, set
  `woocommerce_feature_order_attribution_enabled = no` in the site's setup (`wp option update`); write a cookie policy only from
  a measured inventory (Playwright `context.cookies()` plus the storage of every frame along a
  visitor path), never from a plugin list.
- **A mu-plugin loads before WooCommerce.** A file-scope class that extends or implements a
  plugin's type (`Automattic\WooCommerce\Blocks\Integrations\IntegrationInterface`) fatals the
  whole site: declare it, as an anonymous class, inside the callback that registers it
  (`woocommerce_blocks_<registry>_registration`), and boot WP once (`wp eval 1`) after adding any
  mu-plugin file. Woo calls `initialize()` on every request: register assets there and enqueue
  them on `wp_enqueue_scripts` behind the page check.
- **Store API checkout reuses the session's pending or failed order**
  (`Checkout::create_or_update_draft_order()`), and a PATCH writes the request into it again. An
  order change made at POST time must also hold after a PATCH and after a POST retried after a
  failed payment: prove it with a real `rest_do_request()` checkout as a signed-in throwaway
  customer, never by firing the hook by name (local only: it sends order e-mails).
- **`wc_get_orders()` on posts storage ignores a caller's `meta_query`**
  (`WC_Data_Store_WP::get_wp_query_args()` skips it) and returns every order; HPOS honours it.
  Select by meta through a recheck in PHP, and test the query on both stores with a non-matching
  order that must stay out.
- **Action Scheduler reports failure as 0, not an exception:** `as_schedule_single_action()`
  returns 0 before its store loads (`init` priority 1). Check the id before cancelling what it
  replaces; work from a hook that fires earlier (`woocommerce_guest_session_to_user_id`) is
  scheduled from `init` priority 2 or later.
- **Data that belongs to an address goes into address fields.** Before building a checkout block,
  register it with `woocommerce_register_additional_checkout_field()` and
  `'location' => 'address'`, mirror derived keys (meta another plugin reads, such as an invoicing plugin) on save, and list
  what checkout, My account, admin, e-mails and REST then show without code.
- **A write to a `wc/store/*` data store is not a form change.** `wp.data` actions such as
  `setBillingAddress` update the store and the screen but skip the checkout component's
  `experimental__woocommerce_blocks-checkout-*` actions that integrations (Klaviyo, Pixel Manager)
  listen to. Before replacing a field change with a store write, grep the installed plugins for
  those hooks; if any listen, drive the change through the field.
