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
                   "rigger-panel", "rigger-review", "rigger-testing")
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
# An enabled plugin named superpowers with no manifest: it fills the four process slots (D39, D40).
STUB = "superpowers"
STUB_KEY = STUB + "@stub-market"

# --- the allowed differences of plan decision D40, applied to the baseline side -------------------
TABLE_HEADER = b"| Before | Skill |\n|---|---|\n"
SKILLS = "## Skill triggers"
OLD_INTRO = (b"- Load the skill before the first step of its kind; the `superpowers:` rows apply when the\n"
             b"  superpowers plugin is installed:\n")
NEW_INTRO = b"- Load the skill before the first step of its kind:\n"
WINDOWS_TABLE = "## Windows skills, loaded before the first step of their kind"


def _review_row() -> bytes:
    """The review module's one trigger row as the merged table prints it, read from its manifest."""
    data = json.loads((WORK_PLUGINS / "rigger-review" / "rigger-module.json").read_bytes().decode("utf-8"))
    (row,) = data["triggers"]
    return ("| %s | %s |\n" % (row["before"], ", ".join("`%s`" % name for name in row["load"]))).encode("utf-8")


REVIEW_ROW = _review_row()
WP_OLD = "## WordPress skill, loaded before the first step of its kind"
WP_NEW = "## WordPress site copies"
PLANS = "## Plans: main session"
PLANS_OLD = (b"- A brief or full requirements never waive brainstorming or planning (with superpowers installed:\n"
             b"  `superpowers:brainstorming`, then `superpowers:writing-plans`), the thinking pass (goal, reader,\n")
PLANS_NEW = (b"- A brief or full requirements never waive brainstorming or planning (load the brainstorm and\n"
             b"  plans skills the Skill triggers table names), the thinking pass (goal, reader,\n")
STATE = "## State file for long sessions"
STATE_OLD = (gb.ROOT / "tests" / "fixtures" / "golden-34e7713" / "plugins" / "rigger-core" / "rules"
             / "state-file.md").read_bytes().replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"
STATE_NEW = (  # phase 3b D87, written by hand: work items go through the state tool
    b"## State file for long sessions\n"
    b"- **Long sessions keep a state file; they never hand off.** Main session only (subagents never\n"
    b"  write one). From a plan's first task boundary or the context reminder, keep\n"
    b"  `<config dir>/state/<CLAUDE_CODE_SESSION_ID>.md` (`CLAUDE_CONFIG_DIR`, else `~/.claude`):\n"
    b"  `---` / `safe_to_compact: true` / `updated: <ISO time>` / `plan:`, `stop:` when set / `---`,\n"
    b"  then goal, plan file, commits, running agents, `## Next step`, `## Items`, `## Keep verbatim`\n"
    b"  (exact errors, file:line, commands, ids, decisions and why), lessons. Rewrite it at each task\n"
    b"  boundary (commit, green tests, subagent result), refreshing Next step and Keep verbatim,\n"
    b"  with `true`; set `false` before a step that must not be cut (merge, migration).\n"
    b"  Compaction waits for `true`; after it, continue from Next step without\n"
    b"  asking or writing a\n"
    b"  handoff.\n"
    b"- **The state file stays under 9 000 characters,** all that comes back after compaction.\n"
    b"  `## Next step` is the first section: rewrite it, never append to it, at most ~15 lines. History\n"
    b"  goes to `<session id>.log.md`, linked\n"
    b"  from the state file.\n"
    b"- **Change `## Items` only with `<Plugin root>/tools/state/state.py add|set|note|done`**; act\n"
    b"  on stale items named at session start.\n")
SLOT_ROWS = [  # D36, written by hand: every slot at its default, superpowers enabled
    b"| reviewer | agent | rigger-review:reviewer | by-rule | default |\n",
    b"| worker | agent | rigger-core:worker | by-rule | default |\n",
    b"| implementer | agent | rigger-core:implementer | pinned | default |\n",
    b"| mapper | agent | rigger-core:mapper | pinned | default |\n",
    b"| testing | skill | rigger-testing:testing | \xe2\x80\x94 | default |\n",
    b"SHELL-GUARD",
    b"| lessons | hook | rigger-lessons (failure mode: undeclared) | \xe2\x80\x94 | default |\n",
    b"| process.brainstorm | skill | superpowers:brainstorming | \xe2\x80\x94 | default |\n",
    b"| process.plans | skill | superpowers:writing-plans | \xe2\x80\x94 | default |\n",
    b"| process.tdd | skill | superpowers:test-driven-development | \xe2\x80\x94 | default |\n",
    b"| process.debug | skill | superpowers:systematic-debugging | \xe2\x80\x94 | default |\n",
]
SHELL_GUARD_FILLED = b"| shell-guard | hook | rigger-windows (fails open) | \xe2\x80\x94 | default |\n"
SHELL_GUARD_EMPTY = b"| shell-guard | hook | \xe2\x80\x94 | \xe2\x80\x94 | default |\n"


