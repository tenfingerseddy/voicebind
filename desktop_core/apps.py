"""Read desktop entries once and resolve spoken names without evaluating Exec."""

from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import subprocess
import unicodedata


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str
    path: Path
    exec: str
    wm_class: str | None = None
    aliases: tuple[str, ...] = ()


def normalize_alias(alias: str) -> str:
    """Treat punctuation and spacing in installed app names consistently."""
    alias = unicodedata.normalize("NFKC", alias).casefold().strip()
    if alias.endswith(".desktop"):
        alias = alias[:-8]
    return " ".join(re.sub(r"[^\w+]", " ", alias).split())


def _application_dirs() -> list[Path]:
    data_home = os.environ.get("XDG_DATA_HOME", "")
    home = Path(data_home) if data_home.startswith("/") else Path.home() / ".local/share"
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home / "applications", *[
        Path(directory) / "applications"
        for directory in system.split(":") if directory.startswith("/")
    ]]


def _desktop_default(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True,
            text=True, timeout=2, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read_entry(path: Path, desktop_id: str) -> AppEntry | None:
    config = ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    try:
        config.read_string(path.read_text(encoding="utf-8"))
        if "Desktop Entry" not in config:
            return None
        entry = config["Desktop Entry"]
        if (
            entry.get("Type") != "Application"
            or entry.get("Hidden", "false").casefold() == "true"
            or entry.get("NoDisplay", "false").casefold() == "true"
        ):
            return None
        name, executable = entry.get("Name", "").strip(), entry.get("Exec", "").strip()
        if not name or not executable:
            return None
        aliases = {normalize_alias(name), normalize_alias(desktop_id)}
        short_id = desktop_id.removesuffix(".desktop").rsplit(".", 1)[-1]
        aliases.add(normalize_alias(short_id))
        try:
            first = shlex.split(executable)[0]
            binary = Path(first).name
            if binary not in {"env", "sh", "bash", "python", "python3", "node", "uwsm", "uwsm-app", "xdg-open"}:
                aliases.add(normalize_alias(binary))
        except (ValueError, IndexError):
            pass
        wm_class = entry.get("StartupWMClass", "").strip() or None
        return AppEntry(desktop_id, name, path, executable, wm_class, tuple(sorted(aliases - {""})))
    except (OSError, UnicodeError, ConfigError):
        return None


class AppCatalog:
    """Catalog with first-directory-wins desktop override semantics."""

    def __init__(self, directories=None, *, default_browser: str | None = None,
                 default_file_manager: str | None = None,
                 alias_overrides: dict[str, str] | None = None):
        discover_defaults = directories is None
        self.apps: dict[str, AppEntry] = {}
        self.aliases: dict[str, tuple[str, ...]] = {}
        named_aliases: dict[str, set[str]] = {}
        seen: set[str] = set()
        for directory in _application_dirs() if directories is None else directories:
            root = Path(directory)
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.desktop")):
                desktop_id = "-".join(path.relative_to(root).parts)
                if desktop_id in seen:
                    continue
                # Hidden and invalid user entries still shadow the system entry.
                seen.add(desktop_id)
                entry = _read_entry(path, desktop_id)
                if entry is None:
                    continue
                self.apps[desktop_id] = entry
                for alias in {normalize_alias(entry.name), normalize_alias(desktop_id),
                              normalize_alias(desktop_id.removesuffix(".desktop").rsplit(".", 1)[-1])}:
                    named_aliases.setdefault(alias, set()).add(desktop_id)
                for alias in entry.aliases:
                    self.aliases[alias] = (*self.aliases.get(alias, ()), desktop_id)
        # Browser-installed web apps share a binary with their parent browser.
        # An app's actual name or desktop ID takes priority over executable aliases.
        self.aliases.update({alias: tuple(sorted(ids)) for alias, ids in named_aliases.items()})
        browser = (_desktop_default(["xdg-settings", "get", "default-web-browser"])
                   if default_browser is None and discover_defaults else default_browser)
        if browser in self.apps:
            self.aliases["browser"] = (browser,)
        else:
            self._prefer(("browser",), ("firefox", "chromium", "google chrome", "brave browser", "zen browser"))
        try:
            terminal = Path(shlex.split(os.environ.get("TERMINAL", ""))[0]).name
        except (IndexError, ValueError):
            terminal = ""
        self._prefer(("terminal",), (terminal, "ghostty", "alacritty", "kitty", "foot", "gnome terminal", "xterm"))
        file_manager = (_desktop_default(["xdg-mime", "query", "default", "inode/directory"])
                        if default_file_manager is None and discover_defaults else default_file_manager)
        if file_manager in self.apps:
            for alias in ("files", "file manager"):
                self.aliases[alias] = (file_manager,)
        else:
            self._prefer(("files", "file manager"), ("org gnome nautilus", "nautilus", "thunar", "dolphin", "pcmanfm", "strata"))
        self._prefer(("code", "vs code", "v s code", "visual studio code"), ("code", "visual studio code", "code oss", "vscodium"))
        self._prefer(("herder", "herd"), ("herdr",))
        for alias, desktop_id in (alias_overrides or {}).items():
            normalized = normalize_alias(alias)
            if not normalized or desktop_id not in self.apps:
                raise ValueError(f"App alias {alias!r} must name an installed desktop ID.")
            self.aliases[normalized] = (desktop_id,)

    def _prefer(self, aliases: tuple[str, ...], candidates: tuple[str, ...]) -> None:
        for candidate in candidates:
            ids = self.aliases.get(normalize_alias(candidate), ())
            if len(ids) == 1:
                for alias in aliases:
                    self.aliases[normalize_alias(alias)] = ids
                return

    def resolve(self, alias: str) -> AppEntry:
        """Return one installed application, or explain absence or ambiguity."""
        if alias in self.apps:
            return self.apps[alias]
        ids = self.aliases.get(normalize_alias(alias), ())
        if not ids:
            raise ValueError(f"No installed app matches {alias!r}.")
        if len(ids) != 1:
            choices = ", ".join(sorted(ids))
            raise ValueError(f"App name {alias!r} is ambiguous: {choices}.")
        return self.apps[ids[0]]
