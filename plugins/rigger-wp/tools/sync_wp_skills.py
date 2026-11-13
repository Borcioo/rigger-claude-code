"""Mirror the upstream WordPress agent skills into the rigger-wp router and show what changed.

The upstream skills live under skills/wordpress-router/references/skills/<name>/ and are read on
demand through the router; only the router is listed at session start. Default is a dry run: it
prints the file summary and a diff and writes nothing. --apply writes the tree, deletes skills
upstream removed (listed first) and regenerates the router SKILL.md index.

    python -I sync_wp_skills.py                      # dry run against the default upstream
    python -I sync_wp_skills.py --apply              # write
    python -I sync_wp_skills.py --source <dir>       # a local checkout instead of a clone

Exit codes: 0 up to date (or applied), 10 changes pending (dry run), 2 refused (nothing written).
"""

import argparse
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "https://github.com/WordPress/agent-skills.git"
START = "<!-- wp-skills-index:start -->"
END = "<!-- wp-skills-index:end -->"
DESC_MAX = 140
DIFF_LINES = 40

ROUTER = """---
name: wordpress-router
description: "Use first for any WordPress task (plugin, theme, block, REST API, WP-CLI, performance, Playground, wp-env): it classifies the repo and indexes the WordPress skills to read on demand."
---

# WordPress router

The WordPress skills are not listed at session start. This file indexes them; read the ones the
task needs, not all.

1. Classify the repo: read `${CLAUDE_SKILL_DIR}/references/skills/wordpress-router/SKILL.md` and
   its decision tree, then run the triage script it names.
2. Pick the skills below that match the task and Read each `SKILL.md`.
3. Paths such as `skills/<name>/scripts/x.mjs` inside those files mean
   `${CLAUDE_SKILL_DIR}/references/skills/<name>/scripts/x.mjs`; run scripts from the project root.

""" + START + """
{index}
""" + END + """

Source and licence (GPL-2.0-or-later): `${CLAUDE_SKILL_DIR}/references/UPSTREAM.md`.
"""


class Refused(Exception):
    pass


def frontmatter(text):
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        raise Refused("no frontmatter")
    fm = m.group(1)
    name = re.search(r"(?m)^name:\s*(.+?)\s*$", fm)
    d = re.search(r"(?ms)^description:\s*(.*?)(?=^[\w-]+:|\Z)", fm)
    if not name or not d:
        raise Refused("frontmatter lacks name or description")
    desc = re.sub(r"\s*\n\s*", " ", d.group(1)).strip()
    desc = re.sub(r"^[>|][-+]?\s*", "", desc).strip()
    if len(desc) >= 2 and desc[0] == desc[-1] and desc[0] in "\"'":
        desc = desc[1:-1]
    return name.group(1).strip().strip("\"'"), desc


def short(desc):
    if len(desc) <= DESC_MAX:
        return desc
    cut = desc[:DESC_MAX].rsplit(" ", 1)[0].rstrip(",;:.")
    return cut + "…"