def expected_slots(layout: str) -> bytes:
    """`## Slots` for this system: 11 rows on Windows (shell-guard filled only with rigger-windows), else 10."""
    if os.name != "nt":
        shell_guard = b""
    else:
        shell_guard = SHELL_GUARD_FILLED if layout == "windows" else SHELL_GUARD_EMPTY
    rows = b"".join(shell_guard if line == b"SHELL-GUARD" else line for line in SLOT_ROWS)
    return b"## Slots\n\n| Slot | Kind | Target | Model | From |\n|---|---|---|---|---|\n" + rows


def split_table(piece: bytes) -> Tuple[bytes, List[bytes], bytes]:
    """(text before the table header, the row lines, text after the last row) of a piece with one table."""
    before, _, rest = piece.partition(TABLE_HEADER)
    lines = rest.splitlines(True)
    rows = [line for line in lines if line.startswith(b"| ")]
    return before, rows, b"".join(lines[len(rows):])


def _piece(pieces: Dict[str, List[Tuple[str, bytes]]], module: str, key: str) -> Optional[int]:
    found = [i for i, (k, _) in enumerate(pieces.get(module, [])) if k == key]
    return found[0] if found else None


def _merged_table(scenario, roots, pieces) -> bool:
    """The rigger-rules table moves to rigger-core with the new intro and every row of the three tables (D38)."""
    at = _piece(pieces, "rigger-rules", SKILLS)
    if at is None:
        return False
    _, old = pieces["rigger-rules"].pop(at)
    before, rows, after = split_table(old)
    core = [row for row in rows if b"`superpowers:" not in row]
    slot_rows = [row for row in rows if b"`superpowers:" in row]
    others = []  # type: List[bytes]
    for module, key in (("rigger-windows", WINDOWS_TABLE), ("rigger-wp", WP_OLD)):
        where = _piece(pieces, module, key)
        if where is not None:
            others += split_table(pieces[module][where][1])[1]
    if OLD_INTRO not in before:
        raise AssertionError("the frozen intro moved: %r" % before)
    merged = before.replace(OLD_INTRO, NEW_INTRO) + TABLE_HEADER + b"".join(core + others + slot_rows) + after
    pieces.setdefault("rigger-core", []).append((SKILLS, merged))
    return True


def _review_row_joins(scenario, roots, pieces) -> bool:
    """The review module's trigger row follows rigger-core's own rows in the merged table (phase 3a D56)."""
    at = _piece(pieces, "rigger-core", SKILLS)
    if at is None or "rigger-review" not in scenario.modules():
        return False
    key, text = pieces["rigger-core"][at]
    before, rows, after = split_table(text)
    core = [i for i, row in enumerate(rows) if b"`rigger-core:" in row and b"`superpowers:" not in row]
    if not core:
        raise AssertionError("the merged table holds no rigger-core row: %r" % text[:300])
    rows.insert(core[-1] + 1, REVIEW_ROW)
    pieces["rigger-core"][at] = (key, before + TABLE_HEADER + b"".join(rows) + after)
    return True


def _testing_cell(scenario, roots, pieces) -> bool:
    """The testing slot's cell in the merged table names the testing module's skill (phase 3a D57)."""
    at = _piece(pieces, "rigger-core", SKILLS)
    if at is None or "rigger-testing" not in scenario.modules():
        return False
    key, text = pieces["rigger-core"][at]
    if text.count(b"`rigger-core:testing`") != 1:
        raise AssertionError("the merged table names rigger-core:testing other than once: %r" % text[:300])
    pieces["rigger-core"][at] = (key, text.replace(b"`rigger-core:testing`", b"`rigger-testing:testing`"))
    return True


