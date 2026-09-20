"""Hyprland 0.56 Lua actions, with verified address-specific placement."""
import configparser
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import time

from .commands import MAX_WORKSPACE
from .folders import resolve_folder
from .sound import check_available, execute_sound


@dataclass(frozen=True)
class WindowTarget:
    address: str
    stable_id: object


def capture_window(client):
    address = client.get("address") or ""
    if address and client.get("stableId") is not None:
        return WindowTarget(address, client["stableId"])
    return address


class Hyprland:
    def __init__(self):
        root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hypr"
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if signature:
            self.directory = root / signature
        else:
            instances = list(root.glob("*/.socket.sock"))
            if len(instances) != 1:
                raise RuntimeError("Start Omarchy Voice inside the intended Hyprland session")
            self.directory = instances[0].parent

    def request(self, command):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(3)
            connection.connect(str(self.directory / ".socket.sock"))
            connection.sendall(command.encode())
            chunks = []
            while chunk := connection.recv(65536):
                chunks.append(chunk)
            return b"".join(chunks).decode()

    def query(self, command):
        return json.loads(self.request("j/" + command))

    def dispatch(self, expression):
        result = self.request("dispatch " + expression).strip()
        if result != "ok":
            raise RuntimeError(result or "Hyprland returned no result")

    def events(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(0.1)
            connection.connect(str(self.directory / ".socket2.sock"))
        except OSError:
            connection.close()
            raise
        return connection


def selector(address):
    if not re.fullmatch(r"0x[0-9a-fA-F]+", address):
        raise ValueError("Invalid window address")
    return json.dumps("address:" + address)


@lru_cache(maxsize=512)
def application_classes(app):
    """Exact identities advertised by the desktop entry and known launchers."""
    wm = (app.wm_class or "").casefold()
    identities = {wm} if wm and not wm.startswith("@@") else set()
    try:
        words = shlex.split(app.exec)
    except ValueError:
        words = []
    stem = app.id.removesuffix(".desktop").casefold()
    # Chromium PWAs advertise an X11 crx_ class, but the observed Wayland
    # app ID includes the desktop ID's profile suffix. Never match the parent
    # browser or another app/profile by prefix or window title.
    pwa = re.fullmatch(r"chrome-([a-p]{32})-(.+)", stem)
    if (pwa and wm == "crx_" + pwa[1]
            and "--app-id=" + pwa[1] in [word.casefold() for word in words]):
        identities.add(stem)
    # Omarchy's TUI helper sets this app ID through xdg-terminal-exec, which
    # can differ from a desktop entry's old StartupWMClass.
    if words and Path(words[0]).name in {"omarchy-launch-tui", "omarchy-launch-or-focus-tui"}:
        if len(words) > 1 and words[1].startswith("--app-id="):
            identity = words[1].split("=", 1)[1]
        elif len(words) > 1 and not words[1].startswith("-"):
            identity = "org.omarchy." + Path(words[1]).name
        else:
            identity = ""
        if identity:
            identities.add(identity.casefold())
    return frozenset(identities)


def matches(app, client, overrides=None, rules=None):
    rule = (rules or {}).get(app.id)
    if rule:
        keys = {"class": "class", "initial_class": "initialClass", "title": "title",
                "initial_title": "initialTitle"}
        variants = rule if isinstance(rule, list) else [rule]
        return any(all(str(client.get(keys[key], "")).casefold() == value.casefold()
                       for key, value in variant.items()) for variant in variants)
    explicit = (overrides or {}).get(app.id)
    classes = {str(client.get(k, "")).casefold() for k in ("class", "initialClass")}
    if explicit:
        if not isinstance(explicit, str):
            raise ValueError(f"Window class override for {app.id} must be a string")
        return explicit.casefold() in classes
    identities = application_classes(app)
    if identities:
        return bool(identities.intersection(classes))
    names = {app.name.casefold(), app.id.removesuffix(".desktop").casefold()}
    try:
        words = shlex.split(app.exec)
        if words:
            executable = Path(words[0]).name.casefold()
            if executable not in {"env", "sh", "bash", "python", "python3", "node", "uwsm-app"}:
                names.add(executable)
    except ValueError:
        pass
    return bool(classes.intersection(names))


def launch_identifier(app, new):
    """uwsm resolves desktop Exec field codes and DBus activation for us."""
    if not new:
        return app.id
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(app.path, encoding="utf-8")
    except (OSError, UnicodeError, configparser.Error) as error:
        raise ValueError(f"Could not read new-window actions for {app.name}: {error}") from error
    actions = parser.get("Desktop Entry", "Actions", fallback="").split(";")
    for name in ("new-window", "new-empty-window", "New", "new"):
        if name in actions and parser.get("Desktop Action " + name, "Exec", fallback="").strip():
            return app.id + ":" + name
    try:
        executable = Path(shlex.split(app.exec)[0]).name if app.exec else ""
    except (ValueError, IndexError):
        executable = ""
    if executable in {"alacritty", "foot", "kitty", "ghostty", "xterm"}:
        return app.id
    raise ValueError(f"{app.name} does not advertise a new-window action")


def recent_key(client, workspace=None):
    rank = client.get("focusHistoryID")
    if not isinstance(rank, int) or rank < 0:
        rank = float("inf")
    return (workspace is not None and client["workspace"]["id"] != workspace,
            rank, client["address"])


class DesktopController:
    def __init__(self, catalog, config, hypr=None):
        self.catalog, self.config = catalog, config
        self.hypr = hypr or Hyprland()
        self.lock = threading.RLock()

    def app_matches(self, app, client):
        rules = self.config.get("window_matches", {})
        # A configured terminal wrapper owns its window instead of its terminal app.
        for app_id in rules.keys() - {app.id}:
            other = self.catalog.apps.get(app_id)
            if other and matches(other, client, rules=rules):
                return False
        return matches(app, client, self.config.get("window_classes", {}), rules)

    def validate(self, command):
        if command.action not in {"open", "move", "switch", "folder", "focus", "fullscreen",
                                  "maximize", "restore", "float", "tile", "close", "volume",
                                  "mute", "unmute"}:
            raise ValueError("Unsupported desktop action")
        if command.workspace is not None and (
            isinstance(command.workspace, bool) or not isinstance(command.workspace, int)
            or not 1 <= command.workspace <= MAX_WORKSPACE
        ):
            raise ValueError("Invalid workspace number")
        if command.action in {"move", "switch"} and command.workspace is None:
            raise ValueError("This action requires a workspace")
        if command.app:
            app = self.catalog.resolve(command.app)
            if command.action == "open" and command.new:
                launch_identifier(app, True)
        if command.action == "folder":
            resolve_folder(command.folder, self.config.get("folders"))
            self.catalog.resolve("file manager")
        if command.action in {"volume", "mute", "unmute"}:
            check_available()

    def execute_plan(self, commands, active_address=None):
        with self.lock:
            for command in commands:
                self.validate(command)
            if active_address is None:
                active_address = capture_window(self.hypr.query("activewindow"))
            if len(commands) == 1:
                return self.execute(commands[0], active_address)
            started = time.monotonic()
            completed = []
            for index, command in enumerate(commands):
                try:
                    # "This window" always means the window captured at speech start.
                    result = self.execute(command, active_address)
                    completed.append(result)
                    address = active_address.address if isinstance(active_address, WindowTarget) else active_address
                    if address in result.get("closed", []):
                        active_address = ""
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    return {"ok": False, "message": f"Stopped at step {index + 1}: {error}",
                            "completed": completed, "failed_step": index + 1,
                            "action_ms": round((time.monotonic() - started) * 1000, 1)}
            return {"ok": True, "message": f"Completed {len(completed)} steps. {completed[-1]['message']}",
                    "completed": completed, "action_ms": round((time.monotonic() - started) * 1000, 1)}

    def focus_state(self):
        return (self.hypr.query("activeworkspace")["id"],
                self.hypr.query("activewindow").get("address"))

    def restore_focus(self, state):
        workspace, address = state
        clients = self.hypr.query("clients")
        original = next((c for c in clients if c["address"] == address
                         and c["workspace"]["id"] == workspace), None)
        if original and self.hypr.query("activewindow").get("address") != address:
            self.hypr.dispatch("hl.dsp.focus({ window = " + selector(address) + " })")
        elif self.hypr.query("activeworkspace")["id"] != workspace:
            self.hypr.dispatch("hl.dsp.focus({ workspace = " + json.dumps(str(workspace)) + " })")
        if self.hypr.query("activeworkspace")["id"] != workspace:
            raise RuntimeError("The previous workspace could not be restored")
        if original and self.hypr.query("activewindow").get("address") != address:
            raise RuntimeError("The previous window could not regain focus")

    def place(self, client, workspace, focus):
        address = client["address"]
        selector(address)
        previous = self.focus_state() if not focus else None
        if workspace is not None and client["workspace"]["id"] != workspace:
            self.hypr.dispatch("hl.dsp.window.move({ workspace = " + json.dumps(str(workspace))
                               + ", window = " + selector(address) + ", follow = "
                               + str(focus).lower() + " })")
        if focus:
            self.hypr.dispatch("hl.dsp.focus({ window = " + selector(address) + " })")
        current = next((c for c in self.hypr.query("clients") if c["address"] == address), None)
        if current is None:
            raise RuntimeError("The application window closed before placement finished")
        if workspace is not None and current["workspace"]["id"] != workspace:
            raise RuntimeError("The application did not reach the requested workspace")
        if focus and self.hypr.query("activewindow").get("address") != address:
            raise RuntimeError("The window was placed but did not receive focus")
        if focus and workspace is not None and self.hypr.query("activeworkspace")["id"] != workspace:
            raise RuntimeError("The window received focus but its workspace did not become active")
        if previous is not None:
            self.restore_focus(previous)
        return current

    def execute(self, command, active_address=None):
        with self.lock:
            started = time.monotonic()
            self.validate(command)
            focus = command.focus and self.config["desktop"]["focus_by_default"]
            if command.action in {"volume", "mute", "unmute"}:
                return {**execute_sound(command), "action_ms": round((time.monotonic() - started) * 1000, 1)}
            if command.action == "switch":
                self.hypr.dispatch("hl.dsp.focus({ workspace = " + json.dumps(str(command.workspace)) + " })")
                if self.hypr.query("activeworkspace")["id"] != command.workspace:
                    raise RuntimeError("The workspace did not become active")
                return {"ok": True, "message": f"Workspace {command.workspace}",
                        "action_ms": round((time.monotonic() - started) * 1000, 1)}
            clients = self.hypr.query("clients")
            if command.action == "folder":
                path = resolve_folder(command.folder, self.config.get("folders"))
                app = self.catalog.resolve("file manager")
                current = self.launch(app, command.workspace, focus, False, clients, files=[str(path)])
                return {"ok": True, "message": f"Opened {command.folder} folder in workspace {current['workspace']['id']}",
                        "window": current["address"], "workspace": current["workspace"]["id"],
                        "stable_id": current.get("stableId"),
                        "action_ms": round((time.monotonic() - started) * 1000, 1)}
            if command.app:
                app = self.catalog.resolve(command.app)
                name = app.name
                candidates = [c for c in clients if self.app_matches(app, c)]
                if command.action in {"close", "focus"} and command.workspace is not None:
                    candidates = [c for c in candidates if c["workspace"]["id"] == command.workspace]
                candidates.sort(key=lambda c: recent_key(c, command.workspace))
                if command.action == "close":
                    targets = candidates if command.workspace is not None else candidates[:1]
                    return self.close(targets, name, started)
                client = candidates[0] if candidates and not command.new else None
                if client is None:
                    if command.action != "open":
                        raise ValueError(f"{name} has no matching open window")
                    current = self.launch(app, command.workspace, focus, command.new, clients)
            else:
                if active_address is None:
                    active_address = capture_window(self.hypr.query("activewindow"))
                address = active_address.address if isinstance(active_address, WindowTarget) else active_address
                client = next((c for c in clients if c["address"] == address), None)
                if client and isinstance(active_address, WindowTarget) and client.get("stableId") != active_address.stable_id:
                    client = None
                if not client:
                    raise ValueError("There is no active window for this command")
                name = "Window"
                if command.action == "close":
                    if command.workspace is not None and client["workspace"]["id"] != command.workspace:
                        raise ValueError("The captured window is not on the requested workspace")
                    return self.close([client], name, started)
            if client is not None:
                if command.action in {"fullscreen", "maximize", "restore", "float", "tile"}:
                    current = self.layout(client, command.action)
                else:
                    current = self.place(client, command.workspace, focus)
            verb = {"fullscreen": "full screen", "maximize": "maximized", "restore": "restored",
                    "float": "floating", "tile": "tiled"}.get(command.action)
            message = f"{name} {verb}" if verb else f"{name} in workspace {current['workspace']['id']}"
            return {"ok": True, "message": message,
                    "window": current["address"], "workspace": current["workspace"]["id"],
                    "stable_id": current.get("stableId"),
                    "action_ms": round((time.monotonic() - started) * 1000, 1)}

    def layout(self, client, action):
        address = client["address"]
        if action in {"float", "tile"}:
            desired = action == "float"
            self.hypr.dispatch('hl.dsp.window.float({ action = "' + ("enable" if desired else "disable")
                               + '", window = ' + selector(address) + ' })')
            verify = lambda c: c.get("floating", False) == desired
        else:
            mode = {"fullscreen": 2, "maximize": 1, "restore": 0}[action]
            self.hypr.dispatch(f'hl.dsp.window.fullscreen_state({{ internal = {mode}, client = {mode}, '
                               + 'action = "set", window = ' + selector(address) + ' })')
            verify = lambda c: c.get("fullscreen", 0) == mode and c.get("fullscreenClient", mode) == mode
        deadline = time.monotonic() + 0.75
        while True:
            current = next((c for c in self.hypr.query("clients") if c["address"] == address), None)
            if current and verify(current):
                return current
            if time.monotonic() >= deadline:
                raise RuntimeError(f"The window did not become {action}")
            time.sleep(0.01)

    def close(self, clients, name, started):
        closed = []
        for client in clients:
            address = client["address"]
            self.hypr.dispatch("hl.dsp.window.close({ window = " + selector(address) + " })")
            deadline = time.monotonic() + self.config["desktop"].get("close_timeout_seconds", 2)
            while any(c["address"] == address for c in self.hypr.query("clients")):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{name} is still open, possibly waiting for a save prompt. "
                                       f"Closed {len(closed)} window(s) before stopping")
                time.sleep(0.02)
            closed.append(address)
        return {"ok": True, "message": f"Closed {len(closed)} {name} window(s)", "closed": closed,
                "action_ms": round((time.monotonic() - started) * 1000, 1)}

    def launch(self, app, workspace, focus, new, before, files=(), valid=lambda:True):
        identifier = launch_identifier(app, new)
        old_addresses = {c["address"] for c in before}
        # Subscribe before launching. The launcher may hand off to an existing process.
        events = self.hypr.events()
        process = None
        initial_focus = None
        changed_workspace = False
        finished = False
        try:
            initial_focus = self.focus_state()
            # Foreground launches start on their destination. Background launches
            # may briefly map elsewhere before their specific new address is moved.
            if focus and workspace is not None:
                self.hypr.dispatch("hl.dsp.focus({ workspace = " + json.dumps(str(workspace)) + " })")
                changed_workspace = workspace != initial_focus[0]
            # Service mode sends application logs to the journal. Only the short
            # launcher writes this unlinked file, so error reads cannot block.
            with tempfile.TemporaryFile() as errors:
                launch_active = self.hypr.query("activewindow").get("address") if files else None
                old_titles = {c["address"]: c.get("title") for c in before}
                process = subprocess.Popen(["uwsm-app", "-t", "service", "--", identifier, *files],
                                           stdout=subprocess.DEVNULL, stderr=errors,
                                           start_new_session=True)
                deadline = time.monotonic() + self.config["desktop"]["launch_timeout_seconds"]
                while time.monotonic() < deadline:
                    if not valid(): raise RuntimeError('Application launch cancelled')
                    clients = self.hypr.query("clients")
                    candidates = [c for c in clients if c["address"] not in old_addresses
                                  and self.app_matches(app, c)]
                    # A file manager may reuse a window. Require an observable
                    # activation or title change after a successful handoff so an
                    # already-focused window cannot mask a pending new launch.
                    if files and not candidates and process.poll() == 0:
                        active = self.hypr.query("activewindow").get("address")
                        candidates = [c for c in clients if c["address"] == active
                                      and self.app_matches(app, c)
                                      and (active != launch_active or c.get("title") != old_titles.get(active))]
                    if candidates:
                        client = min(candidates, key=recent_key)
                        stole_focus = self.hypr.query("activewindow").get("address") == client["address"]
                        current = self.place(client, workspace, focus)
                        if not focus and stole_focus:
                            self.restore_focus(initial_focus)
                        finished = True
                        return current
                    if process.poll() not in (None, 0):
                        errors.seek(0)
                        detail = errors.read(4096).decode(errors="replace").strip()
                        raise RuntimeError(detail or f"Could not launch {app.name}")
                    try:
                        if not events.recv(65536):
                            raise RuntimeError("Hyprland event connection closed during launch")
                    except socket.timeout:
                        pass
                raise RuntimeError(f"{app.name} did not produce a matching window before timeout; "
                                   "check window_classes in config. The app may still be starting")
        finally:
            events.close()
            if not finished and process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.2)
            if not finished and changed_workspace and initial_focus is not None:
                self.restore_focus(initial_focus)
