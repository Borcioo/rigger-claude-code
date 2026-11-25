# Spike: the pinned shellwise release v0.1.1

## Question

PR B pins shellwise `v0.1.1` (D77) and runs it as the shell-guard filler. Before the module is
written, the published release answers four things:

1. **`pin.json`:** the asset names, sizes and SHA-256 values, and whether every hash source agrees.
2. **`RULES`:** the rule ids the pinned binary documents, and where the rule map's Table 1 no longer
   matches `v0.1.1` (D78).
3. **The hook's reply form:** what `hook claude-code` prints for a deny, a warning and a clean
   command (it decides Task 17's launcher).
4. **D76:** does a detached child started from a SessionStart hook outlive the hook and the
   session, on Windows and on Linux?

## Release

- **Release:** tag `v0.1.1` on `Borcioo/shellwise`, published 2026-11-25T17:29:21Z. The tag object
  `26118c7` points at commit `7cb494c`. It has 9 assets: 4 archives, 4 `.sha256` files and
  `SHA256SUMS`.
- **Download:** all 9 assets into a new empty folder, from
  `https://github.com/Borcioo/shellwise/releases/download/v0.1.1/<asset>`.
- **Hash check:** each archive's SHA-256 was computed locally and compared with three sources: its
  `.sha256` file, its `SHA256SUMS` line, and the `digest` field of `gh release view --json assets`.
  All four values agree for every archive: `# archives 4, mismatches 0`.

| Target | Asset | Size (bytes) | SHA-256 |
|---|---|---|---|
| `x86_64-pc-windows-msvc` | `shellwise-v0.1.1-x86_64-pc-windows-msvc.zip` | 4166887 | `fe30a2b01b979a0fd3c2e965123bbf29ab1620f711b79165a21bc9cb5cf0a5f9` |
| `x86_64-unknown-linux-gnu` | `shellwise-v0.1.1-x86_64-unknown-linux-gnu.tar.gz` | 4620584 | `e4a2a5f3291baef64f63c272e5a28e2bf355cab0b066904833dbc92ab0531dfc` |
| `aarch64-apple-darwin` | `shellwise-v0.1.1-aarch64-apple-darwin.tar.gz` | 4134111 | `206c39687e216db360c214e0f94544299557a0b3cfdc5a003b53362e4f95fa08` |
| `x86_64-apple-darwin` | `shellwise-v0.1.1-x86_64-apple-darwin.tar.gz` | 4416367 | `4a248c43812290fb37e618af39a10e2008d432ebd77b63f28d98e3646caee3cd` |

`SHA256SUMS` itself is 446 bytes. The Windows `.sha256` file writes ` *<name>` (binary marker); the
other three write two spaces before the name. A checker that reads only the first field sees the
same hash in both forms.

## Archives

- **Members:** every archive holds 5 members:
  - a top-level folder `shellwise-v0.1.1-<target>/`;
  - `LICENSE`, `README.md` and `CHANGELOG.md`;
  - the binary: `shellwise.exe` on Windows, `shellwise` elsewhere.
- **No surprises:** no links, no other files and no member outside that folder.

| Target | Binary member | Size (bytes) | Mode in the archive |
|---|---|---|---|
| Windows | `shellwise-v0.1.1-x86_64-pc-windows-msvc/shellwise.exe` | 13223936 | zip, no Unix mode (`external_attr` 0) |
| Linux | `shellwise-v0.1.1-x86_64-unknown-linux-gnu/shellwise` | 14441128 | `755` |
| macOS arm64 | `shellwise-v0.1.1-aarch64-apple-darwin/shellwise` | 12053568 | `755` |
| macOS x86_64 | `shellwise-v0.1.1-x86_64-apple-darwin/shellwise` | 12760880 | `755` |

- **Run on Windows:** the zip was unpacked with `zipfile`. `--version` printed `shellwise 0.1.1`, exit 0.
- **Run on Linux:** the Linux archive was unpacked with `tar -xzf` under WSL 2 (kernel 6.18, `microsoft-standard-WSL2`,
  x86_64). The binary kept mode `-rwxr-xr-x`, and `--version` printed `shellwise 0.1.1`, exit 0.
- **macOS:** both archives were listed only. They were not run.

## RULES

The `v0.1.1` tree (`gh api repos/Borcioo/shellwise/git/trees/v0.1.1?recursive=1`) has 54
`docs/rules/<id>.md` files.

- **Every id explains:** `explain <id>` exits 0 for all 54, on Windows and on Linux (`# ids 54,
  nonzero 0`).
- **Control:** `explain no-such-rule-xyz` exits 64.
- **The id the map dropped is back:** `bash-ps-call-operator` is among the 54.

```
analysis-incomplete bash-backslash-collapse bash-cd-slash-d bash-crlf bash-msys-path-mangle
bash-ps-call-operator bash-ps-cmdlet bash-unquoted-backslash-path bash-vendor-bin-shebang
bash-versioned-php bat-metachar claude-cmd-live-config cli-inline-prose delete-allowed-root
delete-critical delete-empty-expansion delete-outside-project delete-repo-contents
delete-through-link delete-unknown-location dynamic-target follow-unbounded gate-truncated-pipeline
git-discard git-no-verify git-restore-discards git-unreadable git-worktree-junction
inline-script-too-long opaque-destructive parse-error-destructive parse-error ps-alias-shadow
ps-auto-variable ps-bad-parameter ps-bare-bash ps-dotnet-relative-path ps-empty-arg
ps-git-commit-stdin ps-kill-wrapper ps-native-nonewline ps-remove-item-star ps-stdin-script
ps-unix-command ps-var-colon pwsh-file-list-arg rg-follow-L stale-write-var too-long
wait-loop-unbounded walk-timeout walk-unreadable will-hang wsl-argv-rejoin
```

The rule map's Table 2 listed 30 rules with no old-guard ancestor, and Table 1 named 23 mapped
shellwise ids (`too-long`, `parse-error` and `bash-ps-cmdlet` among them). With
`bash-ps-call-operator` that is the 54 above. `config-invalid` and `config-repo-ignored` appear in
output but have no rule doc.

## Integration config

`shellwise.toml` (D78) is the release's `examples/integration-config.toml` at the tag, with one
change. Its line `gate_commands = ["run-tests*", "gate*"]` becomes
`gate_commands = ["safe-edit*", "safe_edit*"]`.

**Diff against the integration config delivered with the rule map:** one line differs, the
`gate_commands` value:
- the delivered file holds `["safe-edit*"]`;
- the release file holds `["run-tests*", "gate*"]`.

Every other line is the same: the comments, `hook_timeout_ms = 5000`, `[policies] git_no_verify = true` and
all 11 `[levels]` keys.

**Key check:**
- **Clean:** no probe below, at defaults or with `shellwise.toml`, printed a `config-*` finding, on
  either system.
- **Positive control:** a config with `hook_timeout_ms = 1000`, an unknown top-level key and an
  unknown level value gave three `config-invalid` warnings in the same JSON field the probes read:
  - "below the minimum 3000";
  - "unknown key";
  - "unknown level".

## Table 1 on v0.1.1

### Method

Each probe went to `check --shell <s> --cwd <scratch repo> --json` on stdin, as the rule map did.
- **Config:** once at defaults and once with `--config shellwise.toml`.
- **User config:** points at an empty folder (`APPDATA` on Windows; `XDG_CONFIG_HOME` and `HOME` on
  Linux).
- **Worktree probes:** a scratch worktree with a junction inside it on Windows, a symlink on Linux.
- **Results:** `<temp>/rigger-p3b/B1-task14/step4_*.txt`.

### Windows

The shells are `gitbash` and `pwsh`, as in the map. 23 of 24 rows are as the map says, at defaults
and with the config: same exit codes and the same rule id. One row differs:

| Old rule id | Map | v0.1.1 (Git Bash, `& "<windows>/System32/whoami.exe"`) |
|---|---|---|
| `bash-ps-call-operator` | `dropped`; default 0, only `parse-error` warn; config 0 | `bash-ps-call-operator` break; default 1, config 1 |

**Notes rows, also as the map says:**
- `bash-backslash-collapse` for `sed -i 's/a\\b/c/' f.txt` and for an expanding heredoc: warn at
  defaults, break with the config.
- `composer --version`: no finding.
- `git push --no-verify`, `git commit -nm x` and `git -c core.hooksPath=/dev/null commit -m x`: no
  finding at defaults, `git-no-verify` danger (exit 2) with the config.
- `git checkout -- src/a.php`: warn at defaults, danger with the config.
- `git checkout -- .` and `git worktree remove --force <wt>`: danger (exit 2) at defaults.

**Rows v0.1.1 changed, as updated:**

| Probe | Shell | Defaults | With `shellwise.toml` |
|---|---|---|---|
| `& "<windows>/System32/whoami.exe"` | gitbash | 1, `bash-ps-call-operator` break | 1, break |
| `& "/usr/bin/whoami"` | bash | 1, `bash-ps-call-operator` break | 1, break |
| `cd x;& "x.exe"` | gitbash | 1, `bash-ps-call-operator` break | 1, break |
| `npm test 2>&1 \| tee run.log \| head -n 5` | gitbash | 0, `gate-truncated-pipeline` warn | 1, break |
| `npm test 2>&1 \| tee run.log \| head -n 5` | bash | 0, `gate-truncated-pipeline` warn | 1, break |
| `pwsh -NoProfile -File safe-edit.ps1 -Run 'x' 2>&1 \| Select-Object -First 30` | pwsh | 0, no finding | 1, `gate-truncated-pipeline` break |

### Linux

The Linux binary runs each row once more. A `gitbash` row runs as `bash`, the shell a Linux Bash
tool gives the hook; a `pwsh` row stays `pwsh`. 12 of 24 rows differ from the map, which was probed
on Windows. A second run that kept `gitbash` on Linux narrows it down: with the same shell name, the
Linux binary gives the Windows answer for every Git Bash row. So the 8 Bash-side differences come
from the shell name, not from the binary.

| Row(s) | Linux result | Why |
|---|---|---|
| `bash-backslash-collapse` (and its sed and heredoc notes), `bash-cd-slash-d`, `bash-msys-path-mangle`, `bash-powershell-syntax`, `bash-unquoted-backslash-path`, `bash-vendor-bin-shebang`, `bash-versioned-php` | 0, no finding, at defaults and with the config | Git Bash rules: they fire for `--shell gitbash` (also on Linux), not for `bash` |
| `bash-too-long` | 1, `inline-script-too-long` break (map: `too-long`) | `too-long` is the Git Bash command-line limit; plain bash reports the inline-script rule |
| `bash-ps-call-operator` | 1, `bash-ps-call-operator` break | as on Windows (v0.1.1) |
| `bat-metachar`, `ps-bad-parameter` (`ls -la`), `ps-bare-bash` | 0, no finding, at defaults and with the config | Windows-only facts: no `.bat` launchers, `ls` is not an alias of `Get-ChildItem` in pwsh on Linux, and `bash` exists |

The other 12 rows give the map's answer on Linux. That includes every git rule, `gate-truncated-pipeline`
for pwsh, `cli-inline-prose`, `ps-git-commit-stdin`, `ps-native-nonewline`, `ps-unix-command`,
`ps-dotnet-relative-path`, `stale-write-var` and `parse-error`.

### D100: script hosts

| Probe | Config | Result |
|---|---|---|
| `python safe_edit.py --run x 2>&1 \| Select-Object -First 30` (pwsh) | `shellwise.toml` | 0, no finding: **gap** (D100) |
| `python -I scripts/gate.py \| head` (bash) | `shellwise.toml` and the release config (`gate*`) | 0, no finding: **gap** (D100) |
| `python -I scripts/gate.py 2>&1 \| Select-Object -First 30` (pwsh) | release config | 0, no finding: **gap** |
| `./safe_edit.py --run x 2>&1 \| head -n 30` (bash) | `shellwise.toml` | 1, `gate-truncated-pipeline` break |
| `scripts/gate.py 2>&1 \| head -n 5`, `./gate.py \| head -n 5` (bash) | release config | 1, break |
| `pwsh -NoProfile -File gate.ps1 2>&1 \| Select-Object -First 30` | release config | 1, break |

**How `gate_commands` matches:**
- **Matched:** the program's own file name, run directly (`./safe_edit.py`, `scripts/gate.py`), and a script run through `pwsh -File`.
- **Not matched:** a script given to `python` as its first argument. This holds with either config, as D100 says.

## Hook reply form

`hook claude-code [--config <file>]` reads a PreToolUse event on stdin.

- **Exit code:** always 0, on Windows and Linux.
- **stderr:** always empty.
- **stdout:** one JSON object and a newline, or nothing.
- **Shell choice:** the Bash tool is analysed as `gitbash` on Windows (it reported `bash-cd-slash-d`)
  and as `bash` on Linux.

| Case | stdout |
|---|---|
| break or danger | `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"shellwise: <level> <rule>: <message>\nFix: <fix>"}}` |
| warn | `{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"shellwise: warn <rule>: <message>\nFix: <fix>"}}` |
| clean | empty |
| `SHELLWISE=off`, non-JSON stdin, empty stdin, a tool other than Bash or PowerShell | empty |

**Cases run:**

| Case | Windows | Linux |
|---|---|---|
| break | Bash `cd /d "<temp>"`, PowerShell `ls -la` | `& "/usr/bin/whoami"` |
| warn | `php83 -v` and PowerShell `grep -n foo file.txt` (deny with the config) | the tee pipeline into `head` (deny with the config) |
| clean | `ls`, `Get-ChildItem -Force` | `ls` |
| danger | `git checkout -- .` in the repo root: deny at defaults, `# shellwise: allow` named in the fix | same |

`git checkout -- .` in a folder that is not a repository is a warn at defaults (the root case needs
the repo).

**For Task 17:** the decision is the reply text alone; the exit code carries nothing. The launcher
passes stdout through unchanged and exits 0. An exit 2 of its own would turn every failure into a deny.

## D76: a detached child from SessionStart

### Setup

One isolated `claude -p` session per system against the local stub:
- **Isolation:** fresh config, cache, inbox, `HOME`/`TEMP`/`TMPDIR`; child env without `CLAUDE*`,
  `ANTHROPIC_*`, `HARNESS_*`, `RIGGER_*`; `ANTHROPIC_BASE_URL` set to the stub; no network, no
  model.
- **Hook chain:** a `--plugin-dir` probe plugin's SessionStart `startup` handler runs through the
  real handlers' chain: `sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice -S <spawn.py>`.
- **Children:** the hook starts two children and returns:
  - a detached child;
  - a plain control child, also with stdin, stdout and stderr on `DEVNULL` and `close_fds`.
- **Markers:** each child writes a start marker at once and an end marker 15 s later.
- **Clock:** the hook logs its start, each spawn's flags or exception, and its return. The runner
  logs the CLI's exit and waits for the end markers in the same process (on WSL inside the same
  `wsl.exe` call, with the experiment on the Linux filesystem).

