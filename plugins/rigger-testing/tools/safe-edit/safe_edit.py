"""Red run in place: break one file on purpose, run the test command, always put the file back.

  python safe_edit.py --path FILE --find TEXT --replace TEXT [--count N]
                      [--run COMMAND --expect-ran REGEX] [--working-directory DIR]
  python safe_edit.py --path FILE --restore

A red run never swaps working files by hand: a tool timeout kills the shell before `finally` runs, and
`git checkout` as the restore discards every other uncommitted edit in the file. This makes the in-place
red run safe:

- the edit is a literal replace that must match exactly --count times (proof the mutation applied);
- the original bytes, the hash of the mutated file and the shell that runs the command go into
  `<file>.safe-edit-backup` first;
- the command runs, then the file is restored from that backup and its hash is checked;
- a backup left by a killed run is restored by the next call (or by --restore) when the file is still
  exactly as that run left it; if it changed since, the script refuses and says so.

Line endings and a UTF-8 BOM are kept: in a CRLF file, a newline in --find/--replace becomes CRLF.

--run requires --expect-ran (a regex, case-insensitive, matched against each output line), --expect-ran
requires --run, an empty --run is refused. The command runs through the platform shell: `pwsh -NoProfile
-Command` on Windows when pwsh is on PATH, else `sh -c`.

Exit codes with --run: 0 = red as expected (the command failed and --expect-ran matched), 1 = the script
itself failed, 2 = refused (usage, no or too many matches, a stale backup that cannot be restored safely),
3 = the output does not match --expect-ran (no test may have run), 4 = not red (the command passed: the
mutation was not caught). Without --run the edit is applied and kept (exit 0) or refused (exit 2); there is
no backup, so --restore cannot undo it: revert with the inverse --find/--replace.

The messages keep the wording of the PowerShell tool this replaces, so `-Find` and `-Run` in them name
`--find` and `--run`.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

BACKUP_SUFFIX = ".safe-edit-backup"
BOM = b"\xef\xbb\xbf"


class Refused(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("usage: %s" % message)


def say(text):
    sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))
    sys.stdout.buffer.flush()


def sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def write_bytes(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def platform_shell(which=shutil.which, windows=None):
    """The argv prefix the command runs behind: pwsh on Windows when it is on PATH, else sh."""
    windows = os.name == "nt" if windows is None else windows
    if windows:
        pwsh = which("pwsh")
        if pwsh:
            return [pwsh, "-NoProfile", "-Command"]
    return ["sh", "-c"]


def restore_from_backup(file, backup):
    state = json.loads(read_bytes(backup).decode("utf-8"))
    write_bytes(file, base64.b64decode(state["original"]))
    if sha256(read_bytes(file)) != state["originalSha256"]:
        raise RuntimeError("restored %s but its hash does not match the backup; the backup is kept at %s"
                           % (file, backup))
    os.remove(backup)


def _parse(argv):
    parser = _Parser(prog="safe_edit.py", add_help=False)
    parser.add_argument("--path", required=True)
    parser.add_argument("--find")
    parser.add_argument("--replace")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--run")
    parser.add_argument("--expect-ran", dest="expect_ran")
    parser.add_argument("--working-directory", dest="working_directory")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args(argv)
    if args.restore:
        if any(value is not None for value in (args.find, args.replace, args.run, args.expect_ran,
                                              args.working_directory)) or args.count != 1:
            raise Refused("usage: --restore takes only --path")
    else:
        if args.find is None or args.replace is None:
            raise Refused("usage: --find and --replace are required (--replace may be empty)")
        if not 1 <= args.count <= 10000:
            raise Refused("usage: --count must be between 1 and 10000")
    return args


def _settle_stale_backup(file, backup):
    """A backup left by a killed run: restore it only while the file is exactly as that run left it."""
    state = json.loads(read_bytes(backup).decode("utf-8"))
    current = sha256(read_bytes(file))
    if current == state["originalSha256"]:
        os.remove(backup)
        say("safe-edit: removed a stale backup; %s already holds its original content." % file)
    elif current == state["mutatedSha256"]:
        restore_from_backup(file, backup)
        say("safe-edit: restored %s from a backup left by an earlier, interrupted run." % file)
    else:
        raise Refused("%s exists, but %s changed after that run. Compare the two, put the "
                      "right content back by hand, then delete the backup." % (backup, file))


def _match_line_endings(text, find, replace):
    crlf = len(re.findall("\r\n", text))
    lf = len(re.findall("(?<!\r)\n", text))
    if crlf > 0 and lf == 0:
        find = find.replace("\r\n", "\n").replace("\n", "\r\n")
        replace = replace.replace("\r\n", "\n").replace("\n", "\r\n")
    elif lf > 0 and crlf == 0:
        find = find.replace("\r\n", "\n")
        replace = replace.replace("\r\n", "\n")
    return find, replace


def _run_command(args, shell, captured):
    cwd = args.working_directory or None
    proc = subprocess.Popen(shell + [args.run], cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        captured.append(line)
        say(line)
    proc.stdout.close()
    return proc.wait()


def _main(argv):
    args = _parse(argv)
    if not os.path.isfile(args.path):
        raise Refused("no such file: %s" % args.path)
    file = os.path.abspath(args.path)
    backup = file + BACKUP_SUFFIX
    if os.path.exists(backup):
        _settle_stale_backup(file, backup)
    if args.restore:
        say("safe-edit: nothing else to restore for %s." % file)
        return 0

    if args.find == args.replace:
        raise Refused("-Find and -Replace are the same; that is no mutation.")
    if args.find == "":
        raise Refused("-Find is empty.")
    # Without a command the edit would be kept, exit 0: a red run that never ran.
    if args.run is not None and not args.run:
        raise Refused("-Run is empty.")
    if args.expect_ran is not None and not args.run:
        raise Refused("-ExpectRan needs -Run.")
    if args.run and not args.expect_ran:
        raise Refused("-Run needs -ExpectRan '<regex>' matching a line only a test run prints (e.g. '# tests "
                      "[1-9]'); without it a command that ran no test counts as red.")

    original = read_bytes(file)
    has_bom = original.startswith(BOM)
    body = original[len(BOM):] if has_bom else original
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("%s is not valid UTF-8; edit it with the Edit tool instead." % file)

    find, replace = _match_line_endings(text, args.find, args.replace)
    found = text.count(find)
    if found != args.count:
        raise Refused("expected %d match(es) of -Find in %s, found %d; nothing changed." % (args.count, file, found))
    mutated = (BOM if has_bom else b"") + text.replace(find, replace).encode("utf-8")

    if not args.run:
        write_bytes(file, mutated)
        say("safe-edit: replaced %d match(es) in %s (line endings and BOM kept)." % (found, file))
        return 0

    shell = platform_shell()
    state = {
        "file": file,
        "original": base64.b64encode(original).decode("ascii"),
        "originalSha256": sha256(original),
        "mutatedSha256": sha256(mutated),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "shell": shell,
    }
    write_bytes(backup, json.dumps(state, indent=2).encode("utf-8"))

    exit_code = None
    captured = []
    try:
        write_bytes(file, mutated)
        if sha256(read_bytes(file)) != state["mutatedSha256"]:
            raise RuntimeError("the mutated content did not land in %s" % file)
        say("safe-edit: mutation applied (%d match(es)) to %s; running: %s" % (found, file, args.run))
        exit_code = _run_command(args, shell, captured)
    finally:
        restore_from_backup(file, backup)
        say("safe-edit: restored %s (hash checked)." % file)

    if not any(re.search(args.expect_ran, line, re.IGNORECASE) for line in captured):
        say("safe-edit: NO EVIDENCE — the output never matched -ExpectRan '%s'; maybe no test ran (exit code %s)."
            % (args.expect_ran, exit_code))
        return 3
    if exit_code == 0:
        say("safe-edit: NOT RED — the command passed with the mutation in place; the check did not catch it.")
        return 4
    say("safe-edit: RED as expected — the command exited %s with the mutation in place." % exit_code)
    return 0


def main(argv=None):
    try:
        return _main(sys.argv[1:] if argv is None else argv)
    except Refused as exc:
        say("safe-edit: REFUSED — %s" % exc)
        return 2
    except Exception as exc:  # the script itself failed
        say("safe-edit: FAILED — %s: %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
