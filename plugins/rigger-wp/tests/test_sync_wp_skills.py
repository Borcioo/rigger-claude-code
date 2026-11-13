"""tools/sync_wp_skills.py: dry run by default, a written tree that matches upstream, fail-closed refusals."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "sync_wp_skills.py"
GPL = "Copyright (C) 2026 X\nThis program is free software under the GNU General Public License v2.\n"


def skill(name, desc, body="Body.\n"):
    return '---\nname: %s\ndescription: "%s"\n---\n\n# %s\n\n%s' % (name, desc, name, body)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wp-sync-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.up = self.tmp / "upstream"
        self.dest = self.tmp / "router"
        self.put("LICENSE", GPL)
        self.put("skills/wordpress-router/SKILL.md", skill("wordpress-router", "Routes."))
        self.put("skills/wordpress-router/references/tree.md", "tree\n")
        self.put("skills/wp-alpha/SKILL.md", skill("wp-alpha", "Alpha does alpha things."))
        self.put("skills/wp-alpha/scripts/run.mjs", "console.log(1)\n")
        self.put("skills/wp-beta/SKILL.md", skill("wp-beta", "Beta does beta things."))

    def put(self, rel, text):
        f = self.up / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8", newline="")

    def sync(self, *args):
        p = subprocess.run([sys.executable, "-I", str(SCRIPT), "--source", str(self.up), "--dest", str(self.dest), *args],
                           capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stdout, p.stderr

    def tree(self):
        return {p.relative_to(self.dest).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(self.dest.rglob("*")) if p.is_file()} if self.dest.exists() else {}


class DryRun(Base):
    def test_dry_run_reports_and_writes_nothing(self):
        code, out, _ = self.sync()
        self.assertEqual(code, 10)
        self.assertIn("+ references/skills/wp-alpha/SKILL.md", out)
        self.assertIn("dry run: nothing written", out)
        self.assertFalse(self.dest.exists())

    def test_up_to_date_after_apply(self):
        self.assertEqual(self.sync("--apply")[0], 0)
        code, out, _ = self.sync()
        self.assertEqual(code, 0)
        self.assertIn("up to date (3 upstream skills)", out)

    def test_changed_upstream_file_is_shown_as_a_diff_and_not_written(self):
        self.sync("--apply")
        self.put("skills/wp-alpha/SKILL.md", skill("wp-alpha", "Alpha does alpha things.", "Changed body.\n"))
        before = self.tree()
        code, out, _ = self.sync()
        self.assertEqual(code, 10)
        self.assertIn("~ references/skills/wp-alpha/SKILL.md", out)
        self.assertIn("+Changed body.", out)
        self.assertIn("-Body.", out)
        self.assertEqual(self.tree(), before)


class Apply(Base):
    def test_layout_and_unmodified_copies(self):
        self.assertEqual(self.sync("--apply")[0], 0)
        t = self.tree()
        self.assertEqual(t["references/skills/wp-alpha/SKILL.md"], (self.up / "skills/wp-alpha/SKILL.md").read_text(encoding="utf-8"))
        self.assertEqual(t["references/skills/wp-alpha/scripts/run.mjs"], "console.log(1)\n")
        self.assertEqual(t["references/skills/wordpress-router/references/tree.md"], "tree\n")
        self.assertEqual(t["references/LICENSE"], GPL)
        self.assertIn("GPL-2.0-or-later", t["references/UPSTREAM.md"])
        self.assertIn("Changes by the Rigger maintainers: none to the files under references/skills/", t["references/UPSTREAM.md"])
        self.assertIn("Commit: unversioned", t["references/UPSTREAM.md"])

    def test_router_lists_only_its_own_entry_and_indexes_the_others(self):
        self.sync("--apply")
        router = self.tree()["SKILL.md"]
        head = router.split("---")[1]
        self.assertIn("name: wordpress-router", head)
        index = router.split("<!-- wp-skills-index:start -->")[1].split("<!-- wp-skills-index:end -->")[0]
        self.assertIn("- `wp-alpha`: Alpha does alpha things.", index)
        self.assertIn("- `wp-beta`: Beta does beta things.", index)
        self.assertNotIn("`wordpress-router`", index)
        self.assertNotIn("\n\n", index.strip("\n"))

    def test_long_description_is_cut_in_the_index_not_in_the_copy(self):
        long = "word " * 80
        self.put("skills/wp-beta/SKILL.md", skill("wp-beta", long.strip()))
        self.sync("--apply")
        t = self.tree()
        line = [l for l in t["SKILL.md"].splitlines() if l.startswith("- `wp-beta`")][0]
        self.assertLessEqual(len(line), len("- `wp-beta`: ") + 141)
        self.assertTrue(line.endswith("…"))
        self.assertIn(long.strip(), t["references/skills/wp-beta/SKILL.md"])

    def test_skill_removed_upstream_is_listed_then_deleted_on_apply(self):
        self.sync("--apply")
        shutil.rmtree(self.up / "skills" / "wp-beta")
        code, out, _ = self.sync()
        self.assertEqual(code, 10)
        self.assertIn("skills removed upstream (deleted on --apply): wp-beta", out)
        self.assertIn("references/skills/wp-beta/SKILL.md", self.tree())
        self.assertEqual(self.sync("--apply")[0], 0)
        t = self.tree()
        self.assertNotIn("references/skills/wp-beta/SKILL.md", t)
        self.assertFalse((self.dest / "references" / "skills" / "wp-beta").exists())
        self.assertNotIn("`wp-beta`", t["SKILL.md"])


class Refusals(Base):
    def refused(self, fragment):
        before = self.tree()
        code, _, err = self.sync("--apply")
        self.assertEqual(code, 2)
        self.assertIn("refused", err)
        self.assertIn(fragment, err)
        self.assertEqual(self.tree(), before)

    def test_no_skills(self):
        shutil.rmtree(self.up / "skills")
        (self.up / "skills").mkdir()
        self.refused("empty")

    def test_missing_license(self):
        (self.up / "LICENSE").unlink()
        self.refused("LICENSE")

    def test_license_that_is_not_gpl(self):
        self.put("LICENSE", "All rights reserved.\n")
        self.refused("LICENSE")

    def test_frontmatter_name_differs_from_folder(self):
        self.put("skills/wp-beta/SKILL.md", skill("wp-other", "x"))
        self.refused("wp-beta")

    def test_folder_without_skill_md(self):
        (self.up / "skills" / "wp-beta" / "SKILL.md").unlink()
        self.refused("wp-beta")

    def test_upstream_that_lost_more_than_half_of_the_skills(self):
        for n in range(4, 9):
            self.put("skills/wp-n%d/SKILL.md" % n, skill("wp-n%d" % n, "d"))
        self.sync("--apply")
        for p in list((self.up / "skills").iterdir()):
            if p.name != "wp-alpha":
                shutil.rmtree(p)
        self.refused("more than half")


if __name__ == "__main__":
    unittest.main()
