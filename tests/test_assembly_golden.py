"""Golden test: with no config file, the assembler prints what release 0.4.0's rules handlers print.

The baseline runs the frozen handlers of tests/fixtures/golden-34e7713 through a real sh; the new side
runs the working tree's assemble.py, discovering its modules from a temp config dir as a session
does. Both trees sit at the same paths under a folder named `rigger p1 zażółć`, so root lines can be
compared as they are printed. The measure is plan decision D1 (see golden_baseline.py).

Each scenario is one test method; `# scenarios N` names how many there are.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True

import golden_baseline as gb  # noqa: E402

WORK_PLUGINS = gb.ROOT / "plugins"
MARKET = "rigger"
WINDOWS_MODULES = ("rigger-core", "rigger-rules", "rigger-lessons", "rigger-talk", "rigger-wp", "rigger-windows",
                   "rigger-panel")
SESSION_PARTS = 6
SUBAGENT_PARTS = 4
SKIPPED_AGENTS = ("Explore", "Plan", "rigger-core:implementer", "rigger-core:mapper")
TALK_LANGUAGE = "Polski, zażółć gęślą jaźń"
TALK_ENV = {"CLAUDE_PLUGIN_OPTION_REPLY_LANGUAGE": TALK_LANGUAGE, "CLAUDE_PLUGIN_OPTION_REPLY_LINES": "7",
            "CLAUDE_PLUGIN_OPTION_REPORT_LINES": "20"}
# As spike S2 found the store: numbers as JSON numbers, under <plugin>@<marketplace>.
TALK_STORE = {"reply_language": TALK_LANGUAGE, "reply_lines": 7, "report_lines": 20}
GIT_HEADING = "## Git (procedure and naming: `rigger-core:git-workflow`)"
OVERRIDE_NOTICE = b"rigger: rules.d/tests.md replaces rule tests.tests\n"
MINE = b"## Tests\n- mine\n"


@dataclass(frozen=True)
class Scenario:
    id: str
    event: str = "SessionStart"
    source: Optional[str] = "startup"
    agent: Optional[str] = None
    layout: str = "windows"  # or "macos": without rigger-windows
    eol: str = "lf"
    rules_d: str = ""  # "", "hostile", "main_budget" or "override"
    drop: str = ""
    talk: bool = False
    discovery: str = "installed"  # or "dirs": RIGGER_MODULE_DIRS with an empty installed_plugins.json
    mark_personal: bool = True

    @property
    def subagent(self) -> bool:
        return self.event == "SubagentStart"

    def modules(self) -> List[str]:
        return [m for m in WINDOWS_MODULES if not (self.layout == "macos" and m == "rigger-windows")]

    def plugins_with_rules(self) -> List[str]:
        return [m for m in gb.PLUGINS_WITH_RULES if m in self.modules()]

    def baseline_key(self) -> tuple:
        return (self.event, self.source, self.agent, self.layout, self.eol, self.rules_d, self.drop, self.talk)


def scenario_list() -> List[Scenario]:
    events = [("startup", "SessionStart", "startup", None), ("clear", "SessionStart", "clear", None),
              ("compact", "SessionStart", "compact", None), ("resume", "SessionStart", "resume", None)]
    events += [("sub_" + agent.replace(":", "_").replace("-", "_").lower(), "SubagentStart", None, agent)
               for agent in ("general-purpose", "rigger-core:worker") + SKIPPED_AGENTS]
    result = [Scenario("base_" + name, event=event, source=source, agent=agent) for name, event, source, agent in events]
    axes = [("macos", {"layout": "macos"}), ("crlf", {"eol": "crlf"}), ("rules_d", {"rules_d": "hostile"}),
            ("drop_basename", {"drop": "tests.md"}), ("drop_heading", {"drop": "## Tests"}),
            ("drop_git_heading", {"drop": GIT_HEADING}), ("drop_windows", {"drop": "windows.md"}),
            ("talk", {"talk": True}), ("module_dirs", {"discovery": "dirs"})]
    for name, change in axes:
        result.append(Scenario(name + "_startup", **change))
        result.append(Scenario(name + "_sub", event="SubagentStart", source=None, agent="general-purpose", **change))
    result += [
        Scenario("crlf_rules_d_startup", eol="crlf", rules_d="hostile"),
        Scenario("crlf_rules_d_sub", event="SubagentStart", source=None, agent="general-purpose", eol="crlf",
                 rules_d="hostile"),
        Scenario("drop_heading_compact", source="compact", drop="## Tests"),
        Scenario("macos_talk_startup", layout="macos", talk=True),
        # The subagent budget leaves *-main.md files out before counting: c.md fits only there.
        Scenario("main_budget_startup", rules_d="main_budget"),
        Scenario("main_budget_sub", event="SubagentStart", source=None, agent="general-purpose",
                 rules_d="main_budget"),
    ]
    return result


SCENARIOS = scenario_list()
OVERRIDE = Scenario("override_startup", rules_d="override", mark_personal=False)
OVERRIDE_SUB = replace(OVERRIDE, id="override_sub", event="SubagentStart", source=None, agent="general-purpose")
LONE_ROOT = Scenario("lone_root_startup", drop="## Talking to me")
NAMED = [OVERRIDE, OVERRIDE_SUB, LONE_ROOT]


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def hostile_rules_d(folder: Path) -> None:
    """The plan's hostile set; f.md (9,500 bytes) goes over the cap, and g.md after it must go too."""
    write(folder / "a.md", b"## Alpha zaz\xc3\xb3\xc5\x82\xc4\x87\r\nline one\x01 with a control\r\n\ttabbed\r\n")
    write(folder / "b-main.md", b"## Main only\nmain words, no final newline")
    write(folder / "c.md", b"## Placeholders\n{{slot:testing}} and {{reply_language}}\n\n\n")
    write(folder / "d.md", b"")
    (folder / "e.md").mkdir(parents=True)
    write(folder / "f.md", b"## Big\n" + b"x" * 9492 + b"\n")
    write(folder / "g.md", b"## G\ng123\n")