### CLI

- **Windows:** `command -v claude` gives `<home>/.local/bin/claude`, `2.1.327 (Claude Code)`.
- **WSL:** `<home>/.local/bin/claude` by absolute path, `2.1.323 (Claude Code)`.
- **Hooks:** each session made one stub request and fired the hook once.

### Results

| System | Detached flags that worked | Hook returned | CLI exited | Detached end marker | Plain end marker |
|---|---|---|---|---|---|
| Windows (run 1) | `DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP` | 1.08 s | 1.82 s | 16.11 s | 16.12 s |
| Windows (run 2) | same | 1.16 s | 1.92 s | 16.19 s | 16.19 s |
| Linux (WSL 2) | `start_new_session=True` | 1.15 s | 1.82 s | 16.16 s | 16.17 s |

- **`CREATE_BREAKAWAY_FROM_JOB`:** on Windows, adding it to `Popen` raised
  `PermissionError: [WinError 5]`. The hook process runs inside a job (`IsProcessInJob` true) that
  does not allow breakaway. The installer must catch this and spawn without that flag, as the probe did.
- **The answer for D76:** yes on both systems. The detached child's end marker came 14 s after the
  hook returned and after the CLI exited, so the installer downloads from a detached child.
- **The control child survived too, on both systems.** Neither CLI kills a hook's children when the
  hook or the session ends.
