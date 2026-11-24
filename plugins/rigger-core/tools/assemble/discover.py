"""Discover modules: enabled installed plugins, RIGGER_MODULE_DIRS, and the config folder's personal module.

What Claude Code loads was measured by spikes S3 and S4 (docs/superpowers/spikes/phase-1/):

- `enabledPlugins` comes from the user `settings.json`, the project `.claude/settings.json` and the
  project `.claude/settings.local.json`, a later file overriding an earlier one key by key. The user
  `settings.local.json` is not read, and an install's `scope` or `projectPath` does not limit it.
- A directory marketplace runs hooks from its source folder, while `installPath` is a stale cache
  copy; so its root is the marketplace `source.path` joined with the plugin's `source`. A git
  marketplace runs from `installPath`.
- `CLAUDE_PLUGIN_ROOT` shows that path with every `\\` turned into `/` and the case as stored;
  `root_text` gives that form for the `Plugin root:` line.

Broken or wrongly shaped state gives one notice per problem and never raises: modules that remain
readable are still returned.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from config import _shown
from manifest import CORE, MANIFEST, Module, ManifestError, load_manifest, parsed_problem, TOO_DEEP

MODULE_DIRS = "RIGGER_MODULE_DIRS"
MODULE_DIRS_FILE = ".rigger-module-dirs"
INLINE = "inline"
PERSONAL = "personal"
INSTALLED = Path("plugins") / "installed_plugins.json"
KNOWN = Path("plugins") / "known_marketplaces.json"
LISTING = Path(".claude-plugin") / "marketplace.json"


@dataclass
class Discovery:
    modules: List[Module]
    enabled: FrozenSet[str]
    notices: List[str]


class _Unreadable(Exception):
    pass


def _read_json(path: Path):
    """The parsed file, None when it does not exist; _Unreadable with a reason otherwise."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise _Unreadable(error.strerror or str(error))
    except ValueError as error:  # a NUL inside the path
        raise _Unreadable(str(error))
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
    except RecursionError:
        parsed = TOO_DEEP
    except (UnicodeDecodeError, ValueError) as error:
        raise _Unreadable("not valid JSON: %s" % error)
    problem = parsed_problem(parsed)
    if problem:
        raise _Unreadable(problem)
    return parsed


def root_text(root: Path) -> str:
    """The root as the `Plugin root:` line prints it (CLAUDE_PLUGIN_ROOT's form, spike S3).

    On Windows `Path` reads `\\` as a separator, so as_posix() turns each into `/` and keeps case.
    """
    return root.as_posix() if os.name == "nt" else str(root)


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def _listing(market_dir: str) -> Optional[dict]:
    try:
        data = _read_json(Path(market_dir) / LISTING)
    except _Unreadable:
        return None
    return data if isinstance(data, dict) else None


def _relative_source(listing: Optional[dict], name: str) -> Optional[str]:
    plugins = listing.get("plugins") if isinstance(listing, dict) else None
    for item in plugins if isinstance(plugins, list) else ():
        if isinstance(item, dict) and item.get("name") == name and isinstance(item.get("source"), str):
            source = item["source"].replace("\\", "/")
            while source.startswith("./"):
                source = source[2:]
            parts = source.split("/")
            if source and not source.startswith("/") and ":" not in source and ".." not in parts:
                return source.rstrip("/")
    return None


def _stale_root(listing: Optional[dict], name: str) -> bool:
    """A readable listing that leaves this plugin on its lagging installPath: unlisted, or a refused folder.

    A plugin listed with a source object lives elsewhere (a git repository), so installPath is its folder.
    """
    if listing is None or _relative_source(listing, name) is not None:
        return False
    plugins = listing.get("plugins")
    entries = [i for i in plugins if isinstance(i, dict) and i.get("name") == name] if isinstance(plugins, list) else []
    return not entries or isinstance(entries[0].get("source"), str)


def _directory_source(marketplaces: dict, market: str) -> Optional[str]:
    known = marketplaces.get(market) if isinstance(marketplaces, dict) else None
    source = known.get("source") if isinstance(known, dict) else None
    if isinstance(source, dict) and source.get("source") == "directory" and isinstance(source.get("path"), str):
        return source["path"]
    return None


def module_root(
    entry: dict, marketplaces: dict, key: str, read_listing: Callable[[str], Optional[dict]] = _listing
) -> Path:
    """Where a plugin's hooks run: a directory marketplace's source folder, else installPath (S3)."""
    name, _, market = key.rpartition("@")
    folder = _directory_source(marketplaces, market)
    if folder is not None:
        relative = _relative_source(read_listing(folder), name)
        if relative:
            return Path(folder.rstrip("/\\") + "/" + relative)
    return Path(entry["installPath"])


