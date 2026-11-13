"""Rule checker for the rigger-lessons lint (`lessons_lint.py --rule-checker <this file>`).

A lessons case whose guard is `live:<rule id>` passes only when the shell-guard hook next to this file
(shell_guard.py) denies the case's `probe` with that rule, in a shell the case's `tools` name. The lint
routes a guard here when RULES holds its rule id, whatever the case's `tools` say, and when the case's
`tools` name a shell.
"""

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL_GUARD = os.path.join(HERE, "shell_guard.py")
# Every rule id shell_guard.py reports (tools/token-audit/tests/test_archive.py of rigger-core checks the two agree).
RULES = frozenset((
    "bash-backslash-collapse", "bash-cd-slash-d", "bash-msys-path-mangle", "bash-powershell-syntax", "bash-ps-call-operator", "bash-too-long",
    "bash-unquoted-backslash-path", "bash-unterminated-quote", "bash-vendor-bin-shebang", "bash-versioned-php",
    "bat-metachar", "cli-inline-prose", "gate-truncated-pipeline", "git-no-verify", "git-restore-discards",
    "git-worktree-junction", "ps-bad-parameter", "ps-bare-bash", "ps-dotnet-relative-path", "ps-git-commit-stdin",
    "ps-native-nonewline", "ps-unix-command", "stale-write-var",
))
# `tools` entries that name a shell, and the tool name check_installed takes for it
SHELLS = {"powershell": "PowerShell", "bash": "Bash", "git-bash": "Bash"}

_loaded = {}


def load_shell_guard(path=SHELL_GUARD):
    """(module, None) for shell_guard.py, or (None, problem) when it cannot be used."""
    if path in _loaded:
        return _loaded[path]
    if not os.path.isfile(path):
        result = (None, f"cannot read {path}")
    else:
        try:
            spec = importlib.util.spec_from_file_location("lessons_guard_shell_guard", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # any failure to load it means no live shell-guard guard can be shown
            result = (None, f"cannot load {path} ({type(exc).__name__}: {exc})")
        else:
            if callable(getattr(module, "check_installed", None)):
                result = (module, None)
            else:
                result = (None, f"{path} has no check_installed")
    _loaded[path] = result
    return result


def rule_guard_problem(rule, meta, path=SHELL_GUARD):
    """None when the hook denies the case's probe with `rule` in a shell the case names."""
    probe = meta.get("probe")
    if not isinstance(probe, str) or not probe.strip():
        return f"guard live:{rule}: needs a probe: a command of the failure shape that the hook denies"
    tools = meta.get("tools") if isinstance(meta.get("tools"), list) else []
    shells = list(dict.fromkeys(SHELLS[t] for t in tools if t in SHELLS))
    if not shells:
        return f"guard live:{rule}: tools names no shell ({', '.join(SHELLS)}) to run the probe in"
    module, problem = load_shell_guard(path)
    if module is None:
        return f"guard live:{rule}: {problem}"
    got = []
    for shell in shells:
        try:
            findings = list(module.check_installed(shell, probe) or [])
        except Exception as exc:  # a guard that crashes on the probe denies nothing
            got.append(f"{shell}: raised {type(exc).__name__}")
            continue
        denies = [getattr(f, "rule", repr(f)) for f in findings if getattr(f, "deny", False) is True]
        if rule in denies:
            return None
        got.append(f"{shell}: {', '.join(denies) or 'no deny'}")
    return f"guard live:{rule}: check_installed does not deny the probe with {rule} ({'; '.join(got)})"
