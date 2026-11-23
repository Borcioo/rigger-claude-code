## WordPress skill, loaded before the first step of its kind

On a local or staging site copy, `templates/wp-outbound-block.php` under the `Plugin root:` line above
blocks outgoing HTTP and mail; its header says how to load it.

| Before | Skill |
|---|---|
| WP plugin/theme code, WP integration tests, migrations, roles, enqueues | `rigger-wp:rigger-wp-traps` |
| any other WordPress task (blocks, theme.json, REST API, WP-CLI, performance, Playground, wp-env, PHPStan): the router indexes the upstream skills (`wp-*`, `blueprint`, `wpds`) and you Read the ones the task needs | `rigger-wp:wordpress-router` |
