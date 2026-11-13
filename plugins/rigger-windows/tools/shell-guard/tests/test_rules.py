"""Hand-written cases for every rule, plus the corpus check on a small synthetic corpus.

python -I test_rules.py
  * CASES: (tool, command, expected rule or None)
  * fixtures/corpus.jsonl: synthetic failures and successes with known counts (corpus_check.py);
    a maintainer runs the mined corpora with `corpus_check.py --corpus <file>`.
Ends with `# shell-guard: <n> passed, <m> failed`. The commit rule is in test_commit_gate.py.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

if os.name != "nt":
    print("# shell-guard: skipped on %s: Windows-only (the hook guards native Windows shells; the cases "
          "make cmd.exe junctions)" % sys.platform)
    sys.exit(0)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8")
# The optional tools a fix may name and the Herd and Composer launchers exist only on the user's machine:
# fake files under a fake home, first on PATH, set before the import reads them, restored in main().
TOOL_ENV = ("RIGGER_WPCLI_PHAR",)
HOME_ENV = ("USERPROFILE", "HOME", "APPDATA", "PATH")
SAVED_ENV = {name: os.environ.get(name) for name in TOOL_ENV + HOME_ENV}
TOOLS = tempfile.mkdtemp(prefix="sg-tools-")
WPCLI = os.path.join(TOOLS, "wp-cli.phar")
FAKE_HOME = os.path.join(TOOLS, "home")
FAKE_APPDATA = os.path.join(FAKE_HOME, "AppData", "Roaming")
FAKE_HERD = os.path.join(FAKE_HOME, ".config", "herd", "bin")
FAKE_COMPOSER = os.path.join(FAKE_APPDATA, "Composer", "vendor", "bin")


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")
    return path


for _path in [WPCLI] + [os.path.join(FAKE_HERD, n) for n in (
        "php84\\php.exe", "php83\\php.exe", "php.bat", "php83.bat", "php84.bat", "composer.bat", "composer.phar",
        "wp.bat")] + [os.path.join(FAKE_COMPOSER, n) for n in ("phpcs", "phpcs.bat")]:
    touch(_path)
os.environ.update(USERPROFILE=FAKE_HOME, HOME=FAKE_HOME, APPDATA=FAKE_APPDATA,
                  PATH=os.pathsep.join([FAKE_HERD, FAKE_COMPOSER, os.environ.get("PATH", "")]))
os.environ["RIGGER_WPCLI_PHAR"] = WPCLI
import shell_guard  # noqa: E402
import corpus_check  # noqa: E402
from rules_common import GUARD, _bounded  # noqa: E402
HERD = shell_guard.HERD_BIN_WIN
WRITE_REPORT = "[IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)"
READ_RELATIVE = "[IO.File]::ReadAllText('tools\\x.php')"

CASES = [
    # Bash: should deny
    ("Bash", "cd X:\\src\\x && git status", "bash-unquoted-backslash-path"),
    ("Bash", "git -C X:\\src\\timer status", "bash-unquoted-backslash-path"),
    ("Bash", 'ls "X:\\src\\x\\tests\\"', "bash-unterminated-quote"),
    ("Bash", "php artisan tinker --execute 'echo \\'x\\';'", "bash-unterminated-quote"),
    ("Bash", "cd /d X:\\src\\x && dotnet test", "bash-cd-slash-d"),
    ("Bash", "Get-ChildItem -Path C:/x -Recurse | Select-Object -First 5", "bash-powershell-syntax"),
    ('Bash', 'cd "D:/x" && & "C:/tools/php.exe" -v', "bash-ps-call-operator"),
    ("Bash", "cd D:/x && php83 -l a.php", "bash-versioned-php"),
    ("Bash", "vendor/bin/pint --dirty 2>&1 | tail -5", "bash-vendor-bin-shebang"),
    ("Bash", "docker exec web php /var/www/html/x.php", "bash-msys-path-mangle"),
    ("Bash", 'php artisan test --filter="FooTest|BarTest"', "bat-metachar"),
    ("Bash", "cat > a.py <<'EOF'\n" + "x = 'y'\n" * 1200 + "EOF\npython a.py", "bash-too-long"),
    # Bash: must allow
    ("Bash", 'cd "X:/src/x" && git status', None),
    ("Bash", "cd /d && ls", None),
    ("Bash", "cd /d 2>/dev/null; ls", None),
    ("Bash", "grep -n 'a\\|b' file.txt", None),
    ("Bash", "printf '%s\\n' \"$(git rev-parse HEAD)\"", None),
    ("Bash", "MSYS_NO_PATHCONV=1 docker exec web php /var/www/html/x.php", None),
    ("Bash", "python - <<'EOF'\nprint(\"it's fine\")\nEOF", None),
    ("Bash", 'echo "a \\" b"', None),
    ("Bash", "php artisan test --filter=\"Foo Bar|Baz\"", None),
    ("Bash", "for f in *.php; do php -l \"$f\"; done", None),
    ("Bash", "x=$( (cd sub && ls) | wc -l ); echo $x", None),
    ("Bash", "X:\\\\src\\\\x\\\\tool.exe --help", "bash-backslash-collapse"),   # bash gets one backslash per pair
    ("Bash", "echo ok # shell-guard: allow\ncd D:\\x", None),
    # PowerShell: should deny
    ("PowerShell", "npm test 2>&1 | tail -20", "ps-unix-command"),
    ("PowerShell", "Get-Content a.txt | grep foo", "ps-unix-command"),
    ("PowerShell", "export FOO=1; node x.js", "ps-unix-command"),
    ("PowerShell", "ls -la D:\\x", "ps-bad-parameter"),
    ("PowerShell", "Select-String -Path app -Pattern x -Recurse", "ps-bad-parameter"),
    ("PowerShell", "vendor\\bin\\phpunit --filter 'A|B'", "bat-metachar"),
    ("PowerShell", "php artisan test --filter='CommentApiTest|WorkLogApiTest'", "bat-metachar"),
    # PowerShell: must allow
    ("PowerShell", "npm test 2>&1 | Select-Object -Last 20", None),
    ("PowerShell", "ssh host 'tail -n 5 /var/log/x | grep y'", None),
    ("PowerShell", "rg -n 'tail|head' .", None),
    ("PowerShell", "php artisan test --filter='A B|C'", None),
    ("PowerShell", "& \"%s\\php84\\php.exe\" vendor/bin/phpunit --filter 'A|B'" % HERD, None),
    ("PowerShell", "$x = @{ a = 1 }; if ($x.a -eq 1) { 'ok' }", None),
    ("PowerShell", "$s = @'\nline | tail\n'@\n$s", None),
    ("PowerShell", "cd X:\\src\\x && git status", None),
    ("PowerShell", "Remove-Item -Recurse -Force .\\tmp", None),
    # git rules (both the full guard and --only-git): should deny
    ("PowerShell", "cd X:\\src\\x; git commit -q -F - @'\nfix: x\n\nbody\n'@", "ps-git-commit-stdin"),
    ("PowerShell", "git -C X:\\src\\x commit -F - @'\nmsg\n'@", "ps-git-commit-stdin"),
    ("PowerShell", "git add a.txt; git commit --file=- @'\nmsg\n'@; git log -1", "ps-git-commit-stdin"),
    ("PowerShell", "git commit -qF - @'\nmsg\n'@", "ps-git-commit-stdin"),
    ("PowerShell", "git checkout -- src/a.js", "git-restore-discards"),
    ("PowerShell", "git -C D:\\x checkout HEAD -- src/a.js src/b.js", "git-restore-discards"),
    ("PowerShell", "git checkout .", "git-restore-discards"),
    ("PowerShell", "git restore src/a.js", "git-restore-discards"),
    ("PowerShell", "git restore --staged --worktree src/a.js", "git-restore-discards"),
    ("Bash", "git checkout -- a.txt && npm test", "git-restore-discards"),
    # git rules: must allow
    ("PowerShell", "@'\nmsg\n'@ | git commit -F -", None),
    ("PowerShell", "Get-Content msg.txt |\n  git commit -F -", None),
    ("PowerShell", "git commit -m @'\nmsg\n'@", None),
    ("PowerShell", "git commit -F X:\\tmp\\msg.txt", None),
    ("PowerShell", "git commit -F - @'\nmsg\n'@ # shell-guard: allow", None),
    ("Bash", "git commit -F - <<'EOF'\nmsg\nEOF", None),
    ("PowerShell", "git restore --staged a.txt", None),
    ("PowerShell", "git checkout --ours -- a.pot b.mo", None),
    ("PowerShell", "git checkout main", None),
    ("PowerShell", "git checkout -b feature/x", None),
    ("PowerShell", "rg -n 'git checkout -- x' notes.md", None),
    ("PowerShell", "git checkout -- a.txt # shell-guard: allow", None),
    # git-no-verify: every spelling git 2.53 accepted in the probe (a failing hook did not run)
    ("PowerShell", "git commit --no-verify -m 'fix: x'", "git-no-verify"),
    ("PowerShell", "git commit -n -m x", "git-no-verify"),
    ("PowerShell", "git commit -nm x", "git-no-verify"),
    ("PowerShell", "git add -A; git commit -anm x", "git-no-verify"),
    ("PowerShell", "git commit --no-veri -m x", "git-no-verify"),
    ("PowerShell", "git -C D:\\x commit -qn -m x", "git-no-verify"),
    ("PowerShell", "git commit -C HEAD -n", "git-no-verify"),
    ("PowerShell", "git commit -m @'\nfix: x\n'@ --no-verify", "git-no-verify"),
    ("PowerShell", "git push --no-verify origin feature/x", "git-no-verify"),
    ("PowerShell", "git push origin feature/x --no-veri", "git-no-verify"),
    ("Bash", "git add -A && git commit --no-verify -m x", "git-no-verify"),
    ("Bash", "echo a && \\\n  git commit --no-verify -m m", "git-no-verify"),
    ("Bash", "echo a && \\\ngit commit --no-verify -m m", "git-no-verify"),
    ("Bash", "git \\\n  commit --no-verify -m m", "git-no-verify"),
    ("Bash", "echo a && \\\n  git commit -m m", None),
    ("Bash", "echo a \\\n# git commit --no-verify\n", None),
    # ... allowed: the word as a message or another option's value, re-enabled, push's -n (dry run)
    ("PowerShell", "git commit -m '--no-verify'", None),
    ("PowerShell", "git commit -m x -m 'never use --no-verify or -n'", None),
    ("PowerShell", "git commit --message --no-verify", None),
    ("PowerShell", "git commit --author 'A -n <a@b.c>' -m x", None),
    ("PowerShell", "git commit --no-verify --verify -m x", None),
    ("PowerShell", "git commit --no-ve -m x", None),
    ("PowerShell", "git push -n origin feature/x", None),
    ("PowerShell", "git log -n 5", None),
    ("Bash", "git commit -m \"don't pass --no-verify\"", None),
    ("PowerShell", "git commit --no-verify -m x # shell-guard: allow", None),
    # ... after `--` only pathspecs follow; the value of push's -o; -u/-S take an attached value
    ("PowerShell", "git commit -m x -- --no-verify", None),
    ("PowerShell", "git push -o --no-verify origin x", None),
    ("PowerShell", "git commit -uno -m x", None),
    # ... on merge, rebase and am, each spelling as git 2.53 reads it on that command
    ("PowerShell", "git merge --no-verify side", "git-no-verify"),
    ("Bash", "git merge side --no-verify", "git-no-verify"),
    ("PowerShell", "git merge --verify --no-verify side", "git-no-verify"),
    ("PowerShell", "git merge --log --no-verify side", "git-no-verify"),
    ("PowerShell", "git merge -S --no-verify side", "git-no-verify"),
    ("PowerShell", "git rebase --no-verify main", "git-no-verify"),
    ("PowerShell", "git rebase --no-veri main", "git-no-verify"),
    ("PowerShell", "git rebase --verify --no-verify main", "git-no-verify"),
    ("PowerShell", "git rebase -r --no-verify main", "git-no-verify"),
    ("PowerShell", "git rebase -S --no-verify main", "git-no-verify"),
    ("PowerShell", "git rebase -C1 --no-verify main", "git-no-verify"),
    ("Bash", "git am --no-verify p.patch", "git-no-verify"),
    ("PowerShell", "git am --no-v p.patch", "git-no-verify"),
    ("PowerShell", "git am -n p.patch", "git-no-verify"),
    ("PowerShell", "git am -3n p.patch", "git-no-verify"),
    ("PowerShell", "git am -nq p.patch", "git-no-verify"),
    ("PowerShell", "git am -C1 -n p.patch", "git-no-verify"),
    ("PowerShell", "git am -p1 -n p.patch", "git-no-verify"),
    # ... allowed: `-n` is --no-stat there, merge refuses a prefix, re-enabled, or the word is an option's value
    ("PowerShell", "git merge -n side", None),
    ("PowerShell", "git merge -nq side", None),
    ("PowerShell", "git merge --no-veri side", None),
    ("PowerShell", "git merge --no-verif side", None),
    ("PowerShell", "git merge --no-verify-signatures side", None),
    ("PowerShell", "git merge --no-verify --verify side", None),
    ("PowerShell", "git merge --no-verify --veri side", "git-no-verify"),   # git refuses --veri here
    ("PowerShell", "git merge -m --no-verify side", None),
    ("PowerShell", "git merge -qm --no-verify side", None),
    ("PowerShell", "git merge -F --no-verify side", None),
    ("PowerShell", "git merge -s --no-verify side", None),
    ("PowerShell", "git merge -X --no-verify side", None),
    ("PowerShell", "git merge --message --no-verify side", None),
    ("PowerShell", "git merge --mes --no-verify side", None),
    ("PowerShell", "git merge --file --no-verify side", None),
    ("PowerShell", "git merge --strategy --no-verify side", None),
    ("PowerShell", "git merge --strategy-option --no-verify side", None),
    ("PowerShell", "git merge --into-name --no-verify side", None),
    ("PowerShell", "git merge --cleanup --no-verify side", None),
    ("PowerShell", "git merge -Sn side", None),
    ("PowerShell", "git merge -Sm --no-verify side", "git-no-verify"),   # -S holds its key id
    ("PowerShell", "git rebase -Sx --no-verify main", "git-no-verify"),
    ("PowerShell", "git am -Sp --no-verify p.patch", "git-no-verify"),
    ("PowerShell", "git rebase -n main", None),
    ("PowerShell", "git rebase --no-ver main", None),
    ("PowerShell", "git rebase --no-verify --veri main", None),
    ("PowerShell", "git rebase --no-verify --ver main", "git-no-verify"),   # git refuses --ver here
    ("PowerShell", "git rebase --onto --no-verify main", None),
    ("PowerShell", "git rebase --strategy --no-verify main", None),
    ("PowerShell", "git rebase --strategy-option --no-verify main", None),
    ("PowerShell", "git rebase --exec --no-verify main", None),
    ("PowerShell", "git rebase --whitespace --no-verify main", None),
    ("PowerShell", "git rebase --empty --no-verify main", None),
    ("PowerShell", "git rebase -x --no-verify main", None),
    ("PowerShell", "git rebase -qx --no-verify main", None),
    ("PowerShell", "git rebase -s --no-verify main", None),
    ("PowerShell", "git rebase -X --no-verify main", None),
    ("PowerShell", "git rebase -C --no-verify main", None),
    ("PowerShell", "git rebase -Sn main", None),
    ("PowerShell", "git am --no- p.patch", None),
    ("PowerShell", "git am --no-verify --v p.patch", None),
    ("PowerShell", "git am -p --no-verify p.patch", None),
    ("PowerShell", "git am -p -n p.patch", None),
    ("PowerShell", "git am -C -n p.patch", None),
    ("PowerShell", "git am -Sn p.patch", None),
    ("PowerShell", "git am --directory --no-verify p.patch", None),
    ("PowerShell", "git am --exclude --no-verify p.patch", None),
    ("PowerShell", "git am --include --no-verify p.patch", None),
    ("PowerShell", "git am --whitespace --no-verify p.patch", None),
    ("PowerShell", "git am --patch-format --no-verify p.patch", None),
    ("PowerShell", "git am --resolvemsg --no-verify p.patch", None),
    ("PowerShell", "git am --quoted-cr --no-verify p.patch", None),
    ("PowerShell", "git am --empty --no-verify p.patch", None),
    # ... cherry-pick refuses --no-verify, and its -n is --no-commit
    ("PowerShell", "git cherry-pick --no-verify side", None),
    ("PowerShell", "git cherry-pick -n side", None),
    # git-no-verify: a hooks path given for one call, a key compared as git compares it
    ("Bash", "git -c core.hooksPath=/dev/null commit -m x", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=.husky commit -m x", "git-no-verify"),
    ("PowerShell", "git -c Core.HooksPath=x commit -m m", "git-no-verify"),
    ("PowerShell", "git -c core.HOOKSPATH=x commit -m m", "git-no-verify"),
    ("Bash", "git -c core.hooksPath= commit -m m", "git-no-verify"),
    ("Bash", "git -c core.hooksPath commit -m m", "git-no-verify"),
    ("Bash", "git -c 'core.hooksPath=x' commit -m m", "git-no-verify"),
    ("PowerShell", "git -c \"core.hooksPath=x\" commit -m m", "git-no-verify"),
    ("PowerShell", "git '-c' core.hooksPath=x commit -m m", "git-no-verify"),
    ("PowerShell", "git -c core.hook`sPath=x commit -m m", "git-no-verify"),
    ("PowerShell", "git -c \"core.hook`sPath=x\" commit -m m", "git-no-verify"),
    ("Bash", "git -c core.hook\"sPath\"=x commit -m m", "git-no-verify"),
    ("Bash", "git -c core.hook\\sPath=x commit -m m", "git-no-verify"),
    ("Bash", "git -c $'core.hooksPath=x' commit -m m", "git-no-verify"),
    ("Bash", "git -c $'core.hooks\\x50ath=x' commit -m m", "git-no-verify"),
    ("Bash", "git -c $\"core.hooksPath=x\" commit -m m", "git-no-verify"),
    ("Bash", "\"git\" -c core.hooksPath=x commit -m m", "git-no-verify"),
    ("PowerShell", "git --% -c core.hooksPath=x commit -m m", "git-no-verify"),
    ("PowerShell", "git -c user.name=x -c core.hooksPath=y commit -m m", "git-no-verify"),
    ("PowerShell", "git -C D:\\x -c core.hooksPath=y commit -m m", "git-no-verify"),
    ("PowerShell", "git --attr-source HEAD -c core.hooksPath=y commit -m m", "git-no-verify"),
    ("PowerShell", "git --attr-source HEAD commit --no-verify -m m", "git-no-verify"),
    ("PowerShell", "git --shallow-file x commit --no-verify -m m", "git-no-verify"),
    ("Bash", "git --config-env=core.hooksPath=V commit -m m", "git-no-verify"),
    ("Bash", "git --config-env core.hooksPath=V commit -m m", "git-no-verify"),
    ("PowerShell", "git --config-env=CORE.hookspath=V commit -m m", "git-no-verify"),
    ("PowerShell", "git --config-env=include.path=V commit -m m", "git-no-verify"),
    ("PowerShell", "git --config-env=includeIf.onbranch:main.path=V commit -m m", "git-no-verify"),
    ("PowerShell", "git --config-env=includeIf.a=b.path=V commit -m m", "git-no-verify"),   # the last `=`
    ("PowerShell", "git -c include.path=/x/c commit -m m", "git-no-verify"),
    ("PowerShell", "git -c INCLUDE.PATH=/x/c commit -m m", "git-no-verify"),
    ("PowerShell", "git -c Include.Path=/x/c commit -m m", "git-no-verify"),
    ("PowerShell", "git -c includeIf.gitdir:/x/.path=/y commit -m m", "git-no-verify"),
    ("PowerShell", "git -c includeif.gitdir/i:a.b/.path=/y commit -m m", "git-no-verify"),
    ("PowerShell", "git -c INCLUDEIF.onbranch:main.PATH=/y commit -m m", "git-no-verify"),
    # ... git compares the condition's case and ignores this one: refused all the same, as the class is clear
    ("PowerShell", "git -c includeIf.GITDIR:/x/.path=/y commit -m m", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=x push origin main", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=x merge side", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=x rebase main", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=x cherry-pick side", "git-no-verify"),
    ("PowerShell", "git -c core.hooksPath=x am p.patch", "git-no-verify"),
    # ... a key read through a variable; one that cannot be read names no harmless key
    ("Bash", "k=core.hooksPath; git -c $k=x commit -m m", "git-no-verify"),
    ("Bash", "k=core.hooksPath; git -c \"${k}=x\" commit -m m", "git-no-verify"),
    ("PowerShell", "$k='core.hooksPath'; git -c \"$k=x\" commit -m m", "git-no-verify"),
    ("PowerShell", "$k='core.hooksPath=x'; git -c $k commit -m m", "git-no-verify"),
    ("Bash", "git -c \"$opt\" commit -m m", "git-no-verify"),
    ("Bash", "git -c \"$(cat key)=x\" commit -m m", "git-no-verify"),
    ("PowerShell", "git -c $opt commit -m m", "git-no-verify"),
    ("PowerShell", "git -c (Get-Opt) commit -m m", "git-no-verify"),
    ("Bash", "k=user.name; git -c $k=x commit -m m", None),
    ("PowerShell", "$k='user.name=x'; git -c $k commit -m m", None),
    # ... allowed: another key, one with a subsection or none where git needs the other, text, another command
    ("Bash", "git -c user.name=x commit -m \"core.hooksPath\"", None),
    ("PowerShell", "git -c user.name=x commit -m 'core.hooksPath'", None),
    ("PowerShell", "git -c core.pager=cat log", None),
    ("PowerShell", "git -c core.hooksPath=x log", None),
    ("PowerShell", "git -c core.hooksPath=x status", None),
    ("PowerShell", "git -c core.x.hooksPath=y commit -m m", None),
    ("PowerShell", "git -c core..hooksPath=y commit -m m", None),
    ("PowerShell", "git -c core.hooksPathx=y commit -m m", None),
    ("PowerShell", "git -c xcore.hooksPath=y commit -m m", None),
    ("PowerShell", "git -c include.x.path=y commit -m m", None),
    ("PowerShell", "git -c includeIf.path=y commit -m m", None),
    ("PowerShell", "git -c includeIf.x.pathx=y commit -m m", None),
    ("PowerShell", "git -c includeIf.a=b.path=x commit -m m", None),   # the first `=`: key includeIf.a
    ("PowerShell", "git -c user.name=core.hooksPath=x commit -m m", None),
    ("Bash", "git -c user.name=$me commit -m m", None),
    ("PowerShell", "git -c \"user.name=$me\" commit -m m", None),
    ("PowerShell", "git --config-env=user.name=V commit -m m", None),
    ("PowerShell", "git --config-env user.name=V commit -m m", None),
    ("PowerShell", "git commit -c core.hooksPath=x", None),   # commit's own -c <commit>
    ("PowerShell", "git config core.hooksPath .husky", None),
    ("Bash", "git config core.hooksPath .husky", None),
    ("Bash", "echo git -c core.hooksPath=x commit", None),
    ("PowerShell", "Write-Output 'git -c core.hooksPath=x commit'", None),
    ("Bash", "cat > n.md <<EOF\ngit -c core.hooksPath=x commit\nGIT_CONFIG_PARAMETERS=x git commit\nEOF", None),
    ("PowerShell", "@'\n$env:GIT_CONFIG_PARAMETERS='x'\n'@ | Set-Content n.md; git commit -m m", None),
    ("PowerShell", "rg -n core.hooksPath docs", None),
    ("PowerShell", "git -c core.hooksPath=x commit -m m # shell-guard: allow", None),
    # git-no-verify: a backtick body is read like a $(); a body that does not lex is no finding, the rest still is
    ("Bash", "x=`npm test | tee run.log | head -40`", "gate-truncated-pipeline"),
    ("Bash", "echo `it's`", None),
    ("Bash", "echo `it's`; git commit --no-verify -m m", "git-no-verify"),
    ("Bash", "echo \"`it's`\"; git commit --no-verify -m m", "git-no-verify"),
    ("Bash", "echo `git commit --no-verify -m m", "bash-unterminated-quote"),
    ("Bash", "echo \"`git commit --no-verify -m m\"", "bash-unterminated-quote"),
    ("PowerShell", "echo `git commit --no-verify -m m`", None),
    ("PowerShell", "echo \"`git commit --no-verify -m m\"", None),
    # a heredoc delimiter with its quote open, or with no word at all, ends nothing
    ("Bash", "cat <<'EOF\nbody", "bash-unterminated-quote"),
    ("Bash", "cat <<\"EOF\nbody", "bash-unterminated-quote"),
    ("Bash", "cat <<E`OF\nbody", "bash-unterminated-quote"),
    ("Bash", "echo a <<\ngit commit --no-verify -m m", "git-no-verify"),
    # git-no-verify: the same override through the environment, set in the same command; names in any case
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git commit -m m", "git-no-verify"),
    ("Bash", "git_config_count=1 git_config_key_0=core.hooksPath git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=Core.HOOKSPATH git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=include.path git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=includeIf.onbranch:main.path git push", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_KEY_1=core.hooksPath git merge side", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=01 GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=+1 GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=' 1' GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=$n GIT_CONFIG_KEY_0=user.name git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=$k git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=$'core.hooks\\x50ath' git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=\"$(cat k)\" git commit -m m", "git-no-verify"),
    ("Bash", "k=core.hooksPath; GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=$k git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/x'\" git commit -m m", "git-no-verify"),
    ("Bash", "git_config_parameters=\"'core.hooksPath=/x'\" git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=\"'user.name=x'\" git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=$v git commit -m m", "git-no-verify"),
    ("Bash", "git_config_parameters=$v git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS+=$v git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=\"$(cat f)\" git push origin main", "git-no-verify"),
    # a count too long to be one is unreadable, however it would convert
    ("Bash", "GIT_CONFIG_COUNT=12345678901 GIT_CONFIG_KEY_0=user.name git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1" + "0" * 4300 + " git commit -m a; git commit -m b", "git-no-verify"),
    ("Bash", "GIT_CONFIG_COUNT=1234567890 GIT_CONFIG_KEY_0=user.name git commit -m m", None),
    # a bare `export` before the assignment counts; the name in any case
    ("Bash", "export GIT_CONFIG_PARAMETERS; GIT_CONFIG_PARAMETERS=x; git commit -m m", "git-no-verify"),
    ("Bash", "declare -x GIT_CONFIG_PARAMETERS; GIT_CONFIG_PARAMETERS=x; git commit -m m", "git-no-verify"),
    ("Bash", "export GIT_CONFIG_COUNT GIT_CONFIG_KEY_0; GIT_CONFIG_COUNT=1; GIT_CONFIG_KEY_0=core.hooksPath; git commit -m m",
     "git-no-verify"),
    ("Bash", "export GIT_CONFIG_PARAMETERS=x; export GIT_CONFIG_PARAMETERS; git commit -m m", "git-no-verify"),
    ("Bash", "git_config_parameters=x; export git_config_parameters; git commit -m m", "git-no-verify"),
    ("Bash", "export GIT_CONFIG_PARAMETERS; git commit -m m", None),
    ("Bash", "GIT_CONFIG_PARAMETERS=x; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS='x'; $env:GIT_CONFIG_PARAMETERS=$NULL; git commit -m m", None),
    # the flag held in a literal variable: an unquoted Bash one is split, a quoted one and PowerShell's stay one word
    ("Bash", "NV=--no-verify; git commit $NV -m m", "git-no-verify"),
    ("Bash", "NV=--no-verify; git commit \"$NV\" -m m", "git-no-verify"),
    ("Bash", "NV=--no-verify; git commit ${NV} -m m", "git-no-verify"),
    ("Bash", "NV=-n; git commit -q $NV -m m", "git-no-verify"),
    ("Bash", "NV=--no-veri; git push $NV origin main", "git-no-verify"),
    ("Bash", "OPTS=\"--no-verify -q\"; git commit $OPTS -m m", "git-no-verify"),
    ("Bash", "OPTS='-q --no-verify'; git commit $OPTS -m m", "git-no-verify"),
    ("Bash", "m='a --no-verify'; git commit -m $m", "git-no-verify"),
    ("Bash", "C=commit; git $C --no-verify -m m", "git-no-verify"),
    ("PowerShell", "$f='--no-verify'; git commit $f -m m", "git-no-verify"),
    ("PowerShell", "$f=\"--no-verify\"; git commit $f -m m", "git-no-verify"),
    ("PowerShell", "$f = '--no-veri'; git push $f origin main", "git-no-verify"),
    ("Bash", "m='a --no-verify b'; git commit -m \"$m\"", None),
    ("Bash", "m=--no-verify; git commit -m \"$m\"", None),
    ("Bash", "m=--no-verify; git commit -m $m", None),
    ("Bash", "OPTS=\"--no-verify -q\"; git commit \"$OPTS\" -m m", None),
    ("Bash", "V=--verify; git commit --no-verify $V -m m", None),
    ("Bash", "E=; git commit -m $E --no-verify", None),
    ("Bash", "x=-q; git commit $x -m m", None),
    ("Bash", "git commit $unknown -m m", None),
    ("PowerShell", "$m='a --no-verify b'; git commit -m $m", None),
    ("PowerShell", "$o='--no-verify -q'; git commit $o -m m", None),
    # a value the rule cannot read is unknown, never empty: the right side goes on past what a literal can hold
    ("Bash", "k=$(cat k.txt); git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k=`cat k.txt`; git -c $k=/nonexistent commit -m m", "git-no-verify"),
    ("Bash", "k=\"a$b\"; git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k=a$b; git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k=a'b'; git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k='a'$b; git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k=(core.hooksPath); git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "k=user.name; k=$(cat k.txt); git -c \"$k=/nonexistent\" commit -m m", "git-no-verify"),
    ("Bash", "v=\"$(cat v.txt)\"; GIT_CONFIG_PARAMETERS=$v git commit -m m", "git-no-verify"),
    ("Bash", "v=`cat v.txt`; GIT_CONFIG_PARAMETERS=$v git commit -m m", "git-no-verify"),
    ("Bash", "n=$(cat n.txt); GIT_CONFIG_COUNT=$n GIT_CONFIG_KEY_0=user.name git commit -m m", "git-no-verify"),
    ("Bash", "k=$(cat k.txt); GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=$k git commit -m m", "git-no-verify"),
    ("Bash", "k=$(cat k.txt); export GIT_CONFIG_PARAMETERS=$k; git commit -m m", "git-no-verify"),
    # ... and what it can read stays read
    ("Bash", "x=$(date); git -c \"user.name=$x\" commit -m m", None),
    ("Bash", "k=user.name; git -c \"$k=x\" commit -m m", None),
    ("Bash", "k='user.name'; git -c \"$k=x\" commit -m m", None),
    ("Bash", "k=\"user.name\"; git -c \"$k=x\" commit -m m", None),
    ("Bash", "(k=user.name); git -c \"$k=x\" commit -m m", None),
    ("Bash", "v=; GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=''; GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=\"\"; GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=>f; GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=<f; GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v= GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=\tGIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=\nGIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=\r\nGIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=&& GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "v=|| GIT_CONFIG_PARAMETERS=$v git commit -m m", None),
    ("Bash", "export GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/x'\"; git commit -m m", "git-no-verify"),
    ("Bash", "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath; git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=x; export GIT_CONFIG_PARAMETERS; git commit -m m", "git-no-verify"),
    ("Bash", "export GIT_CONFIG_COUNT=0 GIT_CONFIG_KEY_0=core.hooksPath; GIT_CONFIG_COUNT=1; git commit -m m",
     "git-no-verify"),
    ("Bash", "declare -x GIT_CONFIG_PARAMETERS=x; git commit -m m", "git-no-verify"),
    ("Bash", "typeset -rx GIT_CONFIG_PARAMETERS=x; git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS+=x git commit -m m", "git-no-verify"),
    ("Bash", "env GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "ENV GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("PowerShell", "ENV GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", "git-no-verify"),
    ("Bash", "/usr/bin/env GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env.exe GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env -- GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env -i GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env -u X GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env -uX GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env --unset X GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env --unset=X GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env -C . GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env --chdir . GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env --chdir=. GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "env \"GIT_CONFIG_PARAMETERS=x\" git commit -m m", "git-no-verify"),
    ("Bash", "env GIT_CONFIG_PARAMETERS=$v git commit -m m", "git-no-verify"),
    ("Bash", "A=1 env GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("Bash", "GIT_CONFIG_PARAMETERS=x env git commit -m m", "git-no-verify"),
    ("Bash", "env git commit --no-verify -m m", "git-no-verify"),
    ("Bash", "env git -c core.hooksPath=x commit -m m", "git-no-verify"),
    ("Bash", "env A=1 git push --no-verify", "git-no-verify"),
    ("PowerShell", "& 'C:\\Program Files\\Git\\usr\\bin\\env.exe' GIT_CONFIG_PARAMETERS=x git commit -m m", "git-no-verify"),
    ("PowerShell", "env git commit --no-verify -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0='core.hooksPath'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT = '1'; $env:GIT_CONFIG_KEY_0 = 'core.hooksPath'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT = 1; $env:GIT_CONFIG_KEY_0 = \"core.hooksPath\"; git commit -m m", "git-no-verify"),
    ("PowerShell", "$ENV:git_config_count='1'; $Env:Git_Config_Key_0='core.hooksPath'; git commit -m m", "git-no-verify"),
    ("PowerShell", "${env:GIT_CONFIG_COUNT}='1'; ${env:GIT_CONFIG_KEY_0}='core.hooksPath'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT='1'\n$env:GIT_CONFIG_KEY_0='core.hooksPath'\ngit commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/x'\"; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS=\"'core.hooksPath=/x'\"; & git push origin main", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS=$v; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS=(Get-Content f); git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS=\"$v\"; git rebase main", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS='a' + 'b'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS += 'x'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS ??= 'x'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS += ''; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT=$n; $env:GIT_CONFIG_KEY_0='user.name'; git commit -m m", "git-no-verify"),
    ("PowerShell", "$env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0=$k; git commit -m m", "git-no-verify"),
    ("PowerShell", "$k='core.hooksPath'; $env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0=$k; git commit -m m", "git-no-verify"),
    # ... allowed: harmless keys read as git reads them, nothing git would read, not exported, or no protected git
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=+1 GIT_CONFIG_KEY_0=user.name git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=' 1' GIT_CONFIG_KEY_0=user.name git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_KEY_1=core.pager git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_KEY_1=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=0 GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT= GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=1 git commit -m m", None),   # git stops at the missing key
    ("Bash", "GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_1=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_00=core.hooksPath git commit -m m", None),
    ("Bash", "GIT_CONFIG_PARAMETERS= git commit -m m", None),
    ("Bash", "env GIT_CONFIG_PARAMETERS= git commit -m m", None),
    ("Bash", "GIT_CONFIG_PARAMETERS=x; git commit -m m", None),
    ("Bash", "declare GIT_CONFIG_PARAMETERS=x; git commit -m m", None),
    ("Bash", "export OTHER=1; git commit -m m", None),
    ("Bash", "export OTHER; git commit -m m", None),
    ("Bash", "GIT_CONFIG_PARAMETERS=x git log", None),
    ("Bash", "git commit -m m; export GIT_CONFIG_PARAMETERS=x", None),
    ("Bash", "FOO=1 git commit -m m", None),
    ("Bash", "env FOO=1 git commit -m m", None),
    ("Bash", "env git status", None),
    ("Bash", "env", None),
    ("Bash", "env FOO=1", None),
    ("Bash", "environment git commit --no-verify", None),
    ("PowerShell", "$env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0='user.name'; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_COUNT = 1; $env:GIT_CONFIG_KEY_0='user.name'; git commit -m m", None),
    ("PowerShell", "$k='user.name'; $env:GIT_CONFIG_COUNT='1'; $env:GIT_CONFIG_KEY_0=$k; git commit -m m", None),
    ("PowerShell", "env", None),
    ("PowerShell", "env FOO=1", None),
    ("PowerShell", "$env:GIT_CONFIG_COUNT='0'; $env:GIT_CONFIG_KEY_0='core.hooksPath'; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS='x'; $env:GIT_CONFIG_PARAMETERS=$null; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS='x'; $env:GIT_CONFIG_PARAMETERS=''; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS='x'; git status", None),
    ("PowerShell", "git commit -m m; $env:GIT_CONFIG_PARAMETERS='x'", None),
    ("PowerShell", "$env:FOO='1'; git commit -m m", None),
    ("PowerShell", "$env:GIT_CONFIG_PARAMETERS -eq 'x'; git commit -m m", None),
    # a leading assignment does not hide the command that runs
    ("PowerShell", "$out = git commit --no-verify -m x 2>&1", "git-no-verify"),
    ("PowerShell", "$r = git push --no-verify", "git-no-verify"),
    ("PowerShell", "$null = git commit -nm x", "git-no-verify"),
    ("PowerShell", "[string]$o = & git commit -nm x", "git-no-verify"),
    ("PowerShell", "$log += git commit -nm x", "git-no-verify"),
    ("PowerShell", "$r = bash -c 'ls'", "ps-bare-bash"),
    ("PowerShell", "$v = git rev-parse HEAD", None),
    ("PowerShell", "$c = Get-Content a.txt", None),
    # git restore: every spelling of the flags git 2.53 accepted in the probe (`--s` is refused as ambiguous)
    ("PowerShell", "git restore --stage a.txt", None),
    ("PowerShell", "git restore --st a.txt", None),
    ("PowerShell", "git restore -S -- a.txt", None),
    ("PowerShell", "git restore -S --wor a.txt", "git-restore-discards"),
    ("PowerShell", "git restore -S --w a.txt", "git-restore-discards"),
    ("PowerShell", "git restore -SW a.txt", "git-restore-discards"),
    ("PowerShell", "git restore -WS a.txt", "git-restore-discards"),
    ("PowerShell", "git restore --staged --work a.txt", "git-restore-discards"),
    ("PowerShell", "git restore -sHEAD a.txt", "git-restore-discards"),
    ("PowerShell", "git restore -sSTABLE a.txt", "git-restore-discards"),   # the revision's S is no flag
    ("PowerShell", "git restore -S -- a.txt -W", None),   # after `--` only pathspecs follow
    # a backtick continues the line, with LF and with CRLF
    ("PowerShell", "git commit `\n  --no-verify -m x", "git-no-verify"),
    ("PowerShell", "git commit `\r\n  --no-verify -m x", "git-no-verify"),
    # an apostrophe in a typographic string: PowerShell runs it, so every rule still reads the command
    ("PowerShell", "Write-Host “don't”; git commit --no-verify -m x", "git-no-verify"),
    ("PowerShell", "Write-Host “don't”; bash t.sh", "ps-bare-bash"),
    ("PowerShell", "Write-Host ‘say \"hi’; npm test | tail -5", "ps-unix-command"),
    ("PowerShell", "Write-Host “don't”", None),
    # ... every quote PowerShell accepts opens and closes a string (probe: Parser::ParseInput on pwsh 7.6)
    ("PowerShell", "Write-Host „don't”; git commit -nm x", "git-no-verify"),
    ("PowerShell", "Write-Host ‚don\"t’; git commit -nm x", "git-no-verify"),
    ("PowerShell", "Write-Host ‛don\"t’; git commit -nm x", "git-no-verify"),
    ("PowerShell", "Write-Host ‘don\"t‛; git commit -nm x", "git-no-verify"),
    # ... the rules that read the command's text read it the same way
    ("PowerShell", "Write-Host “don't”\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Host „don't”; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Host “don't”\n" + READ_RELATIVE, "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.File]::ReadAllText(‘tools\\x.php’)", "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.File]::WriteAllText('X:\\tmp\\a.txt', “don't $report”)", "stale-write-var"),
    ("PowerShell", "Write-Host “don't”\n[IO.File]::WriteAllText('X:\\tmp\\a.txt', 'literal')", None),
    ("PowerShell", "Write-Host “don't”\n[IO.File]::ReadAllText('X:\\tmp\\a.txt')", None),
    # ... a straight quote that never closes stays a command PowerShell rejects, nothing to judge
    ("PowerShell", "git commit --no-verify -m 'oops", None),
    ("PowerShell", "Write-Host “ok”; git commit --no-verify -m 'oops", None),
    # Narrow rules: a gate truncated inside its own pipeline
    ("PowerShell", "php vendor/bin/phpunit | Tee-Object run.log | Select-Object -First 30",
     "gate-truncated-pipeline"),
    ("PowerShell", "pwsh -File safe-edit.ps1 -Run | Select-Object -First 12", "gate-truncated-pipeline"),
    ("PowerShell", "vendor\\bin\\phpunit --filter Foo | Select-Object -Last 5", None),
    ("PowerShell", "vendor\\bin\\pest --compact tests\\Feature 2>&1 | Select-Object -Last 8", None),
    ("Bash", "npm test | tee run.log | head -40", "gate-truncated-pipeline"),
    ("PowerShell", "$out = npm test 2>&1; $out | Select-Object -First 30", None),
    ("PowerShell", "npm test 2>&1 | Select-Object -Last 20", None),
    ("PowerShell", "node --test tests/x.test.js 2>&1 | Select-Object -Last 1", None),
    ("Bash", "composer validate | head -5", None),
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log", None),
    ("PowerShell", "Get-Content run.log | Select-Object -First 30", None),
    ("PowerShell", "git log --oneline | Select-Object -First 5", None),
    ("Bash", "git log --oneline | head -5", None),
    ("Bash", "npm test > run.log 2>&1", None),
    # Narrow rules: prose given inline to a CLI
    ("PowerShell", "gh pr create --base develop --title 'chore: x' --body \"Zmiana \u201ex\u201d gotowa\"",
     "cli-inline-prose"),
    # a single-quoted string ends only at a single-family quote: the typographic double ones pass
    ("PowerShell", "gh pr create --base develop --title 'chore: x' --body 'Fix \u201ethe\u201d thing'", None),
    # bash keeps an apostrophe inside a double-quoted body as text; the rule is PowerShell-only
    ("Bash", "gh issue create --title x --body \"it's broken\"", None),
    ("PowerShell", "gh pr create --title 'chore: x' --body-file X:\\tmp\\body.txt", None),
    ("PowerShell", "gh pr create --title 'chore: x' --body 'short plain body'", None),
    ("Bash", "gh pr list --json number", None),
    ("PowerShell", "gh pr create --body \"it's fine\" # shell-guard: allow", None),
    # Narrow rules: a whole file rewritten from a variable this command never assigns
    ("PowerShell", "[IO.File]::WriteAllText($p, $t + $add, [Text.UTF8Encoding]::new($false))",
     "stale-write-var"),
    ("PowerShell", "Set-Content -Path report.md -Value $text", "stale-write-var"),
    ("PowerShell", "$t = Get-Content -Raw report.md; [IO.File]::WriteAllText($p, $t + \"x\")", None),
    ("PowerShell", "Add-Content -Path report.md -Value 'one line'", None),
    ("PowerShell", "Set-Content -Path report.md -Value 'literal'", None),
    ("PowerShell", "[IO.File]::WriteAllText($p, 'literal')", None),
    ("PowerShell", "Get-ChildItem | Set-Content -Value $_ out.txt", None),
    # a comment is blanked: its apostrophe opens no string, its text is no call
    ("PowerShell", "# don't touch it\n[IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)", "stale-write-var"),
    ("PowerShell", "<# don't touch it #>\n[IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)", "stale-write-var"),
    ("PowerShell", "Get-Date # don't\n[IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)", "stale-write-var"),
    ("PowerShell", "Get-Date;# don't\n[IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)", "stale-write-var"),
    ("PowerShell", "# it's relative\n$t = [IO.File]::ReadAllText('tools\\x.php'); $t", "ps-dotnet-relative-path"),
    ("PowerShell", "# old: [IO.File]::WriteAllText($p, $oldText)\nGet-Date", None),
    ("PowerShell", "# old: [IO.File]::ReadAllText('x.txt')\nGet-Date", None),
    ("PowerShell", "Get-Date # old: [IO.File]::ReadAllText('x.txt')", None),
    ("PowerShell", "<#\n[IO.File]::ReadAllText('x.txt')\n#>\nGet-Date", None),
    ("PowerShell", "<# [IO.File]::WriteAllText($p, $oldText) #> Get-Date", None),
    # ... a `#` inside a word or a string starts no comment
    ("PowerShell", "$a = 'x#y'; [IO.File]::ReadAllText('tools\\x.php')", "ps-dotnet-relative-path"),
    ("PowerShell", "Write-Output a#b; [IO.File]::WriteAllText('X:\\tmp\\a.txt', $report)", "stale-write-var"),
    # ... a `<#` inside a word, a `#` after a backtick or inside ${...}, what a CR ends: tokens read in pwsh 7.6
    ("PowerShell", "Write-Output a<#b; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output a<#b; " + READ_RELATIVE, "ps-dotnet-relative-path"),
    ("PowerShell", "Write-Output a<#b; git commit -nm x", "git-no-verify"),
    ("PowerShell", "# note\r" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "# note\r" + READ_RELATIVE, "ps-dotnet-relative-path"),
    ("PowerShell", "# note\rgit commit -nm x", "git-no-verify"),
    ("PowerShell", "Write-Output a` #b; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output `#b; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output ${#x}; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output ${a`} #b}; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output a`'b; " + WRITE_REPORT, "stale-write-var"),
    # ... an escaped backtick escapes nothing, a line continuation leaves a word as it was
    ("PowerShell", "Write-Output a`` # don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Get-Date `\n# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output a`\n#b; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Get-Date `\r\n# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output a`\r\n#b; " + WRITE_REPORT, None),   # inside a word it escapes the CR alone
    # ... the boundaries a comment can follow, one case each; a comment glued to a block comment
    ("PowerShell", "Get-Date|# don't\nOut-Null\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Start-Sleep 1&# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "(# don't\nGet-Date)\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "(Get-Date)# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "if (1) {# don't\n}\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "if (1) {}# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "<# a #># don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output 'a'#b; " + READ_RELATIVE, None),   # a closed string ends a word: pwsh reads a comment
    ("PowerShell", "Write-Output a'b'#c; " + WRITE_REPORT, "stale-write-var"),   # one word, quotes and all
    # ... a comment stays a comment: its text is no call
    ("PowerShell", "Get-Date <# [IO.File]::ReadAllText('x.txt') #>", None),
    ("PowerShell", "Write-Output a`` # [IO.File]::ReadAllText('x.txt')", None),
    ("PowerShell", "# old: [IO.File]::ReadAllText('x.txt')\rGet-Date", None),
    ("PowerShell", "Get-Date|# [IO.File]::ReadAllText('x.txt')\nOut-Null", None),
    # ... and a `#` or `<#` inside a string or here-string starts nothing
    ("PowerShell", "$a = ' # x'; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "$a = \" <# x\"; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "@'\n# x\n'@\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "@\"\n<# x\n\"@\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Host 'a#b' # don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "$u = 'http://x/#a'; " + READ_RELATIVE, "ps-dotnet-relative-path"),
    ("PowerShell", "<# a #> " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "<# don't #> " + READ_RELATIVE, "ps-dotnet-relative-path"),
    # ... a tab and a CR are word boundaries; `${a}` leaves the argument open (tokens read in pwsh 7.6)
    ("PowerShell", "Get-Date\t# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Get-Date\r# don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "Write-Output ${a}#b; " + READ_RELATIVE, "ps-dotnet-relative-path"),
    # ... a block comment glued to a value: its `#>` opens no line comment, the next statement runs
    ("PowerShell", "$a = 1<# why #>; git commit -nm x", "git-no-verify"),
    ("PowerShell", "$a = $b<# why #>; git commit -nm x", "git-no-verify"),
    ("PowerShell", "$a = 1<# why #>; " + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "$a = 1<# why #>; " + READ_RELATIVE, "ps-dotnet-relative-path"),
    # ... with no `<#` open a `#>` is a line comment like any other
    ("PowerShell", "Get-Date #> x; git commit -nm x", None),
    ("PowerShell", "#>'\rgit commit -nm x", "git-no-verify"),
    ("PowerShell", "Get-Date #> don't\n" + WRITE_REPORT, "stale-write-var"),
    ("PowerShell", "$a = 1<# x #>; Get-Date #> don't\n" + WRITE_REPORT, "stale-write-var"),
    # Two typographic apostrophes do not pair up across the command: each sits in its own string
    ("PowerShell", "Write-Host “don't”; git commit -nm x; Write-Host “won't”", "git-no-verify"),
    ("PowerShell", "Write-Host “don't”; " + READ_RELATIVE + "; Write-Host “won't”",
     "ps-dotnet-relative-path"),
    ("PowerShell", "Write-Host “don't”; git worktree remove --force $w; Write-Host “won't”",
     "git-worktree-junction"),
    # ... and one in a comment the lexer takes for code opens no string
    ("PowerShell", "$a=#’\rgit commit -nm x", "git-no-verify"),
    # Bash: a launcher path that resolves to nothing is no .bat; Git Bash runs only what it finds
    ("Bash", shell_guard._msys(os.path.join(FAKE_HERD, "php84", "php")) + " -r 'a|b'", None),
    ("Bash", "~/bin/php -r 'a|b'", None),
    ("Bash", "./composer require 'a|b'", None),
    ("Bash", "cd ex && ./php -r 'a|b'", None),
]

SE = "~/.claude/tools/safe-edit/safe-edit.ps1"
SE_WIN = "D:\\tools\\safe-edit\\safe-edit.ps1"
SE_SPACE = SE_WIN.replace("tools", "Jan Kowalski")
# What the installed narrow hook (`--only-narrow`, check_narrow) decides; one case per rule branch.
NARROW_CASES = [
    # gate-truncated-pipeline: the red-run driver by every path form, and through an interpreter
    ("PowerShell", SE + " -Run 'npm test' 2>&1 | Select-Object -First 5", "gate-truncated-pipeline"),
    ("PowerShell", SE_WIN + " -Run 'npm test' | Select-Object -First 5", "gate-truncated-pipeline"),
    ("PowerShell", "& \"" + SE_WIN + "\" -Run 'npm test' | Select-Object -First 5", "gate-truncated-pipeline"),
    ("Bash", SE + " -Run 'npm test' 2>&1 | head -5", "gate-truncated-pipeline"),
    ("PowerShell", "pwsh -NoProfile -File \"$HOME\\.claude\\tools\\safe-edit\\safe-edit.ps1\" -Run 'npm test' "
     "| Select-Object -First 5", "gate-truncated-pipeline"),
    ("PowerShell", "pwsh -NoProfile -Command \"& '" + SE + "' -Run 'npm test'\" | Select-Object -First 5",
     "gate-truncated-pipeline"),
    ("PowerShell", "pwsh -ExecutionPolicy Bypass " + SE + " -Run 'npm test' | Select-Object -First 5",
     "gate-truncated-pipeline"),
    ("Bash", "bash -c '" + SE + " -Run x' | head -5", "gate-truncated-pipeline"),
    # ... -First in every spelling, head in PowerShell, and a script-block stage after the tee
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log | Select-Object -First:30", "gate-truncated-pipeline"),
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log | select -f 30", "gate-truncated-pipeline"),
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log | head -40", "gate-truncated-pipeline"),
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log | ForEach-Object { $_ } | Select-Object -First 5",
     "gate-truncated-pipeline"),
    ("PowerShell", "$r = & '" + SE + "' -Run 'npm test' | Select-Object -First 5", "gate-truncated-pipeline"),
    # ... a space in the path, in each way the driver is started
    ("PowerShell", "& '" + SE_SPACE + "' -Path f -Find a -Replace b -Run x -ExpectRan y | Select-Object -First 3",
     "gate-truncated-pipeline"),
    ("PowerShell", "& \"" + SE_SPACE + "\" -Run x | Select-Object -First 3", "gate-truncated-pipeline"),
    ("PowerShell", "pwsh -NoProfile -File \"" + SE_SPACE + "\" -Run x | Select-Object -First 3",
     "gate-truncated-pipeline"),
    ("PowerShell", "pwsh '" + SE_SPACE + "' -Run x | Select-Object -First 3", "gate-truncated-pipeline"),
    ("Bash", "'/d/Jan Kowalski/safe-edit.ps1' -Run x | head -3", "gate-truncated-pipeline"),
    # ... a quoted word that only ends in the name is still not the driver when it is an argument
    ("PowerShell", "rg 'a b/safe-edit.ps1' docs | Select-Object -First 3", None),
    ("PowerShell", "& '" + SE_SPACE + "' -Run x | Select-Object -Last 3", None),
    # ... allowed: -Last reads the whole stream; "safe-edit" as a plain argument is not the gate
    ("PowerShell", SE + " -Run 'npm test' 2>&1 | Select-Object -Last 5", None),
    ("PowerShell", "npm test 2>&1 | Tee-Object run.log | Select-Object -Last 5", None),
    ("PowerShell", "rg -n safe-edit docs | Select-Object -First 5", None),
    ("PowerShell", "Get-Content safe-edit.ps1 | Select-Object -First 20", None),
    ("Bash", "git log -- tools/safe-edit | head -5", None),
    ("PowerShell", "pwsh -NoProfile -File X:\\tmp\\scan.ps1 -Name safe-edit | Select-Object -First 5", None),
    ("PowerShell", "pwsh -NoProfile -Command \"rg -n safe-edit docs\" | Select-Object -First 5", None),
    ("PowerShell", "pwsh -NoProfile -ExecutionPolicy Bypass X:\\tmp\\scan.ps1 safe-edit | Select-Object -First 5",
     None),
    # bash-backslash-collapse: Git Bash turns every `\\` into `\`, whatever bash's quoting around it
    ("Bash", "printf '%s' 'a\\\\b'", "bash-backslash-collapse"),
    ("Bash", "python - <<'EOF'\nprint('a\\\\nb')\nEOF", "bash-backslash-collapse"),
    ("Bash", "echo \"a\\\\$x\"", "bash-backslash-collapse"),
    ("Bash", "echo \"a\\\\b\"", "bash-backslash-collapse"),   # same value either way: the D1 trade-off
    ("Bash", "echo a\\\\b", "bash-backslash-collapse"),
    ("Bash", "sed -i 's/_i/_i\\\\d+/g' rulediff.cjs", "bash-backslash-collapse"),
    ("Bash", "python -c 'print(\"a\\\\nb\")'", "bash-backslash-collapse"),
    # ... allowed: one backslash passes unchanged, and the marker
    ("Bash", "grep -n 'a\\d' f.txt", None),
    ("Bash", "printf 'a\\nb'", None),
    ("Bash", "echo 'a\\\\b' # shell-guard: allow", None),
    # cli-inline-prose: PowerShell only; a typographic quote of the family that opened the string.
    # Every deny below was checked in pwsh 7.6 (gh gets the text torn or without its quotes).
    ("PowerShell", "gh release create v1.2.0 -n \"Fix \u201ethe\u201d thing\"", "cli-inline-prose"),
    ("PowerShell", "gh release create v1.2.0 --notes \"Fix \u201ethe\u201d thing\"", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title 'fix: x' -b\"Fix \u201ethe\u201d thing\"", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title 'fix: x' --body=\"Fix \u201ethe\u201d thing\"", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title \"Fix \u201ex\u201d\" --body-file b.txt", "cli-inline-prose"),
    ("PowerShell", "gh issue create -t \"Fix \u201ex\u201d\" --body-file b.txt", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title 'Fix \u2018x\u2019 now' --body-file b.txt", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title x --body \"say `\"hi \u201ex\u201d now\"", "cli-inline-prose"),
    ("PowerShell", "gh pr create --title 'Fix \u201bx\u201b now' --body-file b.txt", "cli-inline-prose"),
    ("PowerShell", "Write-Host \u201cdon't\u201d; gh pr create --title \"Fix \u201ex\u201d\" --body-file b.txt",
     "cli-inline-prose"),
    # ... allowed: the other family, straight quotes, newlines, here-strings arrive intact
    ("PowerShell", "gh release create v1.2.0 -n 'Fix \u201ethe\u201d thing'", None),
    ("PowerShell", "gh release create v1.2.0 --notes 'Fix \u201ethe\u201d thing'", None),
    ("PowerShell", "gh pr create --title x --body \"it\u2019s \u2018done\u2019\"", None),
    ("PowerShell", "gh pr create --title 'say \"hi\" now' --body-file b.txt", None),
    ("PowerShell", "gh pr create --title x --body 'line1\nline2 \"q\"'", None),
    ("PowerShell", "gh pr create --title x --body \"line1`nline2\"", None),
    ("PowerShell", "gh pr create --title x --body @\"\nZmiana \u201ex\u201d gotowa\n\"@", None),
    ("PowerShell", "gh pr create --title 'fix: x' -b\"plain body\"", None),
    ("Bash", "gh pr create --title 'fix: x' --body \"Fix \u201ethe\u201d thing, it's done\"", None),
    # stale-write-var: every way a never-assigned variable reaches the file
    ("PowerShell", "$t | Set-Content report.md", "stale-write-var"),
    ("PowerShell", "$text | Out-File report.md", "stale-write-var"),
    ("PowerShell", "Out-File -FilePath report.md -InputObject $text", "stale-write-var"),
    ("PowerShell", "Set-Content report.md $text", "stale-write-var"),
    ("PowerShell", "Set-Content -Path report.md $text", "stale-write-var"),
    ("PowerShell", "Set-Content report.md -NoNewline $text", "stale-write-var"),
    ("PowerShell", "Set-Content -Path report.md -Value \"$text\"", "stale-write-var"),
    ("PowerShell", "Set-Content -Path report.md -Value ($text + 'x')", "stale-write-var"),
    ("PowerShell", "Set-Content -Path report.md -Value:$text", "stale-write-var"),
    ("PowerShell", "$t | Out-File report.md -Append:$false", "stale-write-var"),
    ("PowerShell", "$m = \"say `\"hi\"; [IO.File]::WriteAllText($p, $t)", "stale-write-var"),
    # ... allowed: bound names, automatic variables, literals, the encoding argument
    ("PowerShell", "$p = 'X:\\tmp\\a.txt'; [IO.File]::WriteAllText($p, 'x', $enc)", None),
    ("PowerShell", "Get-Content b.txt -OutVariable t | Out-Null; Set-Content report.md $t", None),
    ("PowerShell", "$a, $b = (Get-Content b.txt -Raw) -split '---'; Set-Content report.md $a", None),
    ("PowerShell", "Set-Content report.md $PSVersionTable.PSVersion", None),
    ("PowerShell", "Set-Content report.md $IsWindows", None),
    ("PowerShell", "Set-Content report.md $MyInvocation.Line", None),
    ("PowerShell", "Set-Content report.md $PSBoundParameters.Keys", None),
    ("PowerShell", "Set-Content report.md $Env:PATH", None),
    ("PowerShell", "Set-Content report.md \"$HOME $PWD $LASTEXITCODE $true $null\"", None),
    ("PowerShell", "Get-ChildItem *.md | ForEach-Object { Set-Content ($PSItem.Name + '.bak') $PSItem.FullName }",
     None),
    ("PowerShell", "Get-ChildItem *.md | ForEach-Object { Set-Content ($_.Name + '.bak') $_.FullName }", None),
    ("PowerShell", "function Save { Set-Content report.md $args[0]; $input | Out-File log.txt }", None),
    ("PowerShell", "Set-Content report.md 'it''s $x'", None),
    ("PowerShell", "Set-Content report.md @'\nit's $x\n'@", None),
    ("PowerShell", "Set-Content report.md \"price: `$total\"", None),
    # ... allowed: compound assignment binds, appending $null changes nothing, a call named in text
    ("PowerShell", "foreach ($l in Get-Content a.txt) { $acc += $l }; Set-Content b.txt $acc", None),
    ("PowerShell", "$z ??= 'x'; Set-Content b.txt $z", None),
    ("PowerShell", "$t | Out-File report.md -Append", None),
    ("PowerShell", "git commit -m 'Use [IO.File]::WriteAllText($p, $text) for UTF-8'", None),
    ("PowerShell", "Write-Output \"next: [IO.File]::WriteAllText($p, $text)\"", None),
    ("PowerShell", "git commit -m @'\nfix: don't use [IO.File]::WriteAllLines(f, $lines)\n'@", None),
    ("PowerShell", "(Get-Content f.php -Raw) -replace '\\$this->a\\(\\$cap\\)', '$this->b($cap)' "
     "| Set-Content f.php -Encoding utf8NoBOM", None),
    # ps-bare-bash: `bash` in PowerShell is C:\Windows\System32\bash.exe = WSL (probe: uname = Linux ...WSL2)
    ("PowerShell", "bash tools/x.sh", "ps-bare-bash"),
    ("PowerShell", "cd F:\\x; bash -c 'ls'", "ps-bare-bash"),
    ("PowerShell", "npm run build && bash scripts/deploy.sh", "ps-bare-bash"),
    ("PowerShell", "Get-Content a.sh | bash", "ps-bare-bash"),
    ("PowerShell", "& bash -lc 'echo hi'", "ps-bare-bash"),
    ("PowerShell", "BASH.exe x.sh", "ps-bare-bash"),
    ("PowerShell", "Get-ChildItem *.sh | ForEach-Object { bash $_.FullName }", "ps-bare-bash"),
    ("PowerShell", "pwsh -NoProfile -File " + SE + " -Path a.sh -Find x -Replace y -Run 'bash tests/run.sh' "
     "-ExpectRan ok", "ps-bare-bash"),
    ("PowerShell", SE_WIN + " -Path a -Find x -Replace y -Run:'npm test; bash t.sh'", "ps-bare-bash"),
    ("PowerShell", "& \"" + SE_WIN + "\" -Path a -Find x -Replace y -Ru \"npm test && bash t.sh\"", "ps-bare-bash"),
    # ... allowed: Git Bash or WSL named on purpose, bash as an argument or inside a string
    ("PowerShell", "& \"C:\\Program Files\\Git\\bin\\bash.exe\" -lc 'ls'", None),
    ("PowerShell", "C:\\Windows\\System32\\bash.exe -c ls", None),
    ("PowerShell", "wsl bash -c 'uname'", None),
    ("PowerShell", "rg -n bash docs", None),
    ("PowerShell", "Get-Command bash", None),
    ("PowerShell", "ssh staging \"cd ~/x && bash deploy.sh 2>&1 | tail -40\"", None),
    ("PowerShell", "git commit -m 'run bash x.sh'", None),
    ("PowerShell", "pwsh -File " + SE + " -Path a -Find x -Replace y -Run 'npm test' -ExpectRan 'bash'", None),
    ("PowerShell", "pwsh -File X:\\tmp\\other.ps1 -Run 'bash t.sh'", None),
    # a -Run string pwsh cannot parse runs nothing, its bash included
    ("PowerShell", SE + " -Path a -Find x -Replace y -Run \"bash t.sh; echo 'oops\"", None),
    # ps-dotnet-relative-path: probe: after Set-Location X:\tmp\g1g5 a relative WriteAllText landed in the
    # process directory (the session's start folder), not in X:\tmp\g1g5
    ("PowerShell", "[IO.File]::ReadAllText('tools\\x.php')", "ps-dotnet-relative-path"),
    ("PowerShell", "$t = 'x'; [System.IO.File]::WriteAllText(\"src\\a.ts\", $t)", "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.File]::WriteAllLines(\"out\\$name.txt\", @('a'))", "ps-dotnet-relative-path"),
    ("PowerShell", "[io.file]::readalllines('~/.claude/x')", "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.Directory]::CreateDirectory('build\\tmp')", "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.Directory]::Move('X:\\tmp\\a', 'b')", "ps-dotnet-relative-path"),
    ("PowerShell", "[IO.File]::AppendAllText('logs\\run.txt', 'x')", "ps-dotnet-relative-path"),
    # ... allowed: absolute, $PWD / Join-Path / variables, a call named in text, other methods
    ("PowerShell", "[IO.File]::ReadAllText('X:\\src\\x\\a.php')", None),
    ("PowerShell", "[IO.File]::ReadAllText('X:/tmp/a.txt')", None),
    ("PowerShell", "[IO.File]::ReadAllText(\"$PWD\\a.php\")", None),
    ("PowerShell", "[IO.File]::WriteAllText((Join-Path $PWD 'a.txt'), 'x')", None),
    ("PowerShell", "$f = 'tools\\x.php'; $c = [IO.File]::ReadAllText($f)", None),
    ("PowerShell", "[IO.File]::ReadAllText(\"$env:TEMP\\a.txt\")", None),
    ("PowerShell", "[IO.File]::ReadAllText('\\\\server\\share\\a.txt')", None),
    ("PowerShell", "[IO.Directory]::GetCurrentDirectory()", None),
    ("PowerShell", "[IO.Directory]::GetFiles('D:\\x', '*.php')", None),
    ("PowerShell", "git commit -m \"never [IO.File]::ReadAllText('rel.txt')\"", None),
    # ps-native-nonewline: probe: `git show HEAD:INSTALL.md | Set-Content -NoNewline f` wrote 1 line of 123
    ("PowerShell", "git show HEAD:src/a.js | Set-Content -NoNewline src/a.js", "ps-native-nonewline"),
    ("PowerShell", "git show HEAD~1:a.txt | Set-Content a.txt -Encoding utf8 -NoNewline", "ps-native-nonewline"),
    ("PowerShell", "(git show HEAD:a.txt) | Set-Content -NoNew a.txt", "ps-native-nonewline"),
    ("PowerShell", "& 'C:\\Program Files\\Git\\cmd\\git.exe' show HEAD:a | Set-Content -NoNewline:$true a",
     "ps-native-nonewline"),
    ("PowerShell", "node gen.js 2>&1 | Set-Content -NoNewline out.txt", "ps-native-nonewline"),
    # ... allowed: cmdlet output or one joined string, no -NoNewline, a function of the same command
    ("PowerShell", "Get-Content -Raw a.txt | Set-Content -NoNewline b.txt", None),
    ("PowerShell", "(Get-Content a.txt -Raw) -replace 'x','y' | Set-Content a.txt -NoNewline", None),
    ("PowerShell", "(git show HEAD:a.txt) -join \"`n\" | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "git show HEAD:a.txt | Out-String | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "git show HEAD:a.txt | Set-Content a.txt", None),
    ("PowerShell", "git show HEAD:a.txt | Set-Content -NoNewline:$false a.txt", None),
    ("PowerShell", "function Z { 'x' }; Z | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "echo x | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "Convert-Path . | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "Compare-Object a b | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "convert-path . | Set-Content -NoNewline a.txt", None),
    ("PowerShell", "clang-format a.c | Set-Content -NoNewline a.c", "ps-native-nonewline"),
    ("PowerShell", "Frob-Thing a | Set-Content -NoNewline a.txt", "ps-native-nonewline"),
    # bat-metachar in the installed path; probes: composer.bat stored `a^1` as "a1", `x%USERNAME%y` as
    # "x<user name>y", `a&b` as "a"
    ("PowerShell", "composer require 'wordpress/mcp-adapter:^0.5'", "bat-metachar"),
    ("PowerShell", "composer require wordpress/mcp-adapter:^0.5", "bat-metachar"),
    ("PowerShell", "php84 vendor/bin/phpunit --filter 'A|B'", "bat-metachar"),
    ("PowerShell", "phpcs '--ignore=vendor|node_modules' .", "bat-metachar"),
    ("PowerShell", "wp option get %SHELL_GUARD_TEST_VAR%", "bat-metachar"),
    ("PowerShell", "wp db query \"SELECT 1 FROM t WHERE a LIKE '%shell_guard_test_var%'\"", "bat-metachar"),
    # ... allowed: no metacharacter, the exe called directly, spaces quote the argument, an undefined %name%
    ("PowerShell", "composer require 'wordpress/mcp-adapter:0.5.*'", None),
    ("PowerShell", "& \"%s\\php84\\php.exe\" \"%s\\composer.phar\" require 'a/b:^1'" % (HERD, HERD), None),
    ("PowerShell", "& \"%s\\php84\\php.exe\" \"%s\" option get 'a|b'" % (HERD, WPCLI), None),
    ("PowerShell", "wp option get 'a|b'", "bat-metachar"),
    ("PowerShell", "composer config extra.x 'a ^1 b'", None),
    ("PowerShell", "wp db query \"SELECT 1 WHERE a LIKE '%nope_g1g5_undefined%'\"", None),
    # bat-metachar with `"` in the argument: PowerShell passes it unescaped (wrapped in "..." when it holds
    # whitespace) and cmd.exe toggles quoting at each `"`; probe on a .bat echoing %*: `["a"=>1]` wrote a
    # file `1]`, `a "b|c" d` ran `c" d"`; `x"a|b"y` and `he said "hi" & left` arrived intact
    ("PowerShell", "php83 -r '[\"a\"=>1];'", "bat-metachar"),
    ("PowerShell", "wp db query 'SELECT \"a|b\" FROM t'", "bat-metachar"),
    ("PowerShell", "wp eval 'echo \"<b>\";'", "bat-metachar"),
    # PowerShell wraps only when whitespace sits outside its own quotes (probe: `"a b"|c` was not wrapped
    # and ran `c`; `x"a b"y>c9out` wrote c9out; `"a b|c"` arrived intact)
    ("PowerShell", "php83 '\"a b\"|c'", "bat-metachar"),
    ("PowerShell", "php83 'x\"a b\"y>c9out'", "bat-metachar"),
    # PowerShell does not count a `\"` as a quote when it decides to wrap; cmd.exe still toggles at it
    ("PowerShell", "php83 'a\\\" b|c'", "bat-metachar"),
    # ... allowed: the metacharacter sits inside a quoted stretch of the command line
    ("PowerShell", "php83 '\"a b|c\"'", None),
    ("PowerShell", "php84 -r 'x\"a|b\"y'", None),
    ("PowerShell", "wp eval 'echo \"he said\" & 1;'", None),
    ("PowerShell", "wp eval 'echo \"x\";'", None),
    # ps-bad-parameter in the installed path (all verified to raise on PowerShell 7.6)
    ("PowerShell", "Select-String -Path app -Pattern x -Recurse", "ps-bad-parameter"),
    ("PowerShell", "Get-ChildItem *.php | Select-String -Pattern x -C 2", "ps-bad-parameter"),
    ("PowerShell", "ls -la D:\\x", "ps-bad-parameter"),
    ("PowerShell", "Get-ChildItem -Recurse -Filter *.php | Select-String -Pattern x -Context 2,2", None),
    ("PowerShell", "rg -n -C 2 x .", None),
    # unique prefixes of real parameters run: -l = -LiteralPath, -A = -AllMatches
    ("PowerShell", "ls -l C:\\Windows\\System32\\drivers\\etc", None),
    # ... but -l with no path after it fails: "Missing an argument for parameter 'LiteralPath'"
    ("PowerShell", "ls -l", "ps-bad-parameter"),
    ("PowerShell", "ls -l | Select-Object -First 2", "ps-bad-parameter"),
    ("PowerShell", "Get-ChildItem -l -Force", "ps-bad-parameter"),
    ("PowerShell", "'ab' | Select-String b -A", None),
    ("PowerShell", "npm test 2>&1 | Select-String '# fail' -A 1", "ps-bad-parameter"),
]


def make_junction(link, target):
    import subprocess
    out = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
    assert out.returncode == 0, out


def worktree_cases(root, links):
    """Builds trees with junctions under root, recording each junction in links (the caller deletes them
    first); returns (tool, command, cwd, expected rule) cases."""
    import subprocess
    wt, target, clean, inner = (os.path.join(root, n) for n in ("wt", "target", "clean", "inner"))
    for d in (os.path.join(wt, "src"), target, clean, os.path.join(inner, "real")):
        os.makedirs(d)
    with open(os.path.join(target, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("must survive")
    links.append(os.path.join(wt, "src", "node_modules"))
    make_junction(links[-1], target)                        # points outside the tree: the damage case
    links.append(os.path.join(inner, "link"))
    make_junction(links[-1], os.path.join(inner, "real"))   # points inside the tree: deleted with it anyway
    repo = os.path.join(root, "repo")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", repo]
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(git + ["worktree", "add", "-q", os.path.join(root, "repo-wt")], check=True)
    links.append(os.path.join(root, "repo-wt", "vendor"))
    make_junction(links[-1], target)
    for side in ("a", "b"):   # two worktrees named `dup`: git refuses `worktree remove dup` as ambiguous
        subprocess.run(git + ["worktree", "add", "-q", "--detach", os.path.join(root, side, "dup")], check=True)
        links.append(os.path.join(root, side, "dup", "vendor"))
        make_junction(links[-1], target)
    msys = "/" + root[0].lower() + root[2:].replace("\\", "/")
    J, U = "would empty", "cannot tell which folder"   # the deny for a junction found / an unknown folder
    # Both candidates of one name: git takes the registered worktree by its suffix first, whatever the plain
    # folder of that name holds. In `both` the worktree holds the junction, in `rev` the plain folder does.
    both, rev = os.path.join(root, "both"), os.path.join(root, "rev")
    for base, junction_in in ((both, os.path.join("wts", "foo")), (rev, "foo")):
        subprocess.run(["git", "init", "-q", base], check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", base, "commit", "-q", "--allow-empty",
                        "-m", "init"], check=True)
        subprocess.run(["git", "-C", base, "worktree", "add", "-q", "--detach", os.path.join("wts", "foo")], check=True)
        os.makedirs(os.path.join(base, "foo"))
        links.append(os.path.join(base, junction_in, "vendor"))
        make_junction(links[-1], target)
    # The suffix typed with `\`: git reads it as `/`, and the worktree lies outside the repository, so the plain
    # folder of that name under the repository is another folder. In `bs` the worktree holds the junction.
    bs, bsrev = os.path.join(root, "bs", "main"), os.path.join(root, "bsrev", "main")
    registered = os.path.join("..", "Worktrees", "proj", "feature")
    for base, junction_in in ((bs, registered), (bsrev, os.path.join("proj", "feature"))):
        subprocess.run(["git", "init", "-q", base], check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", base, "commit", "-q", "--allow-empty",
                        "-m", "init"], check=True)
        subprocess.run(["git", "-C", base, "worktree", "add", "-q", "--detach", registered], check=True)
        os.makedirs(os.path.join(base, "proj", "feature"))
        links.append(os.path.normpath(os.path.join(base, junction_in, "vendor")))
        make_junction(links[-1], target)
    in_worktree = os.path.join("Worktrees", "proj", "feature", "vendor")
    # A registered worktree whose folder is gone: git drops the record with rc 0, nothing to empty
    prune = os.path.join(root, "prune")
    subprocess.run(["git", "init", "-q", prune], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", prune, "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True)
    subprocess.run(["git", "-C", prune, "worktree", "add", "-q", "--detach", os.path.join("wts", "gone")], check=True)
    shutil.rmtree(os.path.join(prune, "wts", "gone"))
    # Two names that differ only in a non-ASCII letter's case: git tells them apart (probe: `ÜNïx` removed
    # only a\Ünïx, `ÜNÏX` matched nothing). The worktree with the capital holds the junction.
    fold = os.path.join(root, "fold")
    subprocess.run(["git", "init", "-q", fold], check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", fold, "commit", "-q", "--allow-empty",
                    "-m", "init"], check=True)
    capital, small = "Ünïx", "ünïx"
    for side, name in (("a", capital), ("b", small)):
        subprocess.run(["git", "-C", fold, "worktree", "add", "-q", "--detach", os.path.join(side, name)], check=True)
    links.append(os.path.join(fold, "a", capital, "vendor"))
    make_junction(links[-1], target)
    return [
        ("PowerShell", "git worktree remove --force " + q(wt), None, J),
        ("PowerShell", "git worktree remove " + q(wt), None, J),   # git 2.53 followed it without --force too
        ("PowerShell", "git worktree remove -f -f %s 2>&1 | Select-Object -Last 1" % q(wt), None, J),
        ("PowerShell", "Set-Location " + q(root) + "; git worktree remove wt", None, J),
        ("PowerShell", "Push-Location " + q(os.path.join(root, "clean")) + "; Set-Location ..; "
         "git worktree remove --force wt; Pop-Location", None, J),
        ("PowerShell", "$w = " + q(wt) + "; git worktree remove --force $w", None, J),
        ("PowerShell", "git -C " + q(root) + " worktree remove -f wt", None, J),
        ("PowerShell", "git worktree remove wt", root, J),
        ("PowerShell", "git worktree remove --force .\\wt\\", root, J),
        ("Bash", "cd " + q(msys) + " && git worktree remove --force wt", None, J),
        ("Bash", "d=$(pwd); git worktree remove \"$d\"", root, U),   # no folder the hook can name, never the cwd
        ("Bash", "(cd wt) && git worktree remove wt", root, J),
        ("Bash", "git worktree remove --force " + q(msys + "/wt"), None, J),
        ("PowerShell", "git worktree remove repo-wt", repo, J),   # git's last-components form
        ("PowerShell", "$o = git worktree remove --force " + q(wt), None, J),
        # ... both candidates present: the suffix wins, as a plain name, a longer suffix, in any case, by variable
        ("PowerShell", "git worktree remove --force foo", both, os.path.join("wts", "foo", "vendor")),
        ("PowerShell", "git worktree remove wts/foo", both, os.path.join("wts", "foo", "vendor")),
        ("PowerShell", "git worktree remove FOO", both, os.path.join("wts", "foo", "vendor")),
        ("PowerShell", "$w = 'foo'; git worktree remove $w", both, os.path.join("wts", "foo", "vendor")),
        ("Bash", "git worktree remove --force foo", both, os.path.join("wts", "foo", "vendor")),
        ("PowerShell", "git -C " + q(both) + " worktree remove foo", None, os.path.join("wts", "foo", "vendor")),
        ("PowerShell", "git worktree remove foo", rev, None),   # git removes the clean wts\foo, not ./foo
        # ... no suffix match in git's eyes (`foo/`, `.\foo` were refused in the probe): the plain folder is judged
        ("PowerShell", "git worktree remove foo/", rev, J),
        ("PowerShell", "git worktree remove .\\foo", rev, J),
        ("PowerShell", "git worktree remove foo/", both, None),
        ("PowerShell", "git worktree remove .\\foo", both, None),
        ("PowerShell", "git worktree remove oo", both, None),   # a suffix starts at a folder boundary
        # ... `\` in the suffix is `/`, both candidates present
        ("PowerShell", "git worktree remove --force proj\\feature", bs, in_worktree),
        ("PowerShell", "git worktree remove --force PROJ\\FEATURE", bs, in_worktree),
        ("PowerShell", "git worktree remove --force proj/feature", bs, in_worktree),
        ("Bash", "git worktree remove --force 'proj\\feature'", bs, in_worktree),
        ("PowerShell", "git worktree remove proj\\feature", bsrev, None),   # git removes the clean worktree
        # ... only ASCII letters fold
        ("PowerShell", "git worktree remove --force '%s'" % capital, fold, os.path.join("a", capital, "vendor")),
        ("PowerShell", "git worktree remove --force '%s'" % capital.replace("n", "N"), fold,
         os.path.join("a", capital, "vendor")),
        ("PowerShell", "git worktree remove --force '%s'" % small, fold, None),   # the clean one
        ("PowerShell", "git worktree remove --force '%s'" % "ÜNÏX", fold, None),   # no such worktree
        ("PowerShell", "git worktree remove gone", prune, None),
        # an absolute path is never a suffix: no `git worktree list`, so its failure here cannot deny
        ("PowerShell", "git worktree remove " + q(os.path.join(root, "gone")), root, None),
        # ... the hook cannot tell the folder: deny
        ("PowerShell", "git worktree remove --force $wt", root, U),
        ("PowerShell", "$o = git worktree remove $x", root, U),
        ("PowerShell", "Set-Location $d; git worktree remove x", root, U),
        ("PowerShell", "git worktree remove x", None, U),
        ("Bash", "cd \"$DIR\" && git worktree remove --force wt", root, U),
        # ... allowed: no junction, only junctions into the tree itself, not a worktree, other subcommands
        ("PowerShell", "git worktree remove " + q(clean), None, None),
        ("PowerShell", "git worktree remove --force " + q(inner), None, None),
        ("PowerShell", "git worktree remove " + q(os.path.join(root, "gone")), None, None),
        ("PowerShell", "git worktree remove nosuch", repo, None),   # git ran and lists no such worktree
        ("PowerShell", "git worktree remove dup", repo, None),      # two match: git refuses on its own
        ("PowerShell", "git worktree list", root, None),
        ("PowerShell", "git worktree add ..\\x", root, None),
        ("PowerShell", "git worktree remove --force " + q(wt) + " # shell-guard: allow", None, None),
    ]


def checkout_cases(root):
    """A real repository: tracked files, a folder, and a file named like a branch. Returns (tool, command, cwd,
    expected rule) and the repository; what git does with each form was read in the probe."""
    import subprocess
    repo = os.path.join(root, "co")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", repo]
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    for rel in ("README.md", "src\\a.py", "docs\\d.txt", "feature"):
        touch(os.path.join(repo, rel))
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    subprocess.run(git + ["branch", "feature"], check=True)
    R = "git-restore-discards"
    msys = "/" + repo[0].lower() + repo[2:].replace("\\", "/")
    return repo, [
        ("PowerShell", "git checkout README.md", repo, R),
        ("PowerShell", "git checkout HEAD src/a.py", repo, R),
        ("PowerShell", "git checkout main README.md src/a.py", repo, R),
        ("PowerShell", "git checkout README.md src/a.py", repo, R),
        ("PowerShell", "git checkout docs", repo, R),            # a folder: every edit under it goes
        ("PowerShell", "git checkout -f README.md", repo, R),
        ("PowerShell", "git checkout -q README.md", repo, R),
        ("PowerShell", "git -C " + q(repo) + " checkout README.md", None, R),
        ("PowerShell", "cd " + q(repo) + "; git checkout README.md", None, R),
        ("PowerShell", "$f = 'README.md'; git checkout $f", repo, R),
        ("PowerShell", "git checkout " + q(os.path.join(repo, "README.md")), repo, R),
        ("PowerShell", "git checkout " + q(os.path.join(repo, "README.md")), None, R),   # no folder to ask git in
        ("PowerShell", "git checkout main feature", repo, R),    # only the first word can be the commit
        ("PowerShell", "$o = git checkout README.md 2>&1", repo, R),
        ("Bash", "git checkout README.md", repo, R),
        ("Bash", "cd " + q(msys) + " && git checkout src/a.py", None, R),
        # ... both candidates present: a commit wins for one word, as git says "Do not complain" in that case
        ("PowerShell", "git checkout feature", repo, None),
        ("Bash", "git checkout feature", repo, None),
        # a subshell or a substitution keeps its `cd`: what follows reads the folder before it, the body its own
        ("Bash", "d=`cd src && pwd`; git checkout README.md", repo, R),
        ("Bash", "d=\"`cd src && pwd`\"; git checkout README.md", repo, R),
        ("Bash", "d=$(cd src && pwd); git checkout README.md", repo, R),
        ("Bash", "d=\"$(cd src && pwd)\"; git checkout README.md", repo, R),
        ("Bash", "(cd src && pwd); git checkout README.md", repo, R),
        ("Bash", "git checkout $(cd src && pwd) README.md", repo, R),
        ("Bash", "(cd src; (cd ..; pwd); git checkout a.py)", repo, R),
        ("Bash", "(cd src; d=$(cd ..; pwd); git checkout a.py)", repo, R),
        ("Bash", "(cd src; d=`cd ..; pwd`; git checkout a.py)", repo, R),
        ("Bash", "((cd src); cd docs; pwd); git checkout README.md", repo, R),
        ("Bash", "(cd docs; (cd ../src)); git checkout README.md", repo, R),
        ("Bash", "d=`cd docs; echo \`cd ../src; pwd\``; git checkout README.md", repo, R),
        ("Bash", "pushd docs; (popd); popd; git checkout README.md", repo, R),
        ("Bash", "pwd; d=$( (cd src); git checkout README.md)", repo, R),
        ("Bash", "pwd; d=`(cd src); git checkout README.md`", repo, R),
        ("Bash", "(cd src && git checkout a.py)", repo, R),
        ("Bash", "(cd src && git checkout README.md)", repo, None),
        ("Bash", "d=$(cd src && git checkout README.md)", repo, None),
        ("Bash", "d=`cd src && git checkout README.md`", repo, None),
        ("Bash", "cd src && git checkout a.py", repo, R),
        ("Bash", "cd src && git checkout README.md", repo, None),
        ("Bash", "(pwd); cd src; git checkout README.md", repo, None),
        ("Bash", "cd $(cd src && pwd) && git checkout README.md", repo, None),   # unknown stays unknown
        ("Bash", "d=$(cat f); cd \"$d\"; git checkout README.md", repo, None),
        ("Bash", "d=$(cat f); git -C \"$d\" checkout README.md", repo, None),
        ("Bash", "case x in x) cd src;; esac; git checkout a.py", repo, R),   # a `)` that opens no body
        ("Bash", "f() { cd src; }; (pwd); git checkout a.py", repo, R),   # a `()` that holds no command
        ("PowerShell", "git checkout main", repo, None),
        ("PowerShell", "git checkout HEAD", repo, None),
        ("PowerShell", "git checkout nosuchword", repo, None),   # neither a commit nor on disk
        ("PowerShell", "git checkout HEAD nosuchword", repo, None),
        ("PowerShell", "git checkout -", repo, None),
        ("PowerShell", "git checkout -b new README.md", repo, None),   # git refuses: not a commit
        ("PowerShell", "git checkout -qb new README.md", repo, None),
        ("PowerShell", "git checkout --orphan new", repo, None),
        ("PowerShell", "git checkout --orphan README.md", repo, None),   # the branch's name, no path
        ("PowerShell", "git checkout --detach README.md", repo, None),
        ("PowerShell", "git checkout main > README.md", repo, None),   # a redirect's target is no path
        ("PowerShell", "git checkout main 2> README.md", repo, None),
        ("PowerShell", "git checkout main >README.md", repo, None),
        ("PowerShell", "git checkout main 2>&1", repo, None),
        ("Bash", "git checkout main > README.md", repo, None),
        # ... the hook cannot tell: a word it cannot place is left to git, listed in the known gaps
        ("PowerShell", "git checkout README.md", None, None),
        ("PowerShell", "cd $d; git checkout README.md", repo, None),
        ("PowerShell", "git checkout $x", repo, None),
        ("PowerShell", "git checkout README.md # shell-guard: allow", repo, None),
    ]


def q(path):
    """path as one single-quoted shell word: temp paths may hold a space."""
    return "'%s'" % path


def deny_message(cmd, cwd=None):
    f = next((f for f in shell_guard.check_installed("PowerShell", cmd, cwd) if f.deny), None)
    return None if f is None else f.message


def walk_checks(root, links):
    """git-worktree-junction where the real system cannot be made to misbehave: hostile readlink results,
    `git worktree list` failing, a scan that never ends, the stop after 3 links, a scan that crashes.
    Every patched global is restored in finally."""
    import subprocess
    import time
    inner, repo, many = (os.path.join(root, n) for n in ("inner", "repo", "many"))
    os.makedirs(os.path.join(many, "deep"))
    for name in ("j1", "j2", "j3", os.path.join("deep", "j4")):
        links.append(os.path.join(many, name))
        make_junction(links[-1], os.path.join(root, "target"))
    real_readlink, real_run, real_target = os.readlink, subprocess.run, shell_guard._link_target
    real_lstat = os.lstat
    budget = shell_guard.JUNCTION_SCAN_BUDGET
    remove_inner = "git worktree remove --force " + q(inner)
    both = os.path.join(root, "both")
    results = []   # (label, deny message or None, None or the fragments the message must hold)
    try:
        # junction targets in the forms readlink returns; only a drive-letter target can lie inside the tree
        for raw, want in ((r"\\?\UNC\srv\share\x", r"would empty `\\srv\share\x`"),
                          (r"\\?\Volume{0a1b2c3d-0000-0000-0000-000000000000}\d",
                           r"would empty `\\?\Volume{0a1b2c3d-0000-0000-0000-000000000000}\d`"),
                          ("\\\\?\\" + os.path.join(inner, "real"), None)):
            os.readlink = lambda _path, raw=raw: raw
            results.append(("readlink " + raw, deny_message(remove_inner), want))
        os.readlink = real_readlink

        def times_out(args, **_kw):
            raise subprocess.TimeoutExpired(args, 1.5)

        def exits_128(args, **_kw):
            return subprocess.CompletedProcess(args, 128, b"", b"fatal: not a git repository")
        for label, fake in (("git worktree list times out", times_out), ("git worktree list exits 128", exits_128)):
            subprocess.run = fake
            results.append((label, deny_message("git worktree remove nosuch", repo),
                            "the hook cannot tell which folder `nosuch` is: `git worktree list` failed or timed out"))
        # a plain folder of that name does not settle it: a registered worktree may win the suffix lookup
        subprocess.run = times_out
        results.append(("git worktree list times out, a plain folder of that name", deny_message(
            "git worktree remove foo", both), "the hook cannot tell which folder `foo` is"))
        # ... but when git cannot list worktrees here it cannot remove one either: the plain folder is judged
        subprocess.run = exits_128
        results.append(("git worktree list exits 128, a clean plain folder of that name",
                        deny_message("git worktree remove foo", both), None))
        subprocess.run = real_run

        prune = os.path.join(root, "prune")
        gone = os.path.normcase(os.path.join(prune, "wts", "gone"))

        def lstat_denied(path, **kw):
            if os.path.normcase(os.fspath(path)) == gone:
                raise PermissionError(13, "denied")
            return real_lstat(path, **kw)
        os.lstat = lstat_denied
        results.append(("a registered worktree the system refuses to read is not a missing one", deny_message(
            "git worktree remove gone", prune), "an unreadable target"))
        os.lstat = real_lstat

        def slow(path, seconds):   # the walk is one thread: a slow call is a sleep, bounded so a red run ends
            time.sleep(seconds)
            return real_target(path)
        shell_guard._link_target = lambda p: slow(p, 0.5)   # inner holds one link and one more folder to scan
        shell_guard.JUNCTION_SCAN_BUDGET = 0.3
        try:
            got = deny_message(remove_inner)
        except Exception as exc:   # the hook fails open on an error: a red run must show it as a failed assertion
            got = "raised %r" % exc
        results.append(("a scan that never ends", got,
                        ("too large to check", "Only links whose target is outside the tree matter",
                         "When there are none, add `# shell-guard: allow`")))

        shell_guard._link_target = lambda p: slow(p, 1.5) if p.endswith("j4") else real_target(p)
        shell_guard.JUNCTION_SCAN_BUDGET = 2
        start = time.monotonic()
        got = deny_message("git worktree remove --force " + q(many))
        took = time.monotonic() - start
        results.append(("3 links stop the walk", got if took < 1 else "took %.1f s" % took,
                        ("would empty", "j1", "j2", "j3")))

        def crashes(path):
            raise RuntimeError("scan bug")
        shell_guard._link_target = crashes
        try:
            got = deny_message(remove_inner)
        except Exception as exc:   # the hook fails open on an error: a red run must show it as a failed assertion
            got = "raised %r" % exc
        results.append(("a scan that crashes", got, "so git would empty `an unreadable target`"))
    finally:
        os.readlink, subprocess.run, shell_guard._link_target = real_readlink, real_run, real_target
        os.lstat = real_lstat
        shell_guard.JUNCTION_SCAN_BUDGET = budget
    failed = 0
    for label, got, want in results:
        wants = () if want is None else (want,) if isinstance(want, str) else want
        if (got is not None) if want is None else (got is None or not all(w in got for w in wants)):
            failed += 1
            print("WALK FAIL  %s: want=%r got=%r" % (label, want, got and got[:240]))
    print("walk checks: %d/%d pass" % (len(results) - failed, len(results)))
    return failed, len(results)


class FakeEntry:
    """A scandir entry with the given stat result; a stat that is an exception is raised."""

    def __init__(self, path, st, is_dir=False):
        self.path, self.st, self.dir = path, st, is_dir

    def stat(self, follow_symlinks=True):
        if isinstance(self.st, Exception):
            raise self.st
        return self.st

    def is_dir(self, follow_symlinks=True):
        return self.dir


class FakeScan(list):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def link_kind_checks(root, links):
    """Which entries the walk stops at: mount points only, on real links and on stat results Windows cannot be
    made to return here; an unreadable folder or entry cannot be cleared; POSIX has no reparse fields. Every
    patched global and ACL is restored in finally."""
    import subprocess
    from types import SimpleNamespace as NS
    results = []   # (label, got, want)
    target = os.path.join(root, "target")
    tree = os.path.join(root, "symlinks")
    os.makedirs(tree)
    symlinks = True
    try:
        os.symlink(target, os.path.join(tree, "dirlink"), target_is_directory=True)
        os.symlink(touch(os.path.join(root, "outside.txt")), os.path.join(tree, "filelink"))
    except OSError:   # needs Developer Mode or admin; the stat-result cases below still cover the branch
        symlinks = False
        print("# shell-guard: skipped 2 checks: creating a symbolic link needs Developer Mode here")
    if symlinks:
        results.append(("real symbolic links to outside targets", deny_message(
            "git worktree remove " + q(tree)), None))
        results.append(("the same links are walked past, not read as a link", shell_guard.outside_links(tree),
                        ([], True)))

    locked_tree = os.path.join(root, "locked")
    locked = os.path.join(locked_tree, "inner")
    os.makedirs(os.path.join(locked, "unseen"))
    everyone = "*S-1-1-0"
    real_scandir = os.scandir
    try:
        subprocess.run(["icacls", locked, "/deny", everyone + ":(RD)"], capture_output=True, check=True)
        results.append(("a real folder the walk cannot read", shell_guard.outside_links(locked_tree),
                        ([(locked, None)], True)))
        msg = deny_message("git worktree remove " + q(locked_tree))
        results.append(("... is denied through the rule", bool(msg) and "an unreadable target" in msg, True))
        subprocess.run(["icacls", locked, "/remove:d", everyone], capture_output=True, check=True)

        plain = os.path.join(root, "plain")
        os.makedirs(os.path.join(plain, "d"))
        sub = os.path.join(plain, "d")

        def scan_of(*entries):
            def fake(path):
                return FakeScan(entries) if os.path.normcase(path) == os.path.normcase(plain) else real_scandir(path)
            return fake
        os.scandir = scan_of(FakeEntry(sub, NS(st_file_attributes=0x410, st_reparse_tag=0xA000000C)))
        results.append(("a directory symlink's stat", shell_guard.outside_links(plain), ([], True)))
        os.scandir = scan_of(FakeEntry(os.path.join(plain, "f"), NS(st_file_attributes=0x420, st_reparse_tag=0xA000000C)))
        results.append(("a file symlink's stat", shell_guard.outside_links(plain), ([], True)))
        os.scandir = scan_of(FakeEntry(sub, NS(st_file_attributes=0x410, st_reparse_tag=0x9000001A), is_dir=True))
        results.append(("a cloud-file folder's stat is walked, not a link", shell_guard.outside_links(plain), ([], True)))
        os.scandir = scan_of(FakeEntry(sub, NS(), is_dir=True), FakeEntry(os.path.join(plain, "f"), NS()))
        results.append(("POSIX: a stat without Windows fields", shell_guard.outside_links(plain), ([], True)))
        os.scandir = scan_of(FakeEntry(sub, PermissionError(13, "denied")))
        results.append(("an entry the walk cannot stat", shell_guard.outside_links(plain), ([(plain, None)], True)))
    finally:
        os.scandir = real_scandir
        subprocess.run(["icacls", locked, "/remove:d", everyone], capture_output=True)
    failed = 0
    for label, got, want in results:
        if got != want:
            failed += 1
            print("LINK FAIL  %s: want=%r got=%r" % (label, want, got))
    print("link kind checks: %d/%d pass" % (len(results) - failed, len(results)))
    return failed, len(results)


def launcher_checks(root):
    """bat-metachar decides by the file the launcher resolves to, through the installed hook: both candidates
    present in each order, as PowerShell 7.6 chose in the probe, and a relative launcher against the event's cwd."""
    first, second, proj = (os.path.join(root, "launch", n) for n in ("first", "second", "proj"))
    cmd = "php vendor/bin/phpunit --filter 'A|B'"
    pct = "php -r \"echo '%SHELL_GUARD_TEST_VAR%';\""
    touch(os.path.join(first, "php.bat"))
    touch(os.path.join(second, "php.exe"))
    touch(os.path.join(proj, "vendor", "bin", "real.exe"))
    touch(os.path.join(proj, "vendor", "bin", "proxy.bat"))
    touch(os.path.join(proj, "vendor", "bin", "proxy"))
    touch(os.path.join(proj, "tools", "php.exe"))
    touch(os.path.join(proj, "tools", "composer.cmd"))
    same = os.path.join(root, "launch", "same")
    touch(os.path.join(same, "php.exe"))
    touch(os.path.join(same, "php.bat"))
    script = os.path.join(root, "launch", "script")
    touch(os.path.join(script, "php.ps1"))
    touch(os.path.join(script, "php.exe"))
    touch(os.path.join(script, "php.bat"))
    cmdfile = os.path.join(root, "launch", "cmdfile")
    touch(os.path.join(cmdfile, "php.cmd"))
    empty = os.path.join(root, "launch", "empty")
    os.makedirs(empty)
    bare = os.path.join(root, "launch", "bare")   # php with no extension: PowerShell takes it (probe: Get-Command)
    touch(os.path.join(bare, "php"))
    touch(os.path.join(proj, "tools", "wp"))
    herdless = os.path.join(root, "launch", "herdless")   # php.bat on PATH, no Herd under this profile
    touch(os.path.join(herdless, "php.bat"))
    touch(os.path.join(herdless, "composer.bat"))
    path = lambda *dirs: {"PATH": os.pathsep.join(dirs)}
    M = "is a .bat wrapper"
    cases = [
        ("a .bat in the first folder beats a real php.exe in the second", cmd, None, path(first, second), M),
        ("a real php.exe in the first folder beats a .bat in the second", cmd, None, path(second, first), None),
        ("... the %NAME% branch too", pct, None, path(second, first), None),
        ("... and denies with the order swapped", pct, None, path(first, second), M),
        (".exe before .bat inside one folder", cmd, None, path(same), None),
        (".ps1 before both inside one folder", cmd, None, path(script), None),
        ("a .cmd counts as a .bat", cmd, None, path(cmdfile), M),
        ("not on PATH: nothing to protect", cmd, None, path(empty), None),
        ("a relative launcher with a real exe, against cwd", "tools\\php -r 'a|b'", proj, path(empty), None),
        ("a relative launcher with a .cmd, against cwd", "tools\\composer require 'a|b'", proj, path(empty), M),
        ("a vendor\\bin real exe", "vendor\\bin\\real --filter 'A|B'", proj, path(empty), None),
        ("a vendor\\bin proxy: the .bat before the bare file", "vendor\\bin\\proxy --filter 'A|B'", proj, path(empty), M),
        ("vendor\\bin with no cwd stays a proxy", "vendor\\bin\\real --filter 'A|B'", None, path(empty), M),
        ("vendor\\bin not found from cwd stays a proxy", "vendor\\bin\\gone --filter 'A|B'", proj, path(empty), M),
        ("a launcher named by its .bat needs no lookup", "& '%s' -r 'a|b'" % os.path.join(empty, "php.bat"), None,
         path(empty), M),
        # a bare file holds its PATH folder against a .bat further on, as .bat and .exe do
        ("a bare file in the first folder beats a .bat in the second", cmd, None, path(bare, first), None),
        ("a .bat in the first folder beats a bare file in the second", cmd, None, path(first, bare), M),
        ("a relative launcher with no extension, against cwd", "tools\\wp option get 'a|b'", proj, path(empty), None),
        # a name with a folder the hook cannot resolve stays denied: real pwsh ran the first two as .bat files
        ("a folder behind a variable", "& \"$env:USERPROFILE\\.config\\herd\\bin\\php84\" 'a|b'", None, path(empty), M),
        ("... with a cwd too", "& \"$env:USERPROFILE\\.config\\herd\\bin\\php84\" 'a|b'", proj, path(empty), M),
        ("a folder behind ~", os.path.join("~", ".config", "herd", "bin", "php") + " 'a|b'", None, path(empty), M),
        ("a location the hook cannot follow", "Set-Location $d; .\\composer require 'a|b'", proj, path(empty), M),
        ("a forward slash is a folder too", "./composer require 'a|b'", proj, path(empty), M),
        ("a folder in any case", "VENDOR\\bin\\gone --filter 'A|B'", proj, path(empty), M),
        # a move before the call: the cwd no longer says where a name with a folder points
        ("Set-Location, then a real exe under the cwd", "Set-Location sub; tools\\php -r 'a|b'", proj, path(empty), M),
        ("cd, then vendor\\bin", "cd sub; vendor\\bin\\real --filter 'A|B'", proj, path(empty), M),
        ("Push-Location, then a dot folder", "Push-Location sub; %s option get 'a|b'" % os.path.join(".", "tools", "wp"),
         proj, path(empty), M),
        ("Pop-Location, then a folder", "Pop-Location; tools\\php -r 'a|b'", proj, path(empty), M),
        ("a move on an earlier line", "Set-Location sub\ntools\\php -r 'a|b'", proj, path(empty), M),
        ("a move after the call changes nothing", "tools\\php -r 'a|b'; Set-Location sub", proj, path(empty), None),
        ("a move leaves a bare name to PATH", "Set-Location sub; php -r 'a|b'", proj, path(second), None),
    ]
    fix = [
        ("no Herd: the fix names no path", cmd, herdless, "call the program the wrapper starts directly instead"),
        ("no composer.phar: the fix names no path", "composer require 'a/b:^1'", herdless,
         "call the program the wrapper starts directly instead"),
    ]
    failed = 0
    for label, command, cwd, extra, want in cases:
        got = run_installed("--pwsh-all", "PowerShell", command, cwd, extra=extra)
        if (got is not None) if want is None else (got is None or want not in got):
            failed += 1
            print("LAUNCHER FAIL  %s: want=%r got=%r" % (label, want, got and got[:200]))
    for label, command, folder, want in fix:
        got = run_installed("--pwsh-all", "PowerShell", command, None, extra=dict(path(folder), USERPROFILE=empty))
        if got is None or want not in got or ".config" in got:
            failed += 1
            print("LAUNCHER FAIL  %s: want=%r got=%r" % (label, want, got and got[:200]))
    # the named exe exists: Herd present, with a space in the profile when TEMP has one
    got = run_installed("--pwsh-all", "PowerShell", "php83 -r 'a|b'")
    if got is None or '`& "%s"`' % os.path.join(FAKE_HERD, "php83", "php.exe") not in got:
        failed += 1
        print("LAUNCHER FAIL  the fix names the real exe: got=%r" % got)
    # check() hands the event's cwd to the launcher lookup of both shells
    wiring = [("Bash", "tools/php -r 'a|b'"), ("PowerShell", "tools\\php -r 'a|b'")]
    for tool, command in wiring:
        got = [f.message for f in shell_guard.check(tool, command, proj) if f.deny]
        if got:
            failed += 1
            print("LAUNCHER FAIL  check() with a cwd, %s: want none got=%r" % (tool, got[0][:200]))
    # Bash: the .cmd under the cwd counts until a `cd` moves away from it; the full guard
    bash_moves = [("tools/composer require 'a|b'", True), ("cd sub && tools/composer require 'a|b'", False),
                  ("tools/composer require 'a|b'; cd sub", True)]
    for command, want in bash_moves:
        got = [f.message for f in shell_guard.check("Bash", command, proj) if f.deny]
        if bool(got) != want:
            failed += 1
            print("LAUNCHER FAIL  Bash with a cwd: %r want deny=%r got=%r" % (command, want, got))
    # the Bash fix: a program outside the project in its Git Bash form, a project file as typed
    php = '"%s"' % shell_guard._msys(os.path.join(FAKE_HERD, "php84", "php.exe"))
    bash_fix = [("vendor/bin/phpunit.bat --filter='A|B'", "call `%s \"vendor/bin/phpunit.bat\"`" % php),
                ("composer require a/b:^1", "call `%s \"%s\"`" % (php, shell_guard._msys(HERD + "\\composer.phar")))]
    for command, want in bash_fix:
        got = next((f.message for f in shell_guard.check("Bash", command) if f.deny), None)
        if got is None or want not in got:
            failed += 1
            print("LAUNCHER FAIL  the Bash fix: want=%r got=%r" % (want, got and got[:200]))
    # php/composer/wp run Herd's global default, which no file says: the fix names the newest phpNN folder
    for names, want in ((("php83", "php85", "php84"), "php85"), ((), "")):
        herd = tempfile.mkdtemp(prefix="sg-herd-", dir=TOOLS)
        for n in names:
            touch(os.path.join(herd, n, "php.exe"))
        got = shell_guard.default_php_exe(herd)
        if got != (os.path.join(herd, want, "php.exe") if want else ""):
            failed += 1
            print("LAUNCHER FAIL  default php of %r: got=%r" % (names, got))
    total = len(cases) + len(fix) + 1 + len(wiring) + len(bash_moves) + len(bash_fix) + 2
    print("launcher checks: %d/%d pass" % (total - failed, total))
    return failed, total



