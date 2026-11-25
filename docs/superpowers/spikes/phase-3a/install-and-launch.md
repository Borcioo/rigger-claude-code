# Task 17: plugin install forms and the hook launcher

## Question

- **(a)** Does `claude plugin install` install two plugins named in one call?
- **(b)** Does installing a plugin whose `plugin.json` has a `dependencies` entry also install and
  enable that dependency? If it does, the migration does not need to name `rigger-core`.
- **(c)** How does Claude Code start a hook `command`? Through `sh -c`, through some other shell,
  or with no shell at all? Asked on Windows (Git Bash) and on Linux (WSL).

## Answers

| Question | Answer | Form to use |
|---|---|---|
| (a) two plugins in one call | **no.** The command exits 0, installs only the first plugin and says nothing about the second. | One `claude plugin install <plugin>@<marketplace>` call per plugin. |
| (b) a dependency installed with its plugin | **yes.** It is installed and enabled from the same marketplace, a disabled one is enabled again, and an installed one is left alone. | The migration does not need to name `rigger-core`. |
| (c) Windows launcher | **not `sh`.** It is `<Git>\bin\bash.exe -c "<command>"`, which runs `<Git>\usr\bin\bash.exe -c "<command>"`: GNU bash 5.2.37, `$0` `/usr/bin/bash`. | `hook_latency.py` on Windows: `[<Git>\bin\bash.exe, "-c", command]`. |
| (c) Linux launcher (WSL) | **`/bin/sh -c "<command>"`.** On Ubuntu that is dash, `$0` `/bin/sh`, and the parent is `claude`. `SHELL` (zsh here) is not used. | `hook_latency.py` on Linux: `["/bin/sh", "-c", command]`. |

In both launchers the command text reaches the shell unexpanded: the shell itself expands
`${CLAUDE_PLUGIN_ROOT}` from the environment. On Windows that variable holds a path with forward
slashes (`<drive>:/…`). Claude Code's debug log prints the command with the root already substituted.

## CLI

The version of each binary was read in a scratch config dir right before its first run. Each
session's debug log confirms it (`cc_version`).

| Binary | `--version` | Debug log | Used for |
|---|---|---|---|
| PATH CLI, `<home>/.local/bin/claude.exe` (Windows 11, Git for Windows) | `2.1.327 (Claude Code)` | `cc_version=2.1.327.5` | (a), (b), (c) |
| The desktop app's copy, `<home>/AppData/Roaming/Claude/claude-code/2.1.330/<build>/claude.exe` | `2.1.330 (Claude Code)` | `cc_version=2.1.330.879` | (a), (b), (c) |
| WSL Ubuntu, `<home>/.local/bin/claude` → `<home>/.local/share/claude/versions/2.1.323` | `2.1.323 (Claude Code)` | `cc_version=2.1.323.` | (c) |

`<Git>` is the Git for Windows install folder; on this machine it is the default install path.
Claude Code's debug log names the shell it picked: `Using bash path: "<Git>\bin\bash.exe"`.

## Setup

- **Isolation:** every question ran in a fresh `CLAUDE_CONFIG_DIR`, with its own
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` and a fake `HOME` and `USERPROFILE`.
  - The child environment drops every `CLAUDE*`, `ANTHROPIC_*`, `HARNESS_*` and `RIGGER_*`
    variable, which includes the host auth and account ids. It keeps `CLAUDE_CODE_CHILD_SESSION`.
  - It sets `ANTHROPIC_API_KEY=stub-key`, `DISABLE_AUTOUPDATER=1`, `HARNESS_LESSONS_BATCH_OFF=1` and
    `HARNESS_LESSONS_INBOX` to scratch.
  - Under WSL, `CLAUDE_CODE_CHILD_SESSION` was passed through `WSLENV`. The probe ran through the
    login shell (`zsh -lic`).
- **Plugin files go to the cache dir.** With `CLAUDE_CODE_PLUGIN_CACHE_DIR` set,
  `installed_plugins.json` and `known_marketplaces.json` are written there, and
  `<config>/plugins/` is never created. `enabledPlugins` and `extraKnownMarketplaces` go to
  `<config>/settings.json`.
- **Marketplace for (a) and (b):** a scratch directory marketplace named `s17`, added with
  `claude plugin marketplace add <dir>`. This is a `directory` source, not git. It holds three
  plugins:
  - `s17-base` and `s17-one`: `name`, `version` and `description` only.
  - `s17-two`: the same, plus `"dependencies": ["s17-base"]`, the form rigger's plugins use.
- **Probe for (c):** a plugin `s17-probe`, loaded with `--plugin-dir`. It has one `SessionStart`
  group, matcher `startup`, timeout 30, with two handlers on one script:
  - `. "${CLAUDE_PLUGIN_ROOT}/probe.sh"`: sourced, so `$0`, `$BASH_VERSION` and `$$` are the
    launcher shell's own.
  - `sh "${CLAUDE_PLUGIN_ROOT}/probe.sh" child`: rigger's own hook form. Its parent is the launcher.
  - The script writes `$0`, `$BASH_VERSION`, `SHELL`, `/proc/$$/cmdline`, `/proc/$$/exe`,
    `/proc/$PPID/cmdline` and `ps -p $PPID -o args=` to a file.
  - Under Git Bash it also asks Windows for the process behind `/proc/$$/winpid` and for that
    process's parent: `Win32_Process` `ExecutablePath` and `CommandLine`. This is needed because
    a shell started by a native process has no readable MSYS parent (`ppid=1`).
  - It prints one line, `S17-HOOK label=… zero=… bash=… run=<id>`, which reaches the model.
- **Session for (c):** `claude -p <marker> --model haiku --output-format json --settings <file with
  claudeMdExcludes> --debug-file <log> --plugin-dir <probe>`.
  - It runs against `plugins/rigger-rules/tests/stub_api.py` behind `ANTHROPIC_BASE_URL`, so no
    real model is called.
  - `Popen` with a 90 s bound.
- **Windows sessions:**
  - each binary as is;
  - each binary with `SHELL` removed from the child environment;
  - the PATH CLI with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`, which the owner's sessions set.