def _windows_table_goes(scenario, roots, pieces) -> bool:
    at = _piece(pieces, "rigger-windows", WINDOWS_TABLE)
    if at is None:
        return False
    pieces["rigger-windows"].pop(at)
    if not pieces["rigger-windows"]:
        del pieces["rigger-windows"]
        roots.discard("Plugin root: " + gb.root_text(FIXTURE.tree(scenario) / "rigger-windows"))
    return True


def _wp_sentence(scenario, roots, pieces) -> bool:
    at = _piece(pieces, "rigger-wp", WP_OLD)
    if at is None:
        return False
    before, _, after = split_table(pieces["rigger-wp"][at][1])
    heading = WP_OLD.encode("utf-8") + b"\n"
    if not before.startswith(heading) or not before.endswith(b"\n\n"):
        raise AssertionError("the frozen wp piece changed shape: %r" % before)
    pieces["rigger-wp"][at] = (WP_NEW, WP_NEW.encode("utf-8") + b"\n" + before[len(heading):-1] + after)
    return True


def _slots_section(scenario, roots, pieces) -> bool:
    if scenario.subagent or _piece(pieces, "rigger-core", SKILLS) is None:
        return False
    pieces["rigger-core"].append(("## Slots", expected_slots(scenario.layout)))
    return True


def _core_root_for_subagents(scenario, roots, pieces) -> bool:
    if not scenario.subagent or _piece(pieces, "rigger-core", SKILLS) is None:
        return False
    roots.add("Plugin root: " + gb.root_text(FIXTURE.tree(scenario) / "rigger-core"))
    return True


def _plans_wording(scenario, roots, pieces) -> bool:
    at = _piece(pieces, "rigger-rules", PLANS)
    if at is None:
        return False
    key, text = pieces["rigger-rules"][at]
    if PLANS_OLD not in text:
        raise AssertionError("the frozen plans piece changed: %r" % text[:300])
    pieces["rigger-rules"][at] = (key, text.replace(PLANS_OLD, PLANS_NEW))
    return True


LOFI_BULLET = (b"- **Lo-fi means no styling, not no design.** Design each screen from its role's task and decisions\n"
               b"  (what they must do, what they see, what comes next), with the UI that task needs: statuses,\n"
               b"  filters, history, empty and error states.\n")
UI_ROW_OLD = b"| UI components, optimistic updates, "
UI_ROW_NEW = b"| UI components, lo-fi screens, optimistic updates, "


def _lofi_leaves_plans(scenario, roots, pieces) -> bool:
    """The lo-fi trap moves out of ## Plans: main session into the web-ui-traps skill (phase 3b D99)."""
    at = _piece(pieces, "rigger-rules", PLANS)
    if at is None:
        return False
    key, text = pieces["rigger-rules"][at]
    if text.count(LOFI_BULLET) != 1:
        raise AssertionError("the frozen plans piece holds the lo-fi bullet other than once: %r" % text[-400:])
    pieces["rigger-rules"][at] = (key, text.replace(LOFI_BULLET, b""))
    return True


def _ui_row_names_lofi(scenario, roots, pieces) -> bool:
    """The web-ui-traps trigger row names lo-fi screens (phase 3b D99)."""
    at = _piece(pieces, "rigger-core", SKILLS)
    if at is None:
        return False
    key, text = pieces["rigger-core"][at]
    if text.count(UI_ROW_OLD) != 1:
        raise AssertionError("the merged table holds the UI row other than once: %r" % text[:300])
    pieces["rigger-core"][at] = (key, text.replace(UI_ROW_OLD, UI_ROW_NEW))
    return True


def _state_items(scenario, roots, pieces) -> bool:
    at = _piece(pieces, "rigger-core", STATE)
    if at is None:
        return False
    key, text = pieces["rigger-core"][at]
    if text.count(STATE_OLD) != 1:
        raise AssertionError("the frozen state-file piece changed: %r" % text[:300])
    pieces["rigger-core"][at] = (key, text.replace(STATE_OLD, STATE_NEW))
    return True