# The hook run through its real `--pwsh-all` entry point with a slow os.scandir under argv[2] (1.4 s per
# folder) that never returns under argv[3].
NEVER_SCANDIR_DRIVER = (
    "import os, runpy, sys, threading, time\n"
    "never_set, real_scandir = threading.Event(), os.scandir\n"
    "slow, never = (os.path.normcase(p) for p in sys.argv[2:4])\n"
    "def stub(path):\n"
    "    if os.path.normcase(path).startswith(never):\n"
    "        never_set.wait()\n"
    "    if os.path.normcase(path).startswith(slow):\n"
    "        time.sleep(1.4)\n"
    "    return real_scandir(path)\n"
    "os.scandir = stub\n"
    "sys.argv = [sys.argv[1], '--pwsh-all']\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n")


# A git that hangs the way Git's cmd\git.exe launcher does: a child holds its stdin, stdout and stderr and
# sleeps argv[1] seconds, and it sleeps as long itself. A timeout kills only this process.
HOLDING_GIT = (
    "import subprocess, sys\n"
    "sleep = 'import sys, time; time.sleep(float(sys.argv[1]))'\n"
    "subprocess.Popen([sys.executable, '-I', '-S', '-c', sleep, sys.argv[1]], stdin=0, stdout=1, stderr=2)\n"
    "exec(sleep)\n")