- **Not observed:**
  - an interactive terminal session closed by its window, where a plain child in the terminal's
    process group may get SIGHUP. A new session (`start_new_session`) is not in that group.
  - the desktop app, whose job may end its processes when the app quits.
  - macOS.

## Decision

- **`pin.json`:** version `0.1.1`, the four asset names and SHA-256 values of the Release table,
  sizes for reference. The downloader trusts only these committed values (D77).
- **`RULES`:** the 54 ids of the RULES block. `ALIASES` keeps `bash-powershell-syntax` →
  `bash-ps-cmdlet`, `bash-too-long` → `too-long` and `bash-unterminated-quote` → `parse-error`.
  `bash-ps-call-operator` is now a rule, no longer a drop.
- **`shellwise.toml`:** the release example with `gate_commands = ["safe-edit*", "safe_edit*"]`. All
  keys are accepted on `v0.1.1`.
- **Rule-map rows that differ:**
  - on Windows, only `bash-ps-call-operator`;
  - on Linux, the 12 rows above, all explained by the shell name or by Windows-only facts.
- **Launcher:** pass stdout through and exit 0. A deny is
  `hookSpecificOutput.permissionDecision: "deny"`.
- **D76:** a detached download from the SessionStart handler:
  - Windows: `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, trying `CREATE_BREAKAWAY_FROM_JOB` first only if refusal is handled;
  - POSIX: `start_new_session`;
  - all stdio on `DEVNULL`, no inherited handles.