# D40's fixed list, in the order it is applied; every entry must apply in at least one scenario.
ALLOWED = [
    ("the rigger-rules table moves to rigger-core and merges the three tables", _merged_table),
    ("the reviewer slot names the review module, whose trigger row joins the merged table", _review_row_joins),
    ("the testing slot names the testing module", _testing_cell),
    ("the rigger-windows table section goes", _windows_table_goes),
    ("the rigger-wp section keeps its sentence under a new heading", _wp_sentence),
    ("rigger-core gains ## Slots for the main session", _slots_section),
    ("subagents get rigger-core's root line", _core_root_for_subagents),
    ("## Plans: main session names the table, not the slots", _plans_wording),
    ("the lo-fi trap leaves ## Plans: main session for the web-ui-traps skill", _lofi_leaves_plans),
    ("the web-ui-traps trigger row names lo-fi screens", _ui_row_names_lofi),
    ("the state-file section sends work items through the state tool", _state_items),
]


def apply_allowed(scenario, roots: set, pieces: dict) -> List[str]:
    """Apply ALLOWED in place to a normalised baseline's roots and pieces; return the names of the entries used."""
    used = [name for name, effect in ALLOWED if effect(scenario, roots, pieces)]
    for module in [m for m, p in pieces.items() if not p]:
        del pieces[module]
    return used


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
    stub: bool = True  # the superpowers stub is installed and enabled

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
# Spec section 8.2: a rules.d override inherits the audience of the section it replaces (0.4.0 printed it to both).
LESSONS_MINE = b"## Lessons (mine)\n- my lessons words\n"
STATE_MINE = b"## State file for long sessions (mine)\n- my state words\n"
AUD_SUB = Scenario("aud_sub_startup", rules_d="aud_sub", mark_personal=False)
AUD_SUB_SUB = replace(AUD_SUB, id="aud_sub_sub", event="SubagentStart", source=None, agent="general-purpose")
AUD_MAIN = Scenario("aud_main_startup", rules_d="aud_main", mark_personal=False)
AUD_MAIN_SUB = replace(AUD_MAIN, id="aud_main_sub", event="SubagentStart", source=None, agent="general-purpose")
NAMED = [OVERRIDE, OVERRIDE_SUB, LONE_ROOT, AUD_SUB, AUD_SUB_SUB, AUD_MAIN, AUD_MAIN_SUB]
# Review Focus 7: rules.d files named after the sections the merge moves (skills.md) or removes (windows-skills.md).
MY_TABLE = b"## Skill triggers\n- my own table\n"
MY_WINDOWS = b"## Windows skills, loaded before the first step of their kind\n- my windows words\n"
TABLE_OVERRIDES = Scenario("table_overrides_startup", rules_d="table_overrides")
TABLE_OVERRIDES_SUB = replace(TABLE_OVERRIDES, id="table_overrides_sub", event="SubagentStart", source=None,
                              agent="general-purpose")


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
            write(tree / STUB / ".claude-plugin" / "plugin.json", json.dumps({"name": STUB}).encode("utf-8"))
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
        elif scenario.rules_d == "aud_sub":
            write(folder / "rigger" / "rules.d" / "lessons-subagent.md", LESSONS_MINE)
        elif scenario.rules_d == "aud_main":
            write(folder / "rigger" / "rules.d" / "state-file.md", STATE_MINE)
        elif scenario.rules_d == "table_overrides":
            write(folder / "rigger" / "rules.d" / "skills.md", MY_TABLE)
            write(folder / "rigger" / "rules.d" / "windows-skills.md", MY_WINDOWS)
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
        if scenario.discovery != "dirs" and scenario.stub:
            installed[STUB_KEY] = [{"scope": "user", "installPath": str(tree / STUB), "version": "1.0.0"}]
        write(config / "plugins" / "installed_plugins.json",
              json.dumps({"version": 2, "plugins": installed}).encode("utf-8"))
        settings = {"enabledPlugins": {"%s@%s" % (m, MARKET): True for m in scenario.modules()}}
        if scenario.stub:
            settings["enabledPlugins"][STUB_KEY] = True
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
            dirs = [self.tree(scenario) / m for m in scenario.modules()] + ([self.tree(scenario) / STUB]
                                                                           if scenario.stub else [])
            env["RIGGER_MODULE_DIRS"] = os.pathsep.join(str(path) for path in dirs)
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

    def run_new(self, scenario: Scenario) -> Tuple[bytes, bytes, Path, bytes]:
        """(--render output, the hook parts joined, config dir, --render output without --mark-personal) of the
        working tree's assembler."""
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
        plain = rendered
        if "--mark-personal" in render_args:
            plain_args = [arg for arg in render_args if arg != "--mark-personal"]
            code, plain = self.assembler(scenario, env, plain_args)
            if code != 0:
                raise AssertionError("%s exited %d" % (plain_args, code))

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
        return rendered, joined, config, plain


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
        rendered, joined, config, plain = FIXTURE.run_new(scenario)
        names = FIXTURE.root_names(scenario)
        new_roots, new = gb.normalize_render(rendered, names)
        self.assertNotIn(b"\r", rendered + joined, "CR in the assembler's output")
        self.check_preconditions(scenario, baseline, old_roots, old, config)
        apply_allowed(scenario, old_roots, old)
        self.assertEqual(new_roots, old_roots, "root-line sets differ")
        self.assertEqual(new, old, "module pieces differ")
        if scenario.rules_d:
            # Personal text carries no marker in the parts: they must give the unmarked --render, root lines aside.
            self.assertEqual(gb.without_root_lines(joined), gb.without_root_lines(plain),
                             "hook parts differ from the unmarked --render")
        else:
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
            self.assertEqual(FIXTURE.crlf_md["work"], (18, 0))  # windows-skills.md is gone (D38)
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
                apply_allowed(scenario, old_roots, old)
                expected = {m: list(p) for m, p in old.items() if m != "personal"}
                at = keys(expected["rigger-rules"]).index("## Tests")
                expected["rigger-rules"][at] = ("## Tests", MINE)
                expected[gb.HEAD] = [(gb.PREAMBLE, OVERRIDE_NOTICE)]
                rendered, joined, _, _ = FIXTURE.run_new(scenario)
                names = FIXTURE.root_names(scenario)
                new_roots, new = gb.normalize_render(rendered, names)
                self.assertEqual(new_roots, old_roots)
                self.assertEqual(new, expected)
                self.assertTrue(joined.startswith(OVERRIDE_NOTICE), joined[:200])
                self.assertEqual(gb.normalize_render(joined, names), (old_roots, expected))

    def check_narrowing(self, scenarios, words, notice, reaches, owner):
        """0.4.0 printed the rules.d `words` to both audiences; the assembler prints them to `reaches` only, under
        the replaced section's module `owner`, and the other audience gets the override `notice` instead."""
        for scenario in scenarios:
            _, old = gb.normalize(FIXTURE.baselines[scenario.baseline_key()], scenario.subagent)
            self.assertTrue(any(words in v for _, v in old.get("personal", [])),
                            "%s: 0.4.0 printed no %r" % (scenario.id, words))
        for scenario in scenarios:
            rendered, joined, _, _ = FIXTURE.run_new(scenario)
            audience = "subagent" if scenario.subagent else "session"
            for name, out in (("--render", rendered), ("hook parts", joined)):
                where = "%s, %s" % (scenario.id, name)
                if audience == reaches:
                    _, new = gb.normalize_render(out, FIXTURE.root_names(scenario))
                    self.assertTrue(any(words in v for _, v in new.get(owner, [])), where)
                else:
                    self.assertNotIn(words, out, where)
                    self.assertIn(notice, out, where)

    def test_override_of_a_subagent_only_section_reaches_subagents_only(self):
        """rules.d/lessons-subagent.md replaces rigger-lessons' subagent-only section: the session gets the notice."""
        self.check_narrowing((AUD_SUB, AUD_SUB_SUB), b"my lessons words",
                             b"rigger: rules.d/lessons-subagent.md replaces rule lessons-subagent.lessons\n",
                             "subagent", "rigger-lessons")

    def test_override_of_a_main_only_section_reaches_the_session_only(self):
        """rules.d/state-file.md replaces rigger-core's main-only section: subagents get the notice."""
        self.check_narrowing((AUD_MAIN, AUD_MAIN_SUB), b"my state words",
                             b"rigger: rules.d/state-file.md replaces rule state-file.state-file-for-long-sessions\n",
                             "session", "rigger-core")

    def test_every_allowed_difference_applies_somewhere(self):
        used = set()
        for scenario in SCENARIOS + NAMED:
            roots, pieces = gb.normalize(FIXTURE.baselines[scenario.baseline_key()], scenario.subagent)
            used.update(apply_allowed(scenario, roots, pieces))
        self.assertEqual([name for name, _ in ALLOWED if name not in used], [])

    def test_merged_table_holds_every_row_of_the_three_tables(self):
        """D40: the 13 rows of the frozen tables and the review module's row, as a multiset, then in D38's order."""
        frozen = {}
        for module, name in (("rigger-rules", "skills.md"), ("rigger-windows", "windows-skills.md"),
                             ("rigger-wp", "wp-skills.md")):
            lines = (gb.FIXTURE / "plugins" / module / "rules" / name).read_bytes().splitlines(True)
            frozen[module] = [line for line in lines if line.startswith(b"| ") and line != b"| Before | Skill |\n"]
        # The testing slot's cell names the testing module, and the UI row names lo-fi screens (ALLOWED).
        frozen["rigger-rules"] = [line.replace(b"`rigger-core:testing`", b"`rigger-testing:testing`")
                                  .replace(UI_ROW_OLD, UI_ROW_NEW) for line in frozen["rigger-rules"]]
        self.assertEqual([len(frozen[m]) for m in ("rigger-rules", "rigger-windows", "rigger-wp")], [8, 3, 2])
        every = frozen["rigger-rules"] + frozen["rigger-windows"] + frozen["rigger-wp"] + [REVIEW_ROW]
        core = [row for row in frozen["rigger-rules"] if b"`superpowers:" not in row]
        self.assertEqual(len(core), 5)
        ordered = (core + [REVIEW_ROW] + frozen["rigger-windows"] + frozen["rigger-wp"]
                   + [row for row in frozen["rigger-rules"] if b"`superpowers:" in row])
        for scenario in (SCENARIOS[0], next(s for s in SCENARIOS if s.agent == "general-purpose")):
            with self.subTest(scenario.id):
                rendered, _, _, _ = FIXTURE.run_new(scenario)
                _, new = gb.normalize_render(rendered, FIXTURE.root_names(scenario))
                at = _piece(new, "rigger-core", SKILLS)
                self.assertIsNotNone(at, "rigger-core prints no %s" % SKILLS)
                rows = split_table(new["rigger-core"][at][1])[1]
                self.assertEqual(sorted(rows), sorted(every))
                self.assertEqual(rows, ordered)

    def test_overrides_of_merged_table_sections(self):
        """Review Focus 7: rules.d/skills.md replaces the merged table with the override notice; a
        rules.d/windows-skills.md with the removed heading prints as a personal section."""
        notice = b"rigger: rules.d/skills.md replaces rule skills.skill-triggers\n"
        for scenario in (TABLE_OVERRIDES, TABLE_OVERRIDES_SUB):
            with self.subTest(scenario.id):
                rendered, joined, _, _ = FIXTURE.run_new(scenario)
                _, new = gb.normalize_render(rendered, FIXTURE.root_names(scenario))
                self.assertEqual(new[gb.HEAD], [(gb.PREAMBLE, notice)])
                self.assertEqual(new["personal"], [(SKILLS, MY_TABLE), (WINDOWS_TABLE, MY_WINDOWS)])
                self.assertNotIn(SKILLS, keys(new.get("rigger-core", [])))
                self.assertNotIn(TABLE_HEADER, rendered + joined)
                for text in (notice, MY_TABLE, MY_WINDOWS):
                    self.assertIn(text, joined)

    def test_lone_root_line_is_the_only_root_difference(self):
        """D1: a handler whose every section is dropped prints a lone root line today; the assembler prints none."""
        old_roots, old = gb.normalize(FIXTURE.baselines[LONE_ROOT.baseline_key()], False)
        rendered, joined, _, _ = FIXTURE.run_new(LONE_ROOT)
        names = FIXTURE.root_names(LONE_ROOT)
        new_roots, new = gb.normalize_render(rendered, names)
        talk_root = "Plugin root: " + gb.root_text(FIXTURE.tree(LONE_ROOT) / "rigger-talk")
        self.assertIn(talk_root, old_roots)
        self.assertNotIn("rigger-talk", old)
        apply_allowed(LONE_ROOT, old_roots, old)
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
    print("# scenarios %d, named tests 8" % len(SCENARIOS))
    sys.stdout.flush()
    started = time.time()
    try:
        program = unittest.main(verbosity=2, exit=False)
    finally:
        print("# golden wall time %.1f s" % (time.time() - started))
    sys.exit(0 if program.result.wasSuccessful() else 1)