HOLD_SECONDS = "6"   # past every limit below
# The hook through its real `--pwsh-all` entry point, with HOLDING_GIT (argv[2]) in place of git.
HOLDING_GIT_DRIVER = (
    "import runpy, subprocess, sys\n"
    "real_run, stub = subprocess.run, sys.argv[2]\n"
    "subprocess.run = lambda args, **kw: real_run([sys.executable, '-I', '-S', '-c', stub, '%s'], **kw)\n"
    "sys.argv = [sys.argv[1], '--pwsh-all']\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n" % HOLD_SECONDS)


def budget_checks(root):
    """The whole-hook budget: several removes in one command, a scandir that never returns, a hanging
    `git worktree list`, a git whose child holds the pipes, a walk that raises. Wall times are measured here;
    HOOK_BUDGET, the budget's start,
    os.scandir, subprocess.run, shell_guard._walk and threading.excepthook are restored in finally."""
    import subprocess
    import threading
    import time
    trees = [os.path.join(root, "budget", "b%d" % i) for i in range(4)]
    for t in trees:
        os.makedirs(os.path.join(t, "sub"))
    repo, co = os.path.join(root, "repo"), os.path.join(root, "co")
    real_scandir, real_run, real_walk = os.scandir, subprocess.run, shell_guard._walk
    real_hook, budget = threading.excepthook, shell_guard.HOOK_BUDGET
    never_set = threading.Event()
    results, times = [], {}   # (label, got, fragments the deny must hold); times: wall seconds by label

    def timed(label, budget_s, margin, cmd, cwd=None, bound=5.0):
        """Starts a budget_s budget, runs the PowerShell check, and returns the deny message, or a text
        that names what went wrong (a deny past budget_s + margin, a raise, no return within bound)."""
        shell_guard.HOOK_BUDGET = budget_s
        shell_guard.start_budget()
        start = time.monotonic()
        got = _bounded(lambda: deny_message(cmd, cwd), bound)
        took = times[label] = time.monotonic() - start
        if got is None:
            return "no answer within %.0f s" % bound
        if got[0] == "raised":
            return "raised " + got[1]
        if got[1] is not None and took > budget_s + margin:
            return "denied after %.2f s, budget %.1f s" % (took, budget_s)
        return got[1]
    try:
        # 4 removes, each walk 0.7 s (> budget / 4): the third is cut off when the 2 s budget ends
        def slow_walk(root_, limit, deadline):
            time.sleep(0.7)
            return real_walk(root_, limit, deadline)
        shell_guard._walk = slow_walk
        label = "several removes in one command"
        results.append((label, timed(label, 2.0, 0.5, "; ".join("git worktree remove " + q(t) for t in trees)),
                        ("too large to check", trees[2])))
        shell_guard._walk = real_walk

        os.scandir = lambda path: never_set.wait()   # an offline share: the call never returns
        label = "a scandir that never returns"
        results.append((label, timed(label, 1.0, 0.5, "git worktree remove " + q(trees[0])), "too large to check"))
        os.scandir = real_scandir

        def hangs(args, **kw):   # a real process that outlives any timeout, killed by subprocess.run's
            return real_run([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"], **kw)
        subprocess.run = hangs
        label = "git worktree list hangs"
        results.append((label, timed(label, 0.6, 0.5, "git worktree remove nosuch", repo),
                        "`git worktree list` failed or timed out"))

        def holds(_args, **kw):   # killed by the timeout; only its child is left, holding whatever it was given
            return real_run([sys.executable, "-I", "-S", "-c", HOLDING_GIT, HOLD_SECONDS], **kw)
        subprocess.run = holds
        # the limit lies under the 1.5 s cap, so only the budget can meet it; bound (5 s) < HOLD_SECONDS
        for label, cmd, cwd, want in (
                ("git worktree list: its child holds the pipes", "git worktree remove nosuch", repo,
                 "`git worktree list` failed or timed out"),
                ("git rev-parse: its child holds the pipes", "git checkout feature", co, "`git checkout` on feature")):
            results.append((label, timed(label, 0.8, 0.5, cmd, cwd), want))
        subprocess.run = real_run

        def raises(*_a):
            raise RuntimeError("walk bug")
        hooked = []
        threading.excepthook = hooked.append   # a traceback from the worker would land here
        shell_guard._walk = raises
        label = "a walk that raises"
        got = timed(label, 2.0, 0.5, "git worktree remove " + q(trees[0]))
        results.append((label, "worker printed %r" % hooked[0].exc_value if hooked else got, "too large to check"))
    finally:
        os.scandir, subprocess.run, shell_guard._walk = real_scandir, real_run, real_walk
        threading.excepthook, shell_guard.HOOK_BUDGET, shell_guard._deadline = real_hook, budget, None
        never_set.set()   # cleanup only, after every measurement: lets an in-process worker finish

    # The hook process itself, real budget: 4 removes, b0 and b1 take 2.8 s each, b2's scandir never
    # returns. It must deny b2 and exit within the 10 s hook timeout although that worker never returns.
    event = json.dumps({"tool_name": "PowerShell", "tool_input": {
        "command": "; ".join("git worktree remove " + q(t) for t in trees)}})
    label = "e2e --pwsh-all, 4 removes, the third walk's scandir never returns"
    start = time.monotonic()
    try:
        proc = subprocess.run([sys.executable, "-I", "-S", "-c", NEVER_SCANDIR_DRIVER, GUARD,
                               os.path.dirname(trees[0]), trees[2]],
                              input=event.encode("utf-8"), capture_output=True, timeout=10)
        out = proc.stdout.decode("utf-8", "replace")
        got = ("exit %d, stderr %r" % (proc.returncode, proc.stderr[:200]) if proc.returncode or proc.stderr
               else json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else None)
    except subprocess.TimeoutExpired:
        got = "the hook process did not exit within 10 s"
    times[label] = time.monotonic() - start
    results.append((label, got, ("too large to check", trees[2])))

    # The real budget, through the hook: each call ends at its own 1.5 s cap, whatever the child holds
    for label, cmd, cwd, want in (("e2e --pwsh-all, git worktree list held", "git worktree remove nosuch", repo,
                                   "`git worktree list` failed or timed out"),
                                  ("e2e --pwsh-all, git rev-parse held", "git checkout feature", co,
                                   "`git checkout` on feature")):
        event = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": cmd}, "cwd": cwd})
        start = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", HOLDING_GIT_DRIVER, GUARD, HOLDING_GIT],
                                  input=event.encode("utf-8"), capture_output=True, timeout=10)
            out = proc.stdout.decode("utf-8", "replace")
            got = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out else "no deny"
        except subprocess.TimeoutExpired:
            got = "the hook process did not exit within 10 s"
        took = times[label] = time.monotonic() - start
        results.append((label, got if took < 4 else "denied after %.2f s" % took, want))

    failed = 0
    for label, got, want in results:
        wants = (want,) if isinstance(want, str) else want
        if got is None or not all(w in got for w in wants):
            failed += 1
            print("BUDGET FAIL  %s: want=%r got=%r" % (label, want, got and got[:240]))
    print("budget checks: %d/%d pass; wall s: %s" % (len(results) - failed, len(results), ", ".join(
        "%s %.2f" % (k, v) for k, v in times.items())))
    return failed, len(results)


def race_check():
    """The review's race tree: an empty leaf `a`, 3,000 sibling folders and `zz\\out`, a junction to a folder
    outside the tree. A walk that reports "finished" before it has listed every folder misses the link.
    Runs under a 1 microsecond thread switch interval, restored in finally; the junction is removed first."""
    import shutil
    import tempfile
    base = tempfile.mkdtemp(prefix="sg-race-")
    tree, outside = os.path.join(base, "tree"), os.path.join(base, "outside")
    link = os.path.join(tree, "zz", "out")
    interval = sys.getswitchinterval()
    failures = []
    try:
        os.makedirs(os.path.join(tree, "a"))
        for i in range(3000):
            os.mkdir(os.path.join(tree, "b%04d" % i))
        os.makedirs(os.path.dirname(link))
        os.mkdir(outside)
        make_junction(link, outside)
        sys.setswitchinterval(1e-6)
        for n in range(5):
            links, finished = shell_guard.outside_links(tree)
            got = [(os.path.normcase(p), os.path.normcase(t)) for p, t in links]
            want = [(os.path.normcase(link), os.path.normcase(outside))]
            if not finished or got != want:
                failures.append("run %d: finished=%r links=%r want=%r" % (n, finished, got, want))
    finally:
        sys.setswitchinterval(interval)
        if os.path.lexists(link):
            os.rmdir(link)   # removes the junction, not its target
        shutil.rmtree(base)
    for text in failures[:2]:
        print("RACE FAIL  %s" % text)
    print("race check: %d/5 pass" % (5 - len(failures)))
    return len(failures), 5


# The narrow hook runs check_git only: the same expectations for every git case above.
GIT_RULES = {"ps-git-commit-stdin", "git-restore-discards", "git-no-verify", "git-worktree-junction"}


def run_installed(flag, tool, cmd, cwd=None, drop=(), extra=None):
    """The hook as the plugin runs it: event JSON on stdin; returns the denied reason or None. `drop`:
    environment variables the hook process does not get; `extra`: ones it gets on top."""
    import subprocess
    event = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}, "cwd": cwd})
    env = {k: v for k, v in os.environ.items() if k != "SHELL_GUARD" and k not in drop}
    env.update(extra or {})
    proc = subprocess.run([sys.executable, "-I", "-S", GUARD, flag], input=event.encode("utf-8"),
                          capture_output=True, timeout=30, env=env)
    out = proc.stdout.decode("utf-8", "replace")   # a wrong encoding shows as U+FFFD in the reason
    if proc.returncode != 0 or proc.stderr:
        return "hook error %d: %r" % (proc.returncode, proc.stderr[:200])
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]