def personal_module(config_dir: Path) -> Optional[Module]:
    """The config folder as the `personal` module, when <config dir>/rigger/rules.d exists."""
    root = config_dir / "rigger"
    if not (root / "rules.d").is_dir():
        return None
    return Module(name=PERSONAL, root=root, layer=PERSONAL, fills=None, provides={}, rules=[], triggers=[],
                  subagents={}, config=None, lessons=None, tests={}, marketplace=None, origin=PERSONAL)


def _enabled_flags(config_dir: Path, project_dir: Path, notices: List[str]) -> Dict[str, bool]:
    flags = {}  # type: Dict[str, bool]
    for path in (config_dir / "settings.json", project_dir / ".claude" / "settings.json",
                 project_dir / ".claude" / "settings.local.json"):
        shown = _shown(path, 300)
        try:
            settings = _read_json(path)
        except _Unreadable as error:
            notices.append("rigger: settings %s unreadable (%s); its enabledPlugins ignored" % (shown, _shown(error)))
            continue
        if settings is None:
            continue
        if not isinstance(settings, dict):
            notices.append("rigger: settings %s is not a JSON object; its enabledPlugins ignored" % shown)
            continue
        values = settings.get("enabledPlugins")
        if values is None:
            continue
        if not isinstance(values, dict):
            notices.append("rigger: settings %s enabledPlugins is not an object; ignored" % shown)
            continue
        for key, value in values.items():
            if value is True or value is False:
                flags[key] = value
            else:
                notices.append("rigger: settings %s enabledPlugins %s is not true or false; ignored"
                               % (shown, _shown(key)))
    return flags


def _installed(config_dir: Path, enabled_keys: List[str], notices: List[str]) -> List[Tuple[str, dict]]:
    """(plugin key, install entry) for each enabled key that has a usable entry."""
    path = config_dir / INSTALLED
    shown = _shown(path, 300)
    try:
        data = _read_json(path)
    except _Unreadable as error:
        notices.append("rigger: %s unreadable (%s); installed modules not loaded" % (shown, _shown(error)))
        return []
    if data is None:
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        notices.append("rigger: %s has no plugins object; installed modules not loaded" % shown)
        return []
    found = []
    for key in enabled_keys:
        entries = plugins.get(key)
        if entries is None:
            continue
        if not isinstance(entries, list):
            notices.append("rigger: %s entry %s is not a list; skipped" % (shown, _shown(key)))
            continue
        usable = [e for e in entries if isinstance(e, dict) and isinstance(e.get("installPath"), str)
                  and e["installPath"]]
        if not usable:
            notices.append("rigger: %s entry %s has no installPath; skipped" % (shown, _shown(key)))
            continue
        found.append((key, usable[0]))
    return found


def _known_marketplaces(config_dir: Path, notices: List[str]) -> dict:
    path = config_dir / KNOWN
    try:
        data = _read_json(path)
    except _Unreadable as error:
        notices.append("rigger: %s unreadable (%s); plugin roots taken from installPath"
                       % (_shown(path, 300), _shown(error)))
        return {}
    if data is not None and not isinstance(data, dict):
        notices.append("rigger: %s is not a JSON object; plugin roots taken from installPath" % _shown(path, 300))
        return {}
    return data or {}


def _market_order(known: dict, market: str, name: str) -> Optional[int]:
    entry = known.get(market) if isinstance(known, dict) else None
    if not isinstance(entry, dict):
        return None
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    folder = entry.get("installLocation") or source.get("path")
    if not isinstance(folder, str):
        return None
    listing = _listing(folder)
    plugins = listing.get("plugins") if listing else None
    names = [item.get("name") for item in plugins if isinstance(item, dict)] if isinstance(plugins, list) else []
    return names.index(name) if name in names else None


def _dirs_entries(environ: Mapping[str, str], self_root: Path, notices: List[str]) -> List[str]:
    entries = [entry for entry in environ.get(MODULE_DIRS, "").split(os.pathsep) if entry.strip()]
    listed = self_root / MODULE_DIRS_FILE
    try:
        lines = listed.read_bytes().decode("utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    except (OSError, UnicodeDecodeError) as error:
        notices.append("rigger: %s unreadable (%s); ignored" % (_shown(listed, 300), _shown(error)))
        lines = []
    return entries + [line.strip() for line in lines if line.strip()]


def _load(root: Path, marketplace: Optional[str], origin: str, label: str, notices: List[str]) -> Optional[Module]:
    try:
        return load_manifest(root, marketplace, origin)
    except ManifestError as error:
        notices.append("rigger: module %s skipped: %s" % (_shown(label), _shown(error, 300)))
        return None


def _plugin_name(root: Path) -> Optional[str]:
    """The string `name` of root's .claude-plugin/plugin.json, None when it has none."""
    try:
        data = _read_json(root / ".claude-plugin" / "plugin.json")
    except _Unreadable:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) else None