- **Linux session:** under WSL, with the stub inside WSL.
- **Live state:** before and after every `claude` call, a hash of the live
  `installed_plugins.json`, `known_marketplaces.json` and `settings.json`. It read
  `38e9d833ba4d76ef` in every call and still read it at the end. No live file holds `s17`.
- Scratch: `<temp>/rigger-p3/a0/`, one folder per attempt, with every attempt's log kept.

## Control

- **Isolation canaries:**
  - (a) and (b): a marketplace named `s17canary`, added to a scratch config, appears in that
    config's `known_marketplaces.json` and in no live file. `CANARY PASS` on both Windows binaries.
  - (c), in every session: the prompt marker reached the stub, it is in the scratch config's
    transcript, and it is in no live file (`.claude.json`, `history.jsonl`, `installed_plugins.json`,
    `settings.json`). `CANARY PASS` in all six sessions.
- **(a) one plugin alone:**
  ```text
  $ claude plugin install s17-one@s17
  exit 0
  Installing plugin "s17-one@s17"...✔ Successfully installed plugin: s17-one@s17 (scope: user)
  installed_plugins.json keys: ['s17-one@s17']
  settings.json enabledPlugins: {'s17-one@s17': True}
  ```
  The listing before the install held nothing (`<no file>`, `enabledPlugins: {}`). This run is
  also the negative control for (b): `s17-one` has no dependency, and no `s17-base` appears.
- **(c) the probe run by hand.** Each control's known answer was read before the session runs:

  | Run by hand | `$0` of the sourced probe | Shell |
  |---|---|---|
  | Git Bash: `sh -c ". <probe>"` | `sh` | `<Git>\usr\bin\sh.exe`, bash 5.2.37 |
  | WSL: `sh -c ". <probe>"` | `sh` | `/usr/bin/dash`, `BASH_VERSION` unset |

  The probe tells `sh` and bash apart, and tells a launcher's `$0` apart from a script's.

## Observed

### (a) two plugins in one call

Same result on both binaries, in fresh config dirs:

```text
$ claude plugin install s17-one@s17 s17-two@s17
exit 0
Installing plugin "s17-one@s17"...✔ Successfully installed plugin: s17-one@s17 (scope: user)
installed_plugins.json keys: ['s17-one@s17']
settings.json enabledPlugins: {'s17-one@s17': True}
```

`claude plugin list --json` lists `s17-one@s17` alone. `claude plugin install --help` shows
`Usage: claude plugin install|i [options] <plugin>`. The second argument is dropped without a
word, and the exit code is 0, so a migration line with two plugins would look as if it had worked.

### (b) the dependency

Same result on both binaries. In a fresh config dir, where the listing before the install held
nothing:

```text
$ claude plugin install s17-two@s17
exit 0
Installing plugin "s17-two@s17"...✔ Successfully installed plugin: s17-two@s17 (scope: user) (+ 1 dependency: s17-base)
installed_plugins.json keys: ['s17-base@s17', 's17-two@s17']
settings.json enabledPlugins: {'s17-two@s17': True, 's17-base@s17': True}
```

`claude plugin list` shows `s17-base@s17` with `Status: ✔ enabled`, and `--json` shows
`"enabled": true` for both.

The migration case, run on the PATH CLI:

- **Dependency already installed:** `s17-base` was installed first. Then `claude plugin install
  s17-two@s17` printed no `(+ 1 dependency …)` and left `s17-base` enabled.
- **Dependency installed but disabled:** `s17-base` was installed, then
  `claude plugin disable s17-base@s17` gave `enabledPlugins: {'s17-base@s17': False}`. Then
  `claude plugin install s17-two@s17` printed `(+ 1 dependency: s17-base)` and set
  `{'s17-base@s17': True, 's17-two@s17': True}`.

### (c) the launcher on Windows