def main():
    """Pins the environment variable the %NAME% cases expand and the scratch tree the junction cases use;
    the junctions themselves are deleted before the tree, and their target must survive."""
    import stat
    saved = os.environ.get("SHELL_GUARD_TEST_VAR")
    os.environ["SHELL_GUARD_TEST_VAR"] = "probe"
    root = tempfile.mkdtemp(prefix="sg-")   # junctions are made only under this folder
    links = []
    try:
        return run_all(root, links)
    finally:
        for link in links:
            if os.path.lexists(link):
                os.rmdir(link)   # removes the junction, not its target
        keep = os.path.join(root, "target", "keep.txt")
        if os.path.isdir(os.path.join(root, "target")) and not os.path.exists(keep):
            print("CLEANUP FAIL  a junction target lost keep.txt")
            sys.exit(1)

        def writable(func, path, _exc):
            os.chmod(path, stat.S_IWRITE)   # git's object files are read-only
            func(path)
        shutil.rmtree(root, onerror=writable)
        shutil.rmtree(TOOLS, ignore_errors=True)
        for name, value in list(SAVED_ENV.items()) + [("SHELL_GUARD_TEST_VAR", saved)]:
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_all(root, links):
    failed = 0
    for tool, cmd, want in CASES:
        got = next((f.rule for f in shell_guard.check(tool, cmd) if f.deny), None)
        if got != want:
            failed += 1
            print("CASE FAIL  want=%s got=%s  %s: %r" % (want, got, tool, cmd[:90]))
    print("cases: %d/%d pass" % (len(CASES) - failed, len(CASES)))
    git_cases = [c for c in CASES if c[2] in GIT_RULES or (c[2] is None and "git " in c[1])]
    git_failed = 0
    for tool, cmd, want in git_cases:
        got = next((f.rule for f in shell_guard.check_git(tool, cmd) if f.deny), None)
        if got != want:
            git_failed += 1
            print("GIT-ONLY FAIL  want=%s got=%s  %s: %r" % (want, got, tool, cmd[:90]))
    print("git-only cases: %d/%d pass" % (len(git_cases) - git_failed, len(git_cases)))
    failed += git_failed

    narrow_failed = 0
    for tool, cmd, want in NARROW_CASES:
        got = next((f.rule for f in shell_guard.check_narrow(tool, cmd) if f.deny), None)
        if got != want:
            narrow_failed += 1
            print("NARROW FAIL  want=%s got=%s  %s: %r" % (want, got, tool, cmd[:110]))
    print("narrow cases: %d/%d pass" % (len(NARROW_CASES) - narrow_failed, len(NARROW_CASES)))
    failed += narrow_failed

    # git-worktree-junction on real junctions: the installed paths (--pwsh-all, the Bash --only-git
    # handler) and the full guard, then the walk running out of time on a tree without any
    wt_cases = worktree_cases(root, links)
    wt_failed = 0
    for tool, cmd, cwd, want in wt_cases:   # want: None, or a fragment of the git-worktree-junction deny
        paths = [shell_guard.check, shell_guard.check_installed if tool == "PowerShell" else shell_guard.check_git]
        for fn in paths:
            f = next((f for f in fn(tool, cmd, cwd) if f.deny), None)
            got = None if f is None else f.rule if f.rule != "git-worktree-junction" else f.message
            if (want is None and got is not None) or (want is not None and (got is None or want not in got)):
                wt_failed += 1
                print("WORKTREE FAIL  %s want=%s got=%s  %s: %r cwd=%r"
                      % (fn.__name__, want, got and got[:80], tool, cmd, cwd))
    print("worktree cases: %d/%d pass" % (2 * len(wt_cases) - wt_failed, 2 * len(wt_cases)))
    failed += wt_failed

    # git-restore-discards on a real repository, through the same paths
    repo, co_cases = checkout_cases(root)
    co_failed = 0
    for tool, cmd, cwd, want in co_cases:
        for fn in (shell_guard.check, shell_guard.check_installed if tool == "PowerShell" else shell_guard.check_git):
            got = next((f.rule for f in fn(tool, cmd, cwd) if f.deny), None)
            if got != want:
                co_failed += 1
                print("CHECKOUT FAIL  %s want=%s got=%s  %s: %r cwd=%r" % (fn.__name__, want, got, tool, cmd, cwd))
    print("checkout cases: %d/%d pass" % (2 * len(co_cases) - co_failed, 2 * len(co_cases)))
    failed += co_failed
    # a lookup that fails cannot prove the word is a commit, so the word on disk counts as a path
    import subprocess
    real_run = subprocess.run

    def lookup_raises(args, **_kw):
        raise subprocess.TimeoutExpired(args, 1.5)
    try:
        subprocess.run = lookup_raises
        got = next((f.rule for f in shell_guard.check_git("PowerShell", "git checkout feature", repo) if f.deny), None)
    finally:
        subprocess.run = real_run
    if got != "git-restore-discards":
        failed += 1
        print("CHECKOUT FAIL  a failed commit lookup: want=git-restore-discards got=%s" % got)
    total = len(CASES) + len(git_cases) + len(NARROW_CASES) + 2 * len(wt_cases) + 2 * len(co_cases) + 1
    for section in (lambda: walk_checks(root, links), lambda: link_kind_checks(root, links),
                    lambda: budget_checks(root), race_check, lambda: launcher_checks(root)):
        section_failed, section_total = section()
        failed += section_failed
        total += section_total

    # End to end through the installed path: stdin event JSON into `shell_guard.py <flag>`.
    wt = os.path.join(root, "wt")
    e2e_new = [
        ("--pwsh-all", "PowerShell", "bash scripts/x.sh", None, "which is WSL, not Git Bash"),
        ("--pwsh-all", "PowerShell", "$r = bash -c 'ls'", None, "which is WSL, not Git Bash"),
        ("--pwsh-all", "PowerShell", SE + " -Path a -Find x -Replace y -Run 'bash t.sh'", None,
         "safe-edit runs its -Run string with `pwsh -Command`"),
        ("--pwsh-all", "PowerShell", "[IO.File]::ReadAllText('tools\\x.php')", None,
         "resolves the relative path `tools\\x.php` against the process directory"),
        ("--pwsh-all", "PowerShell", "git show HEAD:a.txt | Set-Content -NoNewline a.txt", None,
         "`git restore --source <rev> -- <path>`"),
        ("--pwsh-all", "PowerShell", "composer require 'a/b:^1'", None,
         "call `& \"%s\\php84\\php.exe\" \"%s\\composer.phar\"`" % (HERD, HERD)),
        ("--pwsh-all", "PowerShell", "wp option get 'a|b'", None, "call `& \"%s\\php84\\php.exe\" \"%s\"`" % (HERD, WPCLI)),
        ("--pwsh-all", "PowerShell", "vendor\\bin\\phpunit --filter 'A|B'", None,
         "call `& \"%s\\php84\\php.exe\" \"vendor/bin/phpunit\"`" % HERD),
        ("--only-narrow", "PowerShell", "Write-Host “don't”; bash t.sh", None, "which is WSL, not Git Bash"),
        ("--pwsh-all", "PowerShell", "Write-Host “don't”; git commit -nm x", None, "skips the repository's hooks"),
        ("--pwsh-all", "PowerShell", "Write-Host “don't”; git commit -nm x; Write-Host “won't”", None,
         "skips the repository's hooks"),
        ("--only-narrow", "PowerShell", WRITE_REPORT + " ${x", None, "`$report` is written"),   # `${` never closed
        ("--only-git", "Bash", "git checkout README.md", os.path.join(root, "co"), "`git checkout` on README.md"),
        ("--pwsh-all", "PowerShell", "git checkout feature", os.path.join(root, "co"), None),
        ("--pwsh-all", "PowerShell", "git worktree remove foo", os.path.join(root, "both"),
         "%s holds" % os.path.join(root, "both", "wts", "foo")),
        ("--pwsh-all", "PowerShell", "wp option get x%SHELL_GUARD_TEST_VAR%", None,
         "replaces `%SHELL_GUARD_TEST_VAR%`"),
        ("--pwsh-all", "PowerShell", "git commit -nm x", None, "`git commit -nm` skips the repository's hooks"),
        ("--pwsh-all", "PowerShell", "php83 -r '[\"a\"=>1];'", None, "treats its `>` as an operator"),
        ("--pwsh-all", "PowerShell", "Select-String -Path a -Pattern x -Recurse", None,
         "`Select-String -Recurse` is a bash flag"),
        ("--pwsh-all", "PowerShell", "git worktree remove wt", root, "so git would empty `%s`" % os.path.join(root, "target")),
        ("--only-git", "Bash", "git worktree remove --force wt", root, "holds `%s`" % os.path.join(wt, "src", "node_modules")),
        ("--only-git", "Bash", "git push --no-verify origin x", None, "`git push --no-verify` skips"),
        ("--pwsh-all", "PowerShell", "git rebase --no-verify main", None, "`git rebase --no-verify` skips"),
        ("--pwsh-all", "PowerShell", "git -c Core.HooksPath=x merge side", None,
         "`Core.HooksPath=x` can point git at another hooks folder, so `git merge` may skip"),
        ("--only-git", "Bash", "git --config-env=core.hooksPath=V commit -m m", None, "`core.hooksPath=V` can point git"),
        ("--pwsh-all", "PowerShell", "$env:GIT_CONFIG_PARAMETERS=$v; git am p.patch", None,
         "`GIT_CONFIG_PARAMETERS` can point git at another hooks folder, so `git am` may skip"),
        ("--only-git", "Bash", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath git commit -m m", None,
         "`GIT_CONFIG_KEY_0` can point git"),
        ("--only-git", "Bash", "env GIT_CONFIG_COUNT=$n git commit -m m", None, "`GIT_CONFIG_COUNT` can point git"),
        ("--pwsh-all", "PowerShell", "git -c core.hooksPath=x commit -m m # shell-guard: allow", None, None),
        ("--pwsh-all", "PowerShell", "git -c core.pager=cat log", None, None),
        ("--pwsh-all", "PowerShell", "& \"C:\\Program Files\\Git\\bin\\bash.exe\" -lc ls", None, None),
        ("--pwsh-all", "PowerShell", "git worktree remove clean", root, None),
        ("--only-git", "Bash", "git merge --no-verify side", None, "`git merge --no-verify` skips"),
        ("--only-git", "Bash", "git rebase --no-verify main", None, "`git rebase --no-verify` skips"),
        ("--only-git", "Bash", "git am -n p.patch", None, "`git am -n` skips"),
        ("--only-git", "Bash", "git -c core.hooksPath=x cherry-pick side", None, "so `git cherry-pick` may skip"),
        ("--only-git", "Bash", "git -c core.hooksPath=x push", None, "so `git push` may skip"),
        ("--only-git", "Bash", "git merge side", None, None),
        ("--only-git", "Bash", "git push origin main", None, None),
        ("--only-git", "Bash", "git commit -m 'never --no-verify'", None, None),
    ]
    e2e = [
        ("--only-narrow", "PowerShell",
         "npm test 2>&1 | Tee-Object run.log | ForEach-Object { $_ } | Select-Object -First 5",
         "`npm` is piped into `Select-Object`"),
        ("--only-narrow", "PowerShell", "gh release create v1.2.0 -n \"Fix „the” thing\"",
         "Quote it with '...' instead, or write it with the Write tool and pass `--notes-file <file>`."),
        ("--only-narrow", "PowerShell", "$text | Out-File report.md", "`$text` is written into the file"),
        ("--only-narrow", "Bash", SE + " -Run 'npm test' 2>&1 | head -5", "red run's restore line"),
        ("--only-narrow", "Bash", "cat > a.py <<'EOF'\n" + "x = 'y'\n" * 1200 + "EOF\n",
         "write the content with the Write tool"),
        ("--only-git", "Bash", "python -c '" + "x = 1;" * 1400 + "'; git status",
         "write the content with the Write tool"),
        ("--only-narrow", "Bash", "sed -i 's/_i/_i\\\\d+/g' rulediff.cjs",
         "Git Bash turns every `\\\\` in a Bash tool command into `\\` before bash reads it, inside quotes and "
         "`<<'EOF'` bodies too: line 1 reaches bash as `sed -i 's/_i/_i\\d+/g' rulediff.cjs`. Write code that "
         "holds backslashes with the Write tool and run it by path, and edit source with the Edit tool."),
        ("--only-narrow", "Bash", "python - <<'EOF'\nx = 1\nprint('a\\\\nb')\nEOF",
         "line 3 reaches bash as `print('a\\nb')`."),
        ("--only-narrow", "Bash", "echo '" + "x" * 80 + "\\\\" + "y" * 80 + "'",
         "line 1 reaches bash as `" + "x" * 30 + "\\" + "y" * 29 + "`."),
        ("--only-narrow", "Bash", "cat > a.py <<'EOF'\nx = 1\nEOF\n", None),
        ("--only-narrow", "PowerShell", "npm test 2>&1 | Tee-Object run.log | Select-Object -Last 5", None),
        ("--only-narrow", "PowerShell", "gh release create v1.2.0 -n 'Fix „the” thing'", None),
        ("--only-narrow", "Bash", "gh issue create --title x --body \"it's broken\"", None),
        # the single PowerShell handler: git and narrow rules in one process, the git rule first
        ("--pwsh-all", "PowerShell", "git checkout -- src/a.js", "`git checkout` on src/a.js throws away"),
        ("--pwsh-all", "PowerShell", "gh pr create --title \"Fix „x”\" --body-file b.txt",
         "opened by `\"` at a typographic `„` too, so the text after `--title` reaches gh torn into pieces or "
         "stripped of its quotes. Quote it with '...' instead."),
        ("--pwsh-all", "PowerShell", "gh pr create --title 'Fix ‘x’ now' --body-file b.txt",
         "Quote it with \"...\" (mind `$` in it) instead."),
        ("--pwsh-all", "PowerShell", "git checkout -- a.txt; $t | Set-Content f.txt", "`git checkout` on a.txt"),
        ("--pwsh-all", "PowerShell", "Get-Date", None),
    ]
    e2e = [(flag, tool, cmd, None, want) for flag, tool, cmd, want in e2e] + e2e_new
    e2e_failed = 0
    for flag, tool, cmd, cwd, want in e2e:
        got = run_installed(flag, tool, cmd, cwd)
        ok = got is None if want is None else (got is not None and want in got)
        if not ok:
            e2e_failed += 1
            print("E2E FAIL  %s want=%r got=%r  %s: %r" % (flag, want, got, tool, cmd[:90]))
    # Without the optional tools' env vars the hook still denies, and names no machine path in the fix.
    generic = "; call the program the wrapper starts directly instead."
    gone = os.path.join(TOOLS, "missing")
    # With no profile the Herd path is only a placeholder, no file: the fix must not name it
    placeholder = "call `& \"%USERPROFILE%\\.config\\herd\\bin\\php84\\php.exe\" %USERPROFILE%\\.config\\herd\\bin\\"
    no_profile = generic
    unset = [("wp option get 'a|b'", TOOL_ENV, None, generic),
             ("wp option get 'a|b'", (), {"RIGGER_WPCLI_PHAR": gone}, generic),
             ("composer require 'a/b:^1'", ("USERPROFILE",), None, no_profile)]
    for cmd, drop, extra, want in unset:
        got = run_installed("--pwsh-all", "PowerShell", cmd, drop=drop, extra=extra)
        ok = got is None if want is None else (got is not None and want in got and TOOLS not in got
                                               and placeholder.replace('"', "") not in got.replace('"', ""))
        if not ok:
            e2e_failed += 1
            print("E2E FAIL  tool env drop=%r extra=%r want=%r got=%r  %r" % (drop, extra, want, got, cmd))
    profile = os.environ.get("USERPROFILE", "")
    derived = [("Herd bin from USERPROFILE", HERD, profile + "\\.config\\herd\\bin"),
               ("Composer bin from APPDATA", shell_guard.COMPOSER_BIN_WIN,
                os.environ.get("APPDATA", "") + "\\Composer\\vendor\\bin"),
               ("USERPROFILE is set for this check", bool(profile), True)]
    for label, got, want in derived:
        if got != want:
            e2e_failed += 1
            print("E2E FAIL  %s: want=%r got=%r" % (label, want, got))
    e2e_total = len(e2e) + len(unset) + len(derived)
    print("e2e hook runs: %d/%d pass" % (e2e_total - e2e_failed, e2e_total))
    failed += e2e_failed
    total += e2e_total

    corpus_failed, corpus_total = corpus_fixture_checks()
    failed += corpus_failed
    total += corpus_total
    if not HERD.startswith(FAKE_HOME):
        failed += 1
        print("HOME FAIL  the guard did not read the fake profile: %s" % HERD)
    print("# shell-guard: %d passed, %d failed" % (total - failed, failed))
    return 1 if failed else 0


