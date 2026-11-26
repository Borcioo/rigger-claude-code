"""Rule checker for the rigger-lessons lint (`lessons_lint.py --rule-checker <this file>`).

A lessons case whose guard is `live:<rule id>` passes only when the pinned shellwise binary denies the
case's `probe` with that rule (a finding of that rule at break or danger), in a shell the case's `tools`
name. The lesson lands in this machine's rules, so the check answers for this host's shell: `bash` is
Git Bash on Windows and plain bash elsewhere.

The binary is the one named on the line of `<data folder>/shellwise/current`, as for the hook launcher.
The data folder is $CLAUDE_PLUGIN_DATA when its name starts with `rigger-shellwise-` (`<plugin>-<marketplace>`
or `<plugin>-inline`); otherwise the one `rigger-shellwise-*` folder holding `shellwise/current` in the data
root (the parent of $CLAUDE_PLUGIN_DATA, else `<CLAUDE_CONFIG_DIR or ~/.claude>/plugins/data`). No such
folder, or more than one: every guard sent here is a problem that says `no checker`.
"""

import glob
import json
import os
import subprocess

PLUGIN = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG = os.path.join(PLUGIN, "shellwise.toml")
# The rule ids shellwise v0.2.0 documents (`explain <id>` exits 0 for each).
RULES = frozenset((
    "analysis-incomplete", "bash-backslash-collapse", "bash-cd-slash-d", "bash-crlf", "bash-msys-path-mangle",
    "bash-ps-call-operator", "bash-ps-cmdlet", "bash-unquoted-backslash-path", "bash-vendor-bin-shebang",
    "bash-versioned-php", "bat-metachar", "claude-cmd-live-config", "cli-inline-prose", "delete-allowed-root",
    "delete-critical", "delete-empty-expansion", "delete-outside-project", "delete-repo-contents",
    "delete-through-link", "delete-unknown-location", "dynamic-target", "follow-unbounded",
    "gate-truncated-pipeline", "git-discard", "git-no-verify", "git-restore-discards", "git-unreadable",
    "git-worktree-junction", "inline-script-too-long", "opaque-destructive", "parse-error-destructive",
    "parse-error", "ps-alias-shadow", "ps-auto-variable", "ps-bad-parameter", "ps-bare-bash",
    "ps-dotnet-relative-path", "ps-empty-arg", "ps-git-commit-stdin", "ps-kill-wrapper", "ps-native-nonewline",
    "ps-remove-item-star", "ps-stdin-script", "ps-unix-command", "ps-var-colon", "pwsh-file-list-arg",
    "rg-follow-L", "stale-write-var", "too-long", "wait-loop-unbounded", "walk-timeout", "walk-unreadable",
    "will-hang", "wsl-argv-rejoin",
))
# Old shell-guard rule ids and the shellwise rule that replaced each.
ALIASES = {
    "bash-powershell-syntax": "bash-ps-cmdlet",
    "bash-too-long": "too-long",
    "bash-unterminated-quote": "parse-error",
}
DENY_LEVELS = ("break", "danger")
# `tools` entries that name a shell. The Bash tool is Git Bash on Windows (as the hook analyses it).
TOOL_NAMES = ("powershell", "bash", "git-bash")
CHECK_BOUND = 30.0
DATA_PREFIX = "rigger-shellwise-"


def shell_for(tool, windows):
    return {"powershell": "pwsh", "git-bash": "gitbash", "bash": "gitbash" if windows else "bash"}[tool]


def find_data_dir():
    """(rigger-shellwise's data folder, None), or (None, reason)."""
    own = os.environ.get("CLAUDE_PLUGIN_DATA")
    if own and os.path.basename(os.path.normpath(own)).startswith(DATA_PREFIX):
        return own, None
    if own:
        root = os.path.dirname(os.path.normpath(own))
    else:
        config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        root = os.path.join(config, "plugins", "data")
    hits = [path for path in sorted(glob.glob(os.path.join(glob.escape(root), DATA_PREFIX + "*", "shellwise", "current")))
            if os.path.isfile(path)]
    if len(hits) == 1:
        return os.path.dirname(os.path.dirname(hits[0])), None
    return None, "%d %s* folders with shellwise/current in %s" % (len(hits), DATA_PREFIX, root)


def find_binary(data_dir):
    """(path, None) for the binary `current` names, or (None, reason)."""
    current = os.path.join(data_dir, "shellwise", "current")
    try:
        with open(current, encoding="utf-8") as fh:
            line = fh.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None, "cannot read %s" % current
    if not line:
        return None, "%s is empty" % current
    if not os.path.isfile(line) or (os.name != "nt" and not os.access(line, os.X_OK)):
        return None, "%s is not an executable file" % line
    return line, None


def findings_of(stdout):
    """[(rule, level)] of a `check --json` reply, or None when it is not one."""
    try:
        data = json.loads(stdout)
    except ValueError:
        return None
    found = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(found, list):
        return None
    pairs = [(f.get("rule"), f.get("level")) for f in found if isinstance(f, dict)]
    if len(pairs) != len(found) or not all(isinstance(r, str) and isinstance(lv, str) for r, lv in pairs):
        return None
    return pairs


def run_check(binary, shell, probe, launch):
    """(findings, None) or (None, what went wrong)."""
    argv = (launch(binary) if launch else [binary]) + ["check", "--shell", shell, "--json", "--config", CONFIG]
    try:
        proc = subprocess.run(argv, input=probe.encode("utf-8"), capture_output=True, timeout=CHECK_BOUND)
    except subprocess.TimeoutExpired:
        return None, "%s: timed out after %g s" % (shell, CHECK_BOUND)
    except OSError as exc:
        return None, "%s: cannot run (%s)" % (shell, exc)
    findings = findings_of(proc.stdout.decode("utf-8", "replace"))
    if findings is None:
        return None, "%s: exit %d" % (shell, proc.returncode)
    return findings, None


def rule_guard_problem(rule, meta, *, data_dir=None, launch=None, windows=None):
    """None when shellwise denies the case's probe with `rule` (or the rule an old id became) in a shell
    the case names."""
    probe = meta.get("probe")
    if not isinstance(probe, str) or not probe.strip():
        return f"guard live:{rule}: needs a probe: a command of the failure shape that the hook denies"
    windows = os.name == "nt" if windows is None else windows
    tools = meta.get("tools") if isinstance(meta.get("tools"), list) else []
    shells = list(dict.fromkeys(shell_for(t, windows) for t in tools if t in TOOL_NAMES))
    if not shells:
        return f"guard live:{rule}: tools names no shell ({', '.join(TOOL_NAMES)}) to run the probe in"
    folder, reason = (data_dir, None) if data_dir is not None else find_data_dir()
    binary, reason = find_binary(folder) if folder else (None, reason)
    if binary is None:
        return f"guard live:{rule}: no checker (no shellwise binary: {reason})"
    target = ALIASES.get(rule, rule)
    got, broken = [], []
    for shell in shells:
        findings, problem = run_check(binary, shell, probe, launch)
        if findings is None:
            broken.append(problem)
            continue
        if any(r == target and level in DENY_LEVELS for r, level in findings):
            return None
        got.append(f"{shell}: {', '.join('%s %s' % pair for pair in findings) or 'no finding'}")
    if broken:
        return f"guard live:{rule}: shellwise check gave no readable reply ({'; '.join(broken + got)})"
    return f"guard live:{rule}: shellwise check does not deny the probe with {target} at break or above ({'; '.join(got)})"