Same in all four sessions on Windows (both binaries, with and without `SHELL`) and in the session
with `CLAUDE_CODE_USE_POWERSHELL_TOOL=1`:

```text
SessionStart:startup hook success: S17-HOOK label=sourced zero=/usr/bin/bash bash=5.2.37(1)-release run=<id>
SessionStart:startup hook success: S17-HOOK label=child zero=<temp>/probe/plugin/probe.sh bash=5.2.37(1)-release run=<id>
```

The sourced handler's own process and its parent:

```text
self_cmdline=/usr/bin/bash -c . "${CLAUDE_PLUGIN_ROOT}/probe.sh"
win_self_exe=<Git>\bin\..\usr\bin\bash.exe
win_self_cmdline="<Git>\bin\..\usr\bin\bash.exe" -c ". \"${CLAUDE_PLUGIN_ROOT}/probe.sh\""
win_parent_exe=<Git>\bin\bash.exe
win_parent_cmdline="<Git>\bin\bash.exe" -c ". \"${CLAUDE_PLUGIN_ROOT}/probe.sh\""
```

The `sh "${CLAUDE_PLUGIN_ROOT}/probe.sh" child` handler runs `<Git>\usr\bin\sh.exe` as a child of
`"<Git>\bin\..\usr\bin\bash.exe" -c "sh \"${CLAUDE_PLUGIN_ROOT}/probe.sh\" child"`.

That gives three processes per hook: the `<Git>\bin\bash.exe` wrapper, `usr\bin\bash.exe`, and the
`sh.exe` that rigger's hooks name.

`SHELL` reads `/bin/bash.exe` inside the hook even when the child environment has no `SHELL`, so
the Git Bash launcher sets it. Debug log, PATH CLI:

```text
[DEBUG] Using bash path: "<Git>\bin\bash.exe"
[DEBUG] Hooks: SessionStart:startup [. "<temp>\probe\plugin/probe.sh"] (plugin s17-probe@inline) finished with status 0 (1686ms)
[DEBUG] Hooks: SessionStart:startup [sh "<temp>\probe\plugin/probe.sh" child] (plugin s17-probe@inline) finished with status 0 (1690ms)
```

Those 1.3–1.7 s include the probe's own `powershell.exe` query (the WSL probe, which has none,
took 45 ms). They are not hook latency.

### (c) the launcher on Linux (WSL Ubuntu, 2.1.323)

```text
SessionStart:startup hook success: S17-HOOK label=sourced zero=/bin/sh bash=<unset> run=<id>
SessionStart:startup hook success: S17-HOOK label=child zero=<temp>/probe/plugin/probe.sh bash=<unset> run=<id>
self_cmdline=/bin/sh -c . "${CLAUDE_PLUGIN_ROOT}/probe.sh"
self_exe=/usr/bin/dash
parent_cmdline=<home>/.local/bin/claude -p <marker> --model haiku --output-format json --settings … --plugin-dir …
shell=/usr/bin/zsh
[DEBUG] Hooks: SessionStart:startup [. "<temp>/probe/plugin/probe.sh"] (plugin s17-probe@inline) finished with status 0 (45ms)
```

The `child` handler's parent is `/bin/sh -c sh "${CLAUDE_PLUGIN_ROOT}/probe.sh" child`.

## Decision

- **(a) for Task 15 and D57:** the install command is printed as one call per plugin. The migration
  reads:

  ```text
  claude plugin install rigger-review@rigger
  claude plugin install rigger-testing@rigger
  ```

  D57's notice names one plugin, so its form `claude plugin install <plugin>@<marketplace>` stays
  as planned.
- **(b) for Task 15:** `rigger-core` is not named. Both new plugins declare
  `dependencies: ["rigger-core"]`, and an install from the `rigger` marketplace installs or
  re-enables it and leaves an installed copy alone.
  - **Not covered:** this does not update an old `rigger-core`. (b) shows that an installed
    dependency is left as it is, so the version-skew note about updating rigger-core stays.
- **(c) for Task 3 (D66):** `measure` starts a handler the way Claude Code does:
  - **Windows:** `[<Git>\bin\bash.exe, "-c", command]`, the file the debug line
    `Using bash path:` names. How Claude Code finds that file was not observed here (one Git
    install, at its default path). Task 3 resolves `bash.exe` in `<Git>\bin` and prints the path
    it used.
  - **Linux:** `["/bin/sh", "-c", command]`.
  - **macOS:** not observed (no local macOS). The Linux form `/bin/sh -c` is assumed, and Task 3's
    result file says so.
  - **Both systems:** the command text is passed as written, with `CLAUDE_PLUGIN_ROOT` in the
    environment. On Windows its value has forward slashes.
  - **The Windows floor** is the time of `<Git>\bin\bash.exe -c 'exit 0'`, not `sh -c 'exit 0'`.
    The wrapper adds a second process before rigger's own `sh`, so the plan's 53–62 ms floor for
    `sh -c` understates what a person pays.
