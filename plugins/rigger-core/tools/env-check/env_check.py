"""Checks `.claude/environments.json` as rigger-panel's `parseEnvironments` (hooks/envs.ts) does, with the
same reasons; the two are held together by the cases in rigger-panel/tests/env-cases.ts.

  env_check.py <file>   exit 0 silent when valid; exit 1 printing the reason line
  env_check.py --hook   PostToolUse event on stdin; for a Write/Edit of .claude/environments.json the
                        panel would refuse, prints {"decision": "block", "reason": ...}; otherwise nothing

Where Python differs from JavaScript the port follows JavaScript: `$` would accept a trailing newline
(fullmatch instead), `\\s` is the JavaScript set, keys are walked in JavaScript object order, NaN and
Infinity are not JSON, and true is not 1.
"""

import json
import os
import re
import sys

FIRST = ("local", "dev", "prod")
FIELDS = ("url", "admin", "server", "account", "role", "path", "branch", "database", "deploy", "notes")
SPACE_CODES = (0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029, 0x202F,
               0x205F, 0x3000, 0xFEFF)
JS_SPACE = "".join(re.escape(chr(code)) for code in SPACE_CODES)
BOM = chr(0xFEFF)
SECRET_NAME = re.compile(r"pass(?:wd|word)?|token|secret|key", re.I)
CREDENTIAL_URL = re.compile(r"://[^/@:%s]+:[^/@%s]*@" % (JS_SPACE, JS_SPACE))
WEB_URL = re.compile(r"https?://[^%s]+" % JS_SPACE, re.I)
ARRAY_INDEX = re.compile(r"0|[1-9][0-9]{0,9}")
MAX_INDEX = 2 ** 32 - 2


def js_keys(record):
    """Keys in JavaScript property order: array indices ascending, then the rest as written."""
    indices = sorted((key for key in record if ARRAY_INDEX.fullmatch(key) and int(key) <= MAX_INDEX), key=int)
    return indices + [key for key in record if key not in indices]


def find_secret(value, path):
    if isinstance(value, str):
        return "value of field %s" % (path or "(root)") if CREDENTIAL_URL.search(value) else None
    if isinstance(value, list):
        for index, item in enumerate(value):
            hit = find_secret(item, "%s[%d]" % (path, index))
            if hit:
                return hit
        return None
    if isinstance(value, dict):
        for name in js_keys(value):
            here = "%s.%s" % (path, name) if path else name
            if SECRET_NAME.search(name):
                return "field " + here
            hit = find_secret(value[name], here)
            if hit:
                return hit
    return None


def reject_constant(name):
    raise ValueError("%s is not JSON" % name)


def check(text):
    """The reason the panel would give for this file's text, or None when it draws."""
    try:
        # parse_int: every JavaScript number is a double, with no digit limit.
        doc = json.loads(text[1:] if text.startswith(BOM) else text, parse_constant=reject_constant, parse_int=float)
    except ValueError as error:
        return "invalid JSON (%s)" % error
    secret = find_secret(doc, "")
    if secret:
        return "file refused, it looks like a secret: %s. Keep passwords, tokens and keys out of this file" % secret
    if not isinstance(doc, dict):
        return "wrong shape: expected an object"
    version = doc.get("version")
    if type(version) not in (int, float) or version != 1:
        return 'wrong shape: "version" must be 1'
    if "project" in doc and not isinstance(doc["project"], str):
        return 'wrong shape: "project" must be a string'
    environments = doc.get("environments")
    if not isinstance(environments, dict):
        return 'wrong shape: no "environments" object'
    if "repository" in doc and not (isinstance(doc["repository"], str) and WEB_URL.fullmatch(doc["repository"])):
        return 'wrong shape: "repository" must be an http(s) URL'
    names = js_keys(environments)
    for name in [name for name in FIRST if name in environments] + [name for name in names if name not in FIRST]:
        raw = environments[name]
        if not isinstance(raw, dict):
            return 'wrong shape: environment "%s" must be an object' % name
        for field in FIELDS:
            if field not in raw:
                continue
            value = raw[field]
            if not isinstance(value, str):
                return 'wrong shape: "%s.%s" must be a string' % (name, field)
            if field in ("url", "admin") and not WEB_URL.fullmatch(value):
                return 'wrong shape: "%s.%s" must be an http(s) URL' % (name, field)
    return None


def read_text(path):
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", errors="replace")


def hook():
    """Prints the block decision for a refused .claude/environments.json; anything else stays silent."""
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        file_path = event["tool_input"]["file_path"]
        path = os.path.join(event["cwd"], file_path)
        if os.path.basename(path) != "environments.json" or os.path.basename(os.path.dirname(path)) != ".claude":
            return 0
        reason = check(read_text(path))
    # RecursionError: nesting Python cannot parse but JavaScript can; never report a file that may draw.
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        return 0
    if reason:
        text = '%s: the Environments pane will show "Environments unavailable: %s". Fix the file and write it again.' % (file_path, reason)
        # ASCII escapes: a lone surrogate from the file cannot be encoded as UTF-8.
        sys.stdout.buffer.write(json.dumps({"decision": "block", "reason": text}).encode("ascii") + b"\n")
    return 0


def main(argv):
    if argv == ["--hook"]:
        return hook()
    if len(argv) != 1:
        sys.stderr.write("usage: env_check.py <file> | --hook\n")
        return 2
    try:
        reason = check(read_text(argv[0]))
    except OSError as error:
        reason = "cannot read the file (%s)" % error.strerror
    except RecursionError:
        sys.stderr.write("env_check: cannot check %s: nested deeper than Python parses\n" % argv[0])
        return 2
    if reason:
        sys.stdout.buffer.write(reason.encode("utf-8", "backslashreplace") + b"\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