def read_tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file() and ".git" not in p.relative_to(root).parts:
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def fetch(source, ref, tmp):
    if Path(source).is_dir():
        return Path(source).resolve()
    dest = Path(tmp) / "upstream"
    cmd = ["git", "clone", "--depth", "1", "--quiet"]
    if ref:
        cmd += ["--branch", ref]
    r = subprocess.run(cmd + [source, str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refused("git clone failed: " + r.stderr.strip()[-200:])
    return dest


def upstream_stamp(checkout, source):
    git = ["git", "-C", str(checkout)]
    sha = subprocess.run(git + ["rev-parse", "HEAD"], capture_output=True, text=True)
    if sha.returncode != 0:
        return "unversioned", "unversioned"
    date = subprocess.run(git + ["log", "-1", "--format=%cI"], capture_output=True, text=True)
    return sha.stdout.strip(), date.stdout.strip()


def origin_url(checkout):
    r = subprocess.run(["git", "-C", str(checkout), "remote", "get-url", "origin"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def build(checkout, source):
    """The files that should exist under the router folder, as {relative path: bytes}."""
    skills_dir = Path(checkout) / "skills"
    if not skills_dir.is_dir():
        raise Refused("upstream has no skills/ folder")
    skills = [d for d in sorted(skills_dir.iterdir()) if d.is_dir()]
    if not skills:
        raise Refused("upstream skills/ is empty")
    lic = Path(checkout) / "LICENSE"
    if not lic.is_file() or b"General Public License" not in lic.read_bytes():
        raise Refused("upstream LICENSE missing or not a GPL text")
    files = {}
    entries = []
    for d in skills:
        md = d / "SKILL.md"
        if not md.is_file():
            raise Refused("%s has no SKILL.md" % d.name)
        name, desc = frontmatter(md.read_text(encoding="utf-8"))
        if name != d.name:
            raise Refused("%s: frontmatter name is %r" % (d.name, name))
        for rel, data in read_tree(d).items():
            files["references/skills/%s/%s" % (d.name, rel)] = data
        if d.name != "wordpress-router":
            entries.append("- `%s`: %s" % (name, short(desc)))
    files["references/LICENSE"] = lic.read_bytes()
    sha, date = upstream_stamp(checkout, source)
    files["references/UPSTREAM.md"] = (
        "Source: %s\nCommit: %s\nCommit date: %s\n"
        "Licence: GPL-2.0-or-later, Copyright (C) WordPress Contributors (references/LICENSE)\n"
        "Changes by the Rigger maintainers: none to the files under references/skills/ (copied unmodified by\n"
        "tools/sync_wp_skills.py); the router SKILL.md next to this folder and this note are ours.\n"
        % (origin_url(checkout) or (source if not Path(source).is_dir() else "local checkout"), sha, date)
    ).encode("utf-8")
    files["SKILL.md"] = ROUTER.replace("{index}", "\n".join(entries)).encode("utf-8")
    return files, [d.name for d in skills]


def plan(current, wanted):
    added = sorted(set(wanted) - set(current))
    removed = sorted(set(current) - set(wanted))
    changed = sorted(p for p in set(wanted) & set(current) if wanted[p] != current[p])
    return added, changed, removed


def show(current, wanted, added, changed, removed, full):
    skills_gone = sorted({p.split("/")[2] for p in removed if p.startswith("references/skills/")})
    print("files: +%d ~%d -%d" % (len(added), len(changed), len(removed)))
    if skills_gone:
        print("skills removed upstream (deleted on --apply): " + ", ".join(skills_gone))
    for tag, paths in (("+", added), ("-", removed)):
        for p in paths:
            print("%s %s" % (tag, p))
    for p in changed:
        print("~ %s" % p)
        try:
            old = current[p].decode("utf-8").splitlines()
            new = wanted[p].decode("utf-8").splitlines()
        except UnicodeDecodeError:
            print("  (binary)")
            continue
        lines = list(difflib.unified_diff(old, new, "a/" + p, "b/" + p, lineterm="", n=1))
        for line in (lines if full else lines[:DIFF_LINES]):
            print("  " + line)
        if not full and len(lines) > DIFF_LINES:
            print("  ... %d more diff lines (--full)" % (len(lines) - DIFF_LINES))


def apply(dest, current, wanted):
    for p in sorted(set(current) - set(wanted)):
        f = Path(dest) / p
        f.unlink()
        parent = f.parent
        while parent != Path(dest) and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    for p, data in wanted.items():
        if current.get(p) != data:
            f = Path(dest) / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", default=UPSTREAM, help="git URL or a local checkout directory")
    ap.add_argument("--ref", default=None, help="branch or tag to clone (default: the default branch)")
    ap.add_argument("--dest", default=str(Path(__file__).resolve().parents[1] / "skills" / "wordpress-router"))
    ap.add_argument("--apply", action="store_true", help="write the tree (default: dry run)")
    ap.add_argument("--full", action="store_true", help="print whole diffs")
    args = ap.parse_args(argv)
    tmp = tempfile.mkdtemp(prefix="wp-skills-sync-")
    try:
        checkout = fetch(args.source, args.ref, tmp)
        wanted, names = build(checkout, args.source)
        dest = Path(args.dest)
        current = read_tree(dest) if dest.is_dir() else {}
        if current and len(names) * 2 < len({p.split("/")[2] for p in current if p.startswith("references/skills/")}):
            raise Refused("upstream lost more than half of the skills; check by hand")
        added, changed, removed = plan(current, wanted)
        if not (added or changed or removed):
            print("up to date (%d upstream skills)" % len(names))
            return 0
        show(current, wanted, added, changed, removed, args.full)
        if not args.apply:
            print("dry run: nothing written; rerun with --apply")
            return 10
        apply(dest, current, wanted)
        print("applied")
        return 0
    except Refused as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