# The synthetic corpus: what corpus_check must count on it (no 0/0: every count is fixed).
CORPUS_FIXTURE = os.path.join(HERE, "fixtures", "corpus.jsonl")
CORPUS_COUNTS = {"entries": 18, "unparsed": 0, "failures": 9, "judged": 8, "still_denied": 8, "regressed": 0,
                 "successes": 9, "flagged": 1, "allowed": 7, "git_listed": 1, "new_fp": 0}


def corpus_fixture_checks():
    """corpus_check on the synthetic fixture gives the known counts, and catches a planted regression,
    a planted false positive and an unparsable line."""
    results = []
    entries, unparsed = corpus_check.load([CORPUS_FIXTURE])
    counts = corpus_check.evaluate(entries, unparsed)["counts"]
    results.append(("synthetic corpus counts", counts, CORPUS_COUNTS))
    planted = [{"tool": "PowerShell", "cmd": "Get-Date", "error": "e", "rule": "ps-unix-command"},
               {"tool": "PowerShell", "cmd": "npm test 2>&1 | tail -5"}]
    got = corpus_check.evaluate(entries + planted, 0)["counts"]
    results.append(("planted regression and false positive are counted",
                    (got["regressed"], got["new_fp"]), (1, 1)))
    results.append(("an unparsable line fails the run",
                    corpus_check.evaluate(entries, 1)["ok"], False))
    results.append(("the fixture passes", corpus_check.evaluate(entries, unparsed)["ok"], True))
    import subprocess
    script = os.path.join(HERE, "corpus_check.py")
    bad_line = os.path.join(TOOLS, "corpus-bad-line.jsonl")
    with open(bad_line, "w", encoding="utf-8") as fh:
        fh.write('{"tool": "PowerShell", "cmd": "Get-Date"}\nnot json\n')
    for label, args, want in (("CLI: the fixture exits 0", ["--corpus", CORPUS_FIXTURE], 0),
                              ("CLI: no --corpus exits 2", [], 2),
                              ("CLI: a missing corpus file exits 2", ["--corpus", os.path.join(TOOLS, "none.jsonl")], 2),
                              ("CLI: an unparsable line exits 2", ["--corpus", bad_line], 2)):
        proc = subprocess.run([sys.executable, "-I", script] + args, capture_output=True, timeout=60)
        results.append((label, proc.returncode, want))
    failed = 0
    for label, got, want in results:
        if got != want:
            failed += 1
            print("CORPUS FAIL  %s: want=%r got=%r" % (label, want, got))
    print("corpus fixture checks: %d/%d pass" % (len(results) - failed, len(results)))
    return failed, len(results)


sys.exit(main())