def to_crlf(tree: Path) -> None:
    for path in tree.glob("*/rules/*.md"):
        write(path, path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def child_env(home: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
    env["HOME"] = str(home)
    return env


class Fixture:
    """The temp layout, every scenario's baseline (phase 1, frozen tree) and the working tree in its place."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="golden-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.project = self.tmp / "project"
        self.project.mkdir()
        self.trees = {eol: self.tmp / eol / "rigger p1 zażółć" / "cache" / MARKET for eol in ("lf", "crlf")}
        self.baselines = {}  # type: Dict[tuple, Dict[str, List[bytes]]]
        self.crlf_md = {}  # type: Dict[str, Tuple[int, int]]
        for eol, tree in self.trees.items():
            for plugin in gb.PLUGINS_WITH_RULES:
                shutil.copytree(str(gb.FIXTURE / "plugins" / plugin), str(tree / plugin))
            if eol == "crlf":
                to_crlf(tree)
        self.crlf_md["frozen"] = self._crlf_counts(self.trees["crlf"])
        started = time.time()
        for scenario in SCENARIOS + NAMED:
            key = scenario.baseline_key()
            if key not in self.baselines:
                self.baselines[key] = self._baseline(scenario)
        self.baseline_seconds = time.time() - started
        for eol, tree in self.trees.items():
            shutil.rmtree(str(tree))
            for plugin in WINDOWS_MODULES:
                shutil.copytree(str(WORK_PLUGINS / plugin), str(tree / plugin),
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if eol == "crlf":
                to_crlf(tree)
        self.crlf_md["work"] = self._crlf_counts(self.trees["crlf"])

    @staticmethod
    def _crlf_counts(tree: Path) -> Tuple[int, int]:
        """(rules .md files holding CRLF, .sh files holding CR) in a tree."""
        md = sum(1 for p in tree.glob("*/rules/*.md") if b"\r\n" in p.read_bytes())
        sh = sum(1 for p in tree.rglob("*.sh") if b"\r" in p.read_bytes())
        return md, sh

    def config_dir(self, scenario: Scenario, side: str) -> Path:
        folder = self.tmp / "config" / side / scenario.id
        if scenario.rules_d == "hostile":
            hostile_rules_d(folder / "rigger" / "rules.d")
        elif scenario.rules_d == "override":
            write(folder / "rigger" / "rules.d" / "tests.md", MINE)
        elif scenario.rules_d == "main_budget":
            write(folder / "rigger" / "rules.d" / "b-main.md", b"## Main budget\n" + b"m" * 5985 + b"\n")
            write(folder / "rigger" / "rules.d" / "c.md", b"## After main\n" + b"c" * 3986 + b"\n")
        return folder

    def _baseline(self, scenario: Scenario) -> Dict[str, List[bytes]]:
        env = child_env(self.home)
        env["CLAUDE_CONFIG_DIR"] = str(self.config_dir(scenario, "baseline-%d" % len(self.baselines)))
        if scenario.drop:
            env["HARNESS_RULES_DROP"] = scenario.drop
        if scenario.talk:
            env.update(TALK_ENV)
        return gb.run_baseline(self.trees[scenario.eol], scenario.plugins_with_rules(), scenario.event,
                               scenario.source, scenario.agent, env, cwd=self.project)

    def tree(self, scenario: Scenario) -> Path:
        return self.trees[scenario.eol]

    def root_names(self, scenario: Scenario) -> Dict[str, str]:
        return {"Plugin root: " + gb.root_text(self.tree(scenario) / m): m for m in scenario.modules()}

    def new_config(self, scenario: Scenario) -> Path:
        """A config dir as a session sees it: installed and enabled plugins, the talk store, rules.d."""
        config = self.config_dir(scenario, "new")
        tree = self.tree(scenario)
        installed = {} if scenario.discovery == "dirs" else {
            "%s@%s" % (m, MARKET): [{"scope": "user", "installPath": str(tree / m), "version": "0.4.0"}]
            for m in scenario.modules()}
        write(config / "plugins" / "installed_plugins.json",
              json.dumps({"version": 2, "plugins": installed}).encode("utf-8"))
        settings = {"enabledPlugins": {"%s@%s" % (m, MARKET): True for m in scenario.modules()}}
        if scenario.talk:
            market = "inline" if scenario.discovery == "dirs" else MARKET
            settings["pluginConfigs"] = {"rigger-talk@" + market: {"options": TALK_STORE}}
        write(config / "settings.json", json.dumps(settings).encode("utf-8"))
        return config

    def new_env(self, scenario: Scenario, config: Path) -> Dict[str, str]:
        env = child_env(self.home)
        env["CLAUDE_CONFIG_DIR"] = str(config)
        env["CLAUDE_PLUGIN_ROOT"] = gb.root_text(self.tree(scenario) / "rigger-core")
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        if scenario.drop:
            env["HARNESS_RULES_DROP"] = scenario.drop
        if scenario.discovery == "dirs":
            env["RIGGER_MODULE_DIRS"] = os.pathsep.join(str(self.tree(scenario) / m) for m in scenario.modules())
        return env

    def assembler(self, scenario: Scenario, env: Dict[str, str], args: List[str], stdin: bytes = b"") -> \
            Tuple[int, bytes]:
        script = self.tree(scenario) / "rigger-core" / "tools" / "assemble" / "assemble.py"
        done = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(script)] + args, input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(self.project),
                              timeout=60)
        if done.stderr:
            raise AssertionError("assemble.py %s wrote to stderr: %r" % (args, done.stderr[:2000]))
        return done.returncode, done.stdout

    def run_new(self, scenario: Scenario) -> Tuple[bytes, bytes, Path]:
        """(--render output, the hook parts joined, config dir) of the working tree's assembler."""
        config = self.new_config(scenario)
        env = self.new_env(scenario, config)
        session = "golden-" + scenario.id.replace("_", "-")
        startup = gb.hook_input("SessionStart", "startup", None, session, self.project)
        if scenario.source == "compact" or scenario.subagent:
            # The session started first: part 1 on startup writes the snapshot compact and subagents read.
            code, _ = self.assembler(scenario, env, ["--part", "1"], startup)
            if code != 0:
                raise AssertionError("startup part 1 exited %d" % code)
        if scenario.source == "compact":
            render_args = ["--render", "--source", "compact", "--session-id", session]
        elif scenario.subagent:
            render_args = ["--render", "--subagent", "--agent-type", scenario.agent]
        else:
            render_args = ["--render", "--source", scenario.source]
        if scenario.mark_personal and scenario.source != "compact":
            render_args.append("--mark-personal")
        code, rendered = self.assembler(scenario, env, render_args)
        if code != 0:
            raise AssertionError("%s exited %d" % (render_args, code))

        count = SUBAGENT_PARTS if scenario.subagent else SESSION_PARTS
        stdin = gb.hook_input(scenario.event, scenario.source, scenario.agent, session, self.project)
        flag = ["--subagent"] if scenario.subagent else []
        with ThreadPoolExecutor(max_workers=count) as pool:
            parts = list(pool.map(lambda i: self.assembler(scenario, env, ["--part", str(i)] + flag, stdin),
                                  range(1, count + 1)))
        joined = b""
        for index, (code, out) in enumerate(parts, 1):
            if code != (0 if out else 10):
                raise AssertionError("--part %d exited %d with %d bytes" % (index, code, len(out)))
            joined += gb.decode_subagent(out) if scenario.subagent else out
        return rendered, joined, config


FIXTURE = None  # type: Optional[Fixture]


def setUpModule():
    global FIXTURE
    FIXTURE = Fixture()


def tearDownModule():
    if FIXTURE is not None:
        shutil.rmtree(str(FIXTURE.tmp), True)


def keys(pieces: List[Tuple[str, bytes]]) -> List[str]:
    return [key for key, _ in pieces]


class GoldenTest(unittest.TestCase):
    maxDiff = None

    def compare(self, scenario: Scenario):
        baseline = FIXTURE.baselines[scenario.baseline_key()]
        old_roots, old = gb.normalize(baseline, scenario.subagent)
        rendered, joined, config = FIXTURE.run_new(scenario)
        names = FIXTURE.root_names(scenario)
        new_roots, new = gb.normalize_render(rendered, names)
        self.assertNotIn(b"\r", rendered + joined, "CR in the assembler's output")
        self.check_preconditions(scenario, baseline, old_roots, old, config)
        self.assertEqual(new_roots, old_roots, "root-line sets differ")
        self.assertEqual(new, old, "module pieces differ")
        if not scenario.rules_d:
            # The hook path: every part as Claude Code runs it, joined (personal text carries no marker).
            part_roots, from_parts = gb.normalize_render(joined, names)
            self.assertEqual(part_roots, old_roots, "root-line sets of the hook parts differ")
            self.assertEqual(from_parts, old, "module pieces of the hook parts differ")

    def check_preconditions(self, scenario, baseline, old_roots, old, config):
        """Each axis really applied to the baseline, so equality cannot pass on two empty sides."""
        quiet = scenario.source == "resume" or scenario.agent in SKIPPED_AGENTS
        if quiet:
            self.assertEqual(old, {})
            self.assertEqual(old_roots, set())
            self.assertTrue(all(out == b"" for outs in baseline.values() for out in outs))
            return
        self.assertIn("rigger-rules", old)
        rules = keys(old["rigger-rules"])
        if scenario.subagent:
            self.assertNotIn("rigger-core", old, "state-file.md is main-only")
            self.assertNotIn("## Working environment", rules)
        else:
            self.assertIn("## Working environment", rules)
            self.assertIn("rigger-core", old)
        if scenario.layout == "macos":
            self.assertNotIn("rigger-windows", old)
        else:
            self.assertIn("rigger-windows", old)
        if scenario.eol == "crlf":
            self.assertEqual(FIXTURE.crlf_md["frozen"], (19, 0))
            self.assertEqual(FIXTURE.crlf_md["work"], (19, 0))
        if scenario.rules_d == "hostile":
            personal = b"".join(v for _, v in old["personal"])
            self.assertIn(b"rules_extra: cannot read e.md\n", personal)
            self.assertIn(b"rules_extra: left out over the hook cap: f.md, g.md\n", personal)
            self.assertIn(b"{{slot:testing}} and {{reply_language}}\n", personal)
            self.assertNotIn(b"\x01", personal)
            self.assertEqual(b"main words" in personal, not scenario.subagent)
        elif scenario.rules_d == "main_budget":
            personal = b"".join(v for _, v in old["personal"])
            if scenario.subagent:
                self.assertEqual(keys(old["personal"]), ["## After main"])
            else:
                self.assertEqual(keys(old["personal"]), ["## Main budget"])
                self.assertTrue(personal.endswith(b"rules_extra: left out over the hook cap: c.md\n"))
        else:
            self.assertNotIn("personal", old)
        if scenario.drop in ("tests.md", "## Tests"):
            self.assertNotIn("## Tests", rules)
        if scenario.drop == GIT_HEADING:
            self.assertNotIn(GIT_HEADING, rules)
            self.assertIn("## Tests", rules)
        if scenario.drop == "windows.md":
            self.assertEqual(keys(old["rigger-windows"]), ["## Windows skills, loaded before the first step of their kind"])
        elif scenario.layout == "windows":
            self.assertIn("## Windows shell", keys(old["rigger-windows"]))
        if not scenario.drop:
            self.assertIn("## Tests", rules)
            self.assertIn(GIT_HEADING, rules)
        if scenario.talk and not scenario.subagent:
            talk = b"".join(v for _, v in old["rigger-talk"])
            self.assertIn(TALK_LANGUAGE.encode("utf-8"), talk)
            self.assertIn(b"7", talk)
        if scenario.source == "compact" or scenario.subagent:
            session = "golden-" + scenario.id.replace("_", "-")
            self.assertTrue((config / "rigger" / "state" / (session + ".resolved.json")).is_file(),
                            "startup part 1 wrote no snapshot")
        if scenario.discovery == "dirs":
            data = json.loads((config / "plugins" / "installed_plugins.json").read_bytes().decode("utf-8"))
            self.assertEqual(data["plugins"], {})

    # --- named tests ---------------------------------------------------------------------------

    def test_baseline_ran_every_handler(self):
        """Control: the windows startup baseline holds 19 handler outputs; rigger-rules' 12 share one root line."""
        baseline = FIXTURE.baselines[SCENARIOS[0].baseline_key()]
        self.assertEqual(sum(len(outs) for outs in baseline.values()), 19)
        firsts = [out.split(b"\n", 1)[0] for out in baseline["rigger-rules"]]
        self.assertEqual(len(firsts), 12)
        self.assertEqual(len(set(firsts)), 1)
        self.assertTrue(firsts[0].startswith(b"Plugin root: ") and firsts[0].endswith(b"/rigger-rules"))
        self.assertIn("rigger p1 zażółć".encode("utf-8"), firsts[0])

    def test_accidental_override_is_the_only_difference(self):
        """rules.d/tests.md replacing tests.tests: the piece moves into rigger-rules, and part 1 starts with a notice."""
        for scenario in (OVERRIDE, OVERRIDE_SUB):
            with self.subTest(scenario.id):
                old_roots, old = gb.normalize(FIXTURE.baselines[scenario.baseline_key()], scenario.subagent)
                self.assertEqual(old["personal"], [("## Tests", MINE)])
                expected = {m: list(p) for m, p in old.items() if m != "personal"}
                at = keys(expected["rigger-rules"]).index("## Tests")
                expected["rigger-rules"][at] = ("## Tests", MINE)
                expected[gb.HEAD] = [(gb.PREAMBLE, OVERRIDE_NOTICE)]
                rendered, joined, _ = FIXTURE.run_new(scenario)
                names = FIXTURE.root_names(scenario)
                new_roots, new = gb.normalize_render(rendered, names)
                self.assertEqual(new_roots, old_roots)
                self.assertEqual(new, expected)
                self.assertTrue(joined.startswith(OVERRIDE_NOTICE), joined[:200])
                self.assertEqual(gb.normalize_render(joined, names), (old_roots, expected))

    def test_lone_root_line_is_the_only_root_difference(self):
        """D1: a handler whose every section is dropped prints a lone root line today; the assembler prints none."""
        old_roots, old = gb.normalize(FIXTURE.baselines[LONE_ROOT.baseline_key()], False)
        rendered, joined, _ = FIXTURE.run_new(LONE_ROOT)
        names = FIXTURE.root_names(LONE_ROOT)
        new_roots, new = gb.normalize_render(rendered, names)
        talk_root = "Plugin root: " + gb.root_text(FIXTURE.tree(LONE_ROOT) / "rigger-talk")
        self.assertIn(talk_root, old_roots)
        self.assertNotIn("rigger-talk", old)
        self.assertEqual(old_roots - new_roots, {talk_root})
        self.assertEqual(new_roots - old_roots, set())
        self.assertEqual(new, old)
        self.assertEqual(gb.normalize_render(joined, names), (new_roots, old))


def _make(scenario: Scenario):
    def test(self):
        self.compare(scenario)
    test.__doc__ = "golden scenario %s" % scenario.id
    return test


for _scenario in SCENARIOS:
    setattr(GoldenTest, "test_scenario_" + _scenario.id, _make(_scenario))


if __name__ == "__main__":
    print("# scenarios %d, named tests 3" % len(SCENARIOS))
    sys.stdout.flush()
    started = time.time()
    try:
        program = unittest.main(verbosity=2, exit=False)
    finally:
        print("# golden wall time %.1f s" % (time.time() - started))
    sys.exit(0 if program.result.wasSuccessful() else 1)