def discover(config_dir: Path, project_dir: Path, environ: Mapping[str, str], self_root: Path) -> Discovery:
    notices = []  # type: List[str]
    flags = _enabled_flags(config_dir, project_dir, notices)
    known = _known_marketplaces(config_dir, notices)
    enabled = set()
    chosen = {}  # type: Dict[str, Module]
    order = {}  # type: Dict[str, Tuple[int, str, int]]
    unlisted = set()

    for key, entry in _installed(config_dir, [k for k, v in flags.items() if v is True], notices):
        name, _, market = key.rpartition("@")
        enabled.add(name)
        if name == CORE or name in chosen:
            continue
        folder = _directory_source(known, market)
        if folder is not None and market not in unlisted and _listing(folder) is None:
            unlisted.add(market)
            notices.append("rigger: marketplace %s has no readable %s; plugin roots taken from installPath"
                           % (_shown(market), LISTING.as_posix()))
        root = module_root(entry, known, key)
        if not root.is_absolute():  # it would resolve against the hook's cwd, the project
            notices.append("rigger: plugin %s installPath %s is not an absolute path; skipped"
                           % (_shown(key), _shown(root, 300)))
            continue
        if not root.is_dir():
            notices.append("rigger: plugin %s root %s is not a directory; skipped" % (_shown(key), _shown(root, 300)))
            continue
        if not (root / MANIFEST).is_file():
            continue
        if folder is not None and market not in unlisted and _stale_root(_listing(folder), name):
            notices.append("rigger: plugin %s has no usable source in marketplace %s's listing; root taken "
                           "from installPath" % (_shown(key), _shown(market)))
        module = _load(root, market, "installed", name, notices)
        if module is not None:
            chosen[name] = module
            index = _market_order(known, market, name)
            order[name] = (1, market, index) if index is not None else (2, name, 0)

    core = None  # type: Optional[Module]
    from_dirs = set()
    for entry in _dirs_entries(environ, self_root, notices):
        shown = _shown(entry, 300)
        root = Path(entry)
        if not root.is_absolute():
            notices.append("rigger: %s entry %s is not an absolute path; skipped" % (MODULE_DIRS, shown))
            continue
        if not root.is_dir():
            notices.append("rigger: %s entry %s is not a directory; skipped" % (MODULE_DIRS, shown))
            continue
        if not (root / MANIFEST).is_file():
            name = _plugin_name(root)
            if name is not None:  # a plugin that is not a module, such as superpowers in a stub run (D39)
                enabled.add(name)
                continue
            notices.append("rigger: %s entry %s holds no %s; skipped" % (MODULE_DIRS, shown, MANIFEST))
            continue
        module = _load(root, INLINE, "dirs", entry, notices)
        if module is None:
            continue
        name = module.name
        if name in from_dirs:
            notices.append("rigger: %s names module %s twice; kept the first" % (MODULE_DIRS, _shown(name)))
            continue
        from_dirs.add(name)
        enabled.add(name)
        if name == CORE:
            if not _same(root, self_root):
                notices.append("rigger: module %s taken from %s (%s)" % (CORE, MODULE_DIRS, shown))
                core = module
            continue
        replaced = chosen.get(name)
        if replaced is not None and not _same(replaced.root, root):
            notices.append("rigger: module %s taken from %s (%s), not the installed copy"
                           % (_shown(name), MODULE_DIRS, shown))
        chosen[name] = module
        order.setdefault(name, (2, name, 0))

    if core is None:
        core_market = next((k.rpartition("@")[2] for k, v in flags.items()
                            if v is True and k.rpartition("@")[0] == CORE), INLINE)
        core = _load(self_root, core_market, "self", CORE, notices)
    enabled.add(CORE)

    modules = [core] if core is not None else []
    modules.extend(chosen[name] for name in sorted(chosen, key=lambda n: order[n]))
    personal = personal_module(config_dir)
    if personal is not None:
        modules.append(personal)
    return Discovery(modules=modules, enabled=frozenset(enabled), notices=notices)
