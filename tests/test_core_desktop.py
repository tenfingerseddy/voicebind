import copy
from dataclasses import replace
from pathlib import Path
import re
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_core.apps import AppEntry
from desktop_core.commands import Command
from desktop_core.desktop import DesktopController, capture_window, launch_identifier, matches, selector
from desktop_core.plans import parse_plan


def window(address, workspace=1, app_class="example", rank=0, **extra):
    return {"address": address, "workspace": {"id": workspace}, "class": app_class,
            "initialClass": app_class, "focusHistoryID": rank, **extra}


class FakeEvents:
    def __init__(self):
        self.closed = False
        self.disconnected = False

    def recv(self, count):
        if self.disconnected:
            return b""
        raise socket.timeout()

    def close(self):
        self.closed = True


class FakeHyprland:
    def __init__(self, clients=(), active=None, workspace=1):
        self.clients = copy.deepcopy(list(clients))
        self.active = active
        self.workspace = workspace
        self.dispatched = []
        self.event_socket = FakeEvents()
        self.events_opened = False
        self.ignore_move = False
        self.ignore_focus = False
        self.ignore_layout = False
        self.refuse_close = set()

    def events(self):
        self.events_opened = True
        return self.event_socket

    def query(self, command):
        if command == "clients":
            return copy.deepcopy(self.clients)
        if command == "activewindow":
            return copy.deepcopy(next((c for c in self.clients if c["address"] == self.active), {}))
        if command == "activeworkspace":
            return {"id": self.workspace}
        raise AssertionError(f"Unexpected query: {command}")

    def dispatch(self, expression):
        self.dispatched.append(expression)
        destination = re.search(r'workspace = "([0-9]+)"', expression)
        target = re.search(r'window = "address:(0x[0-9a-fA-F]+)"', expression)
        client = next((c for c in self.clients if target and c["address"] == target[1]), None)
        if expression.startswith("hl.dsp.window.move("):
            if not self.ignore_move:
                client["workspace"]["id"] = int(destination[1])
                if "follow = true" in expression:
                    self.active = client["address"]
                    self.workspace = int(destination[1])
                elif self.active == client["address"] and self.workspace != int(destination[1]):
                    self.active = next((c["address"] for c in self.clients if c["workspace"]["id"] == self.workspace), None)
        elif expression.startswith("hl.dsp.focus("):
            if not self.ignore_focus:
                if client:
                    self.active = client["address"]
                    self.workspace = client["workspace"]["id"]
                elif destination:
                    self.workspace = int(destination[1])
                    self.active = next((c["address"] for c in self.clients if c["workspace"]["id"] == self.workspace), None)
        elif expression.startswith("hl.dsp.window.fullscreen_state("):
            if not self.ignore_layout:
                client["fullscreen"] = int(re.search(r"internal = ([0-2])", expression)[1])
                client["fullscreenClient"] = int(re.search(r"client = ([0-2])", expression)[1])
        elif expression.startswith("hl.dsp.window.float("):
            if not self.ignore_layout:
                client["floating"] = 'action = "enable"' in expression
        elif expression.startswith("hl.dsp.window.close("):
            if client["address"] not in self.refuse_close:
                self.clients.remove(client)
                if self.active == client["address"]:
                    self.active = next((c["address"] for c in self.clients
                                        if c["workspace"]["id"] == self.workspace), None)
        else:
            raise AssertionError(f"Unexpected dispatch: {expression}")


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        path = Path(self.temporary.name) / "example.desktop"
        path.write_text("[Desktop Entry]\nType=Application\nName=Example\nExec=example\n", encoding="utf-8")
        self.app = AppEntry("example.desktop", "Example", path, "example", "example")
        self.config = {"desktop": {"focus_by_default": True, "launch_timeout_seconds": 0.05}, "window_classes": {}}
        self.catalog = Mock()
        self.catalog.resolve.return_value = self.app

    def controller(self, hypr):
        return DesktopController(self.catalog, self.config, hypr=hypr)

    def launch_stub(self, hypr, *, new_window=None, error=None, returncode=0, files=()):
        process = Mock()
        process.poll.return_value = returncode

        def launch(arguments, **kwargs):
            self.assertTrue(hypr.events_opened, "Subscribe before the launcher can create a window")
            self.assertEqual(arguments, ["uwsm-app", "-t", "service", "--", "example.desktop", *files])
            self.assertNotIn("shell", kwargs)
            if error:
                kwargs["stderr"].write(error.encode())
                kwargs["stderr"].flush()
            if new_window:
                hypr.clients.append(copy.deepcopy(new_window))
                hypr.active = new_window["address"]
                hypr.workspace = new_window["workspace"]["id"]
            return process

        return process, launch

    def test_matches_exact_class_and_never_title_substring(self):
        self.assertTrue(matches(self.app, window("0x1", app_class="Example")))
        self.assertFalse(matches(self.app, window("0x1", app_class="other", title="Example")))
        self.assertFalse(matches(self.app, window("0x1", app_class="example-helper")))

    def test_explicit_class_override_wins(self):
        self.assertTrue(matches(self.app, window("0x1", app_class="custom.example"), {self.app.id: "custom.example"}))
        self.assertFalse(matches(self.app, window("0x1"), {self.app.id: "custom.example"}))
        with self.assertRaises(ValueError):
            matches(self.app, window("0x1"), {self.app.id: ["example"]})

    def test_placeholder_class_falls_back_but_real_at_sign_class_is_literal(self):
        app = replace(self.app, wm_class="@@startup_wm_class")
        self.assertTrue(matches(app, window("0x1")))
        app = replace(self.app, wm_class="example@instance")
        self.assertFalse(matches(app, window("0x1")))
        self.assertTrue(matches(app, window("0x1", app_class="example@instance")))

    def test_chromium_pwa_matches_x11_and_exact_wayland_app_profile(self):
        app_id = 'a' * 32
        pwa = replace(self.app, id=f'chrome-{app_id}-Profile_1.desktop',
                      exec=f'/usr/bin/chromium --profile-directory=Profile_1 --app-id={app_id}',
                      wm_class='crx_' + app_id)
        for identity in ('crx_' + app_id, f'chrome-{app_id}-Profile_1'):
            self.assertTrue(matches(pwa, window('0x1', app_class=identity)))
        for identity in ('chromium', f'chrome-{app_id}-Default', 'chrome-' + 'b' * 32 + '-Profile_1'):
            self.assertFalse(matches(pwa, window('0x1', app_class=identity)))
        invalid = replace(pwa, exec='/usr/bin/chromium --app-id=' + 'b' * 32)
        self.assertFalse(matches(invalid, window('0x1', app_class=f'chrome-{app_id}-Profile_1')))
        self.assertFalse(matches(pwa, window('0x1', app_class='chromium', title=pwa.name)))

    def test_omarchy_tui_helper_matches_its_generated_or_explicit_app_id(self):
        for executable, identity in (
                ('omarchy-launch-or-focus-tui tasks', 'org.omarchy.tasks'),
                ('/usr/share/omarchy/bin/omarchy-launch-tui /usr/bin/tasks', 'org.omarchy.tasks'),
                ('omarchy-launch-tui --app-id=org.example.tasks tasks', 'org.example.tasks')):
            app = replace(self.app, exec=executable, wm_class='Tasks')
            self.assertTrue(matches(app, window('0x1', app_class=identity)))
            self.assertFalse(matches(app, window('0x1', app_class='Alacritty')))

    def test_pwa_close_does_not_close_browser_or_another_web_app(self):
        app_id = 'a' * 32
        self.app = replace(self.app, id=f'chrome-{app_id}-Default.desktop',
                           exec=f'chromium --app-id={app_id}', wm_class='crx_' + app_id)
        self.catalog.resolve.return_value = self.app
        hypr = FakeHyprland([window('0x1', app_class='chromium'),
                             window('0x2', app_class=f'chrome-{app_id}-Default'),
                             window('0x3', app_class='chrome-' + 'b' * 32 + '-Default')], '0x2')
        result = self.controller(hypr).execute(Command('close', 'example'))
        self.assertEqual(result['closed'], ['0x2'])
        self.assertEqual([c['address'] for c in hypr.clients], ['0x1', '0x3'])

    def test_new_window_desktop_action_uses_documented_uwsm_identifier(self):
        self.app.path.write_text("[Desktop Entry]\nActions=new-window;\n[Desktop Action new-window]\nExec=example --new-window\n", encoding="utf-8")
        self.assertEqual(launch_identifier(self.app, True), "example.desktop:new-window")
        self.assertEqual(launch_identifier(self.app, False), "example.desktop")

    def test_new_window_requires_action_exec_or_known_terminal(self):
        self.app.path.write_text("[Desktop Entry]\nActions=new-window;\n[Desktop Action new-window]\nName=New\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not advertise"):
            launch_identifier(self.app, True)
        self.assertEqual(launch_identifier(replace(self.app, exec="alacritty"), True), "example.desktop")

    def test_selects_window_already_at_destination(self):
        hypr = FakeHyprland([window("0x1", 1, rank=0), window("0x2", 3, rank=5)], "0x1")
        with patch("desktop_core.desktop.subprocess.Popen") as launch:
            result = self.controller(hypr).execute(Command("open", "example", 3))
        launch.assert_not_called()
        self.assertEqual((result["window"], result["workspace"]), ("0x2", 3))
        self.assertEqual(hypr.active, "0x2")
        self.assertFalse(any("window.move" in expression for expression in hypr.dispatched))

    def test_selects_most_recent_and_puts_unfocused_windows_last(self):
        hypr = FakeHyprland([window("0x1", rank=-1), window("0x2", rank=4), window("0x3", rank=1)], "0x2")
        result = self.controller(hypr).execute(Command("open", "example", 3))
        self.assertEqual(result["window"], "0x3")
        self.assertEqual(hypr.clients[0]["workspace"]["id"], 1)
        self.assertEqual(hypr.clients[1]["workspace"]["id"], 1)

    def test_move_uses_captured_window_even_when_current_focus_changed(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", app_class="other")], "0x2")
        result = self.controller(hypr).execute(Command("move", workspace=3), active_address="0x1")
        self.assertEqual(result["window"], "0x1")
        self.assertEqual(hypr.clients[1]["workspace"]["id"], 1)
        self.assertIn('window = "address:0x1"', hypr.dispatched[0])

    def test_closed_captured_window_never_falls_back_to_new_active_window(self):
        hypr = FakeHyprland([window("0x2")], "0x2")
        with self.assertRaisesRegex(ValueError, "no active window"):
            self.controller(hypr).execute(Command("move", workspace=3), active_address="0x1")
        self.assertEqual(hypr.dispatched, [])

    def test_captured_stable_id_rejects_a_different_window_at_the_same_address(self):
        captured = capture_window(window("0x1", stableId=41))
        for current in (window("0x1", stableId=42), window("0x1")):
            for action in ("move", "fullscreen", "close"):
                hypr = FakeHyprland([current], "0x1")
                command = Command(action, workspace=3 if action == "move" else None)
                with self.subTest(current=current, action=action), self.assertRaisesRegex(ValueError, "no active window"):
                    self.controller(hypr).execute(command, active_address=captured)
                self.assertEqual(hypr.dispatched, [])

    def test_captured_stable_id_accepts_the_original_window(self):
        original = window("0x1", stableId=41)
        captured = capture_window(original)
        hypr = FakeHyprland([original, window("0x2", app_class="other", stableId=42)], "0x2")
        result = self.controller(hypr).execute(Command("move", workspace=3), active_address=captured)
        self.assertEqual(result["window"], "0x1")
        self.assertEqual([c["workspace"]["id"] for c in hypr.clients], [3, 1])

    def test_existing_background_move_preserves_original_workspace(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", app_class="other")], "0x1")
        result = self.controller(hypr).execute(Command("open", "example", 3, focus=False))
        self.assertEqual(result["workspace"], 3)
        self.assertEqual(hypr.workspace, 1)
        self.assertEqual(hypr.active, "0x2")

    def test_launch_places_once_and_returns_fresh_destination(self):
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        process, launch = self.launch_stub(hypr, new_window=window("0x1", workspace=2))
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute(Command("open", "example", 3))
        self.assertEqual((result["window"], result["workspace"]), ("0x1", 3))
        self.assertEqual(sum("window.move" in expression for expression in hypr.dispatched), 1)
        self.assertEqual(hypr.workspace, 3)
        self.assertTrue(hypr.event_socket.closed)
        process.terminate.assert_not_called()

    def test_background_launch_restores_focus_on_same_workspace(self):
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        _, launch = self.launch_stub(hypr, new_window=window("0x1"))
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute(Command("open", "example", 1, focus=False))
        self.assertTrue(result["ok"])
        self.assertEqual(hypr.active, "0xa")
        self.assertEqual(hypr.workspace, 1)

    def test_background_launch_restores_workspace_and_focus(self):
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        _, launch = self.launch_stub(hypr, new_window=window("0x1", workspace=2))
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute(Command("open", "example", 3, focus=False))
        self.assertEqual(result["workspace"], 3)
        self.assertEqual((hypr.workspace, hypr.active), (1, "0xa"))

    def test_unsupported_new_window_fails_before_launch_or_workspace_change(self):
        hypr = FakeHyprland([window("0x1")], "0x1")
        with patch("desktop_core.desktop.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ValueError, "does not advertise"):
                self.controller(hypr).execute(Command("open", "example", 3, new=True))
        launch.assert_not_called()
        self.assertEqual(hypr.dispatched, [])

    def test_launcher_error_is_preserved_and_foreground_is_restored(self):
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        _, launch = self.launch_stub(hypr, error="launcher: desktop entry failed", returncode=1)
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            with self.assertRaisesRegex(RuntimeError, "launcher: desktop entry failed"):
                self.controller(hypr).execute(Command("open", "example", 3))
        self.assertEqual((hypr.workspace, hypr.active), (1, "0xa"))
        self.assertTrue(hypr.event_socket.closed)

    def test_timeout_reports_no_success_and_releases_resources(self):
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        process, launch = self.launch_stub(hypr, returncode=None)
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            with patch("desktop_core.desktop.time.monotonic", side_effect=[0, 0, 0.01, 1]):
                with self.assertRaisesRegex(RuntimeError, "before timeout"):
                    self.controller(hypr).execute(Command("open", "example", 3))
        process.terminate.assert_called_once()
        self.assertEqual((hypr.workspace, hypr.active), (1, "0xa"))
        self.assertTrue(hypr.event_socket.closed)

    def test_unrelated_new_window_is_not_accepted_on_title_match(self):
        hypr = FakeHyprland()
        _, launch = self.launch_stub(hypr, new_window=window("0x1", app_class="other", title="Example"))
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            with patch("desktop_core.desktop.time.monotonic", side_effect=[0, 0, 0.01, 1]):
                with self.assertRaisesRegex(RuntimeError, "before timeout"):
                    self.controller(hypr).execute(Command("open", "example", 3))

    def test_event_disconnect_reports_failure_and_closes_socket(self):
        hypr = FakeHyprland()
        hypr.event_socket.disconnected = True
        _, launch = self.launch_stub(hypr)
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            with self.assertRaisesRegex(RuntimeError, "event connection closed"):
                self.controller(hypr).execute(Command("open", "example", 3))
        self.assertTrue(hypr.event_socket.closed)

    def test_dispatch_acknowledgement_is_not_placement_proof(self):
        hypr = FakeHyprland([window("0x1")], "0x1")
        hypr.ignore_move = True
        with self.assertRaisesRegex(RuntimeError, "did not reach"):
            self.controller(hypr).execute(Command("move", workspace=3))

    def test_missing_focus_is_reported(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", app_class="other")], "0x2")
        hypr.ignore_focus = True
        with self.assertRaisesRegex(RuntimeError, "did not receive focus"):
            self.controller(hypr).execute(Command("open", "example"))

    def test_switch_is_verified(self):
        hypr = FakeHyprland()
        result = self.controller(hypr).execute(Command("switch", workspace=3))
        self.assertTrue(result["ok"])
        self.assertEqual(hypr.workspace, 3)
        hypr.ignore_focus = True
        with self.assertRaisesRegex(RuntimeError, "did not become active"):
            self.controller(hypr).execute(Command("switch", workspace=4))

    def test_invalid_workspace_and_address_are_rejected(self):
        for workspace in (-1, 0, True, "3", 2147483648):
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                self.controller(FakeHyprland()).execute(Command("switch", workspace=workspace))
        with self.assertRaises(ValueError):
            selector('0x1" }); hl.dsp.exec_cmd("bad")')

    def test_exact_window_rule_requires_all_fields_and_precedes_class_override(self):
        rules = {self.app.id: {"initial_class": "Terminal", "initial_title": "Task Console"}}
        matching = window("0x1", app_class="terminal", initialTitle="task console", title="Changed title")
        self.assertTrue(matches(self.app, matching, {self.app.id: "different"}, rules))
        for wrong in (replace_client(matching, initialTitle="Task Console - other"),
                      replace_client(matching, initialClass="Other"), window("0x2")):
            with self.subTest(client=wrong):
                self.assertFalse(matches(self.app, wrong, rules=rules))

    def test_configured_wrapper_is_not_claimed_as_an_ordinary_terminal(self):
        terminal = replace(self.app, id="terminal.desktop", name="Terminal", wm_class="Terminal")
        wrapper = replace(self.app, id="tasks.desktop", name="Tasks", wm_class=None)
        self.config["window_matches"] = {
            wrapper.id: {"initial_class": "Terminal", "initial_title": "Task Console"}}
        self.catalog.apps = {terminal.id: terminal, wrapper.id: wrapper}
        wrapped = window("0x1", app_class="Terminal", initialTitle="Task Console")
        ordinary = window("0x2", app_class="Terminal", initialTitle="Shell")
        controller = self.controller(FakeHyprland([wrapped, ordinary]))
        self.assertTrue(controller.app_matches(wrapper, wrapped))
        self.assertFalse(controller.app_matches(terminal, wrapped))
        self.assertTrue(controller.app_matches(terminal, ordinary))
        self.assertFalse(controller.app_matches(wrapper, ordinary))

    def test_terminal_launch_does_not_reuse_either_configured_wrapper_identity(self):
        wrapper = replace(self.app, id='tasks.desktop', name='Tasks', wm_class='Tasks')
        self.config['window_matches'] = {wrapper.id: [
            {'initial_class': 'example', 'initial_title': 'Task Console'},
            {'class': 'org.omarchy.tasks'}]}
        self.catalog.apps = {self.app.id: self.app, wrapper.id: wrapper}
        for wrapped in (window('0x1', app_class='example', initialTitle='Task Console'),
                        window('0x1', app_class='org.omarchy.tasks', initialClass='example')):
            with self.subTest(wrapped=wrapped):
                hypr = FakeHyprland([wrapped], '0x1')
                controller = self.controller(hypr)
                self.assertTrue(controller.app_matches(wrapper, wrapped))
                self.assertFalse(controller.app_matches(self.app, wrapped))
                process, launch = self.launch_stub(hypr, new_window=window('0x2', initialTitle='Shell'))
                with patch('desktop_core.desktop.subprocess.Popen', side_effect=launch):
                    result = controller.execute(Command('open', 'example'))
                self.assertEqual(result['window'], '0x2')
                self.assertIn('0x1', [c['address'] for c in hypr.clients])

    def test_named_move_targets_app_independently_of_captured_or_active_window(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", app_class="other")], "0x2")
        result = self.controller(hypr).execute(Command("move", "example", 3), active_address="0x2")
        self.assertEqual(result["window"], "0x1")
        self.assertEqual([c["workspace"]["id"] for c in hypr.clients], [3, 1])

    def test_named_focus_only_selects_matching_window_on_requested_workspace(self):
        hypr = FakeHyprland([window("0x1", 1), window("0x2", 3, rank=7)], "0x1")
        result = self.controller(hypr).execute(Command("focus", "example", 3))
        self.assertEqual(result["window"], "0x2")
        self.assertEqual((hypr.workspace, hypr.active), (3, "0x2"))
        self.assertFalse(any("window.move" in operation for operation in hypr.dispatched))

    def test_named_action_without_matching_window_does_not_fall_back_to_active(self):
        for action in ("move", "focus", "fullscreen", "maximize", "restore", "float", "tile"):
            hypr = FakeHyprland([window("0x2", app_class="other")], "0x2")
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "no matching open window"):
                self.controller(hypr).execute(Command(action, "example", 3 if action == "move" else None))
            self.assertEqual(hypr.dispatched, [])

    def test_layout_is_addressed_and_idempotent_without_changing_focus(self):
        for action, field, value in (("fullscreen", "fullscreen", 2), ("maximize", "fullscreen", 1),
                                     ("restore", "fullscreen", 0), ("float", "floating", True),
                                     ("tile", "floating", False)):
            with self.subTest(action=action):
                hypr = FakeHyprland([window("0x1", 3, fullscreen=2, floating=True),
                                     window("0x2", app_class="other", fullscreen=0)], "0x2")
                controller = self.controller(hypr)
                for _ in range(2):
                    result = controller.execute(Command(action, "example"))
                    self.assertEqual(result["window"], "0x1")
                    self.assertEqual(hypr.clients[0][field], value)
                self.assertEqual((hypr.workspace, hypr.active), (1, "0x2"))
                self.assertEqual(hypr.clients[1]["fullscreen"], 0)
                self.assertTrue(all('window = "address:0x1"' in operation for operation in hypr.dispatched))

    def test_layout_requires_observed_state_change(self):
        hypr = FakeHyprland([window("0x1", fullscreen=0)], "0x1")
        hypr.ignore_layout = True
        with patch("desktop_core.desktop.time.monotonic", side_effect=[0, 0, 1]):
            with self.assertRaisesRegex(RuntimeError, "did not become fullscreen"):
                self.controller(hypr).execute(Command("fullscreen", "example"))

    def test_close_named_app_on_workspace_closes_only_its_windows_there(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", 1), window("0x3", 2),
                             window("0x4", 1, app_class="other")], "0x4")
        result = self.controller(hypr).execute(Command("close", "example", 1))
        self.assertEqual(result["closed"], ["0x1", "0x2"])
        self.assertEqual([c["address"] for c in hypr.clients], ["0x3", "0x4"])
        self.assertEqual(hypr.active, "0x4")

    def test_close_without_workspace_closes_only_most_recent_matching_window(self):
        hypr = FakeHyprland([window("0x1", rank=4), window("0x2", rank=0)], "0x1")
        result = self.controller(hypr).execute(Command("close", "example"))
        self.assertEqual(result["closed"], ["0x2"])
        self.assertEqual([c["address"] for c in hypr.clients], ["0x1"])

    def test_close_without_named_matches_is_noop(self):
        hypr = FakeHyprland([window("0x1", 2), window("0x2", app_class="other")], "0x2")
        result = self.controller(hypr).execute(Command("close", "example", 1))
        self.assertTrue(result["ok"])
        self.assertEqual(result["closed"], [])
        self.assertEqual(hypr.dispatched, [])

    def test_close_captured_window_checks_workspace_and_never_falls_back(self):
        for captured, workspace, error in (("0x1", 2, "not on the requested workspace"),
                                            ("", None, "no active window"),
                                            ("0x3", None, "no active window")):
            hypr = FakeHyprland([window("0x1"), window("0x2")], "0x2")
            with self.subTest(captured=captured), self.assertRaisesRegex(ValueError, error):
                self.controller(hypr).execute(Command("close", workspace=workspace), captured)
            self.assertEqual(hypr.dispatched, [])

    def test_refused_close_stops_before_remaining_windows_and_later_plan_steps(self):
        hypr = FakeHyprland([window("0x1"), window("0x2"), window("0x3")], "0x3")
        hypr.refuse_close = {"0x2"}
        self.config["desktop"]["close_timeout_seconds"] = 0
        result = self.controller(hypr).execute_plan([
            Command("close", "example", 1), Command("switch", workspace=2)])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_step"], 1)
        self.assertEqual(result["completed"], [])
        self.assertIn("Closed 1 window(s) before stopping", result["message"])
        self.assertEqual([c["address"] for c in hypr.clients], ["0x2", "0x3"])
        self.assertEqual(hypr.workspace, 1)
        self.assertEqual(len(hypr.dispatched), 2)

    def test_plan_preserves_completed_steps_when_later_step_fails(self):
        hypr = FakeHyprland([window("0x1")], "0x1")
        hypr.refuse_close = {"0x1"}
        self.config["desktop"]["close_timeout_seconds"] = 0
        result = self.controller(hypr).execute_plan([
            Command("switch", workspace=2), Command("close", "example"), Command("switch", workspace=3)])
        self.assertEqual(result["failed_step"], 2)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(hypr.workspace, 2)

    def test_plan_preflights_unknown_app_before_any_mutation(self):
        self.catalog.resolve.side_effect = ValueError("Unknown application")
        hypr = FakeHyprland([window("0x1")], "0x1")
        with self.assertRaisesRegex(ValueError, "Unknown application"):
            self.controller(hypr).execute_plan([
                Command("close"), Command("open", "missing")], active_address="0x1")
        self.assertEqual(hypr.dispatched, [])

    def test_plan_preflights_missing_folder_before_any_mutation(self):
        self.config["folders"] = {"projects": str(Path(self.temporary.name) / "missing")}
        hypr = FakeHyprland([window("0x1")], "0x1")
        with self.assertRaisesRegex(ValueError, "existing absolute directory"):
            self.controller(hypr).execute_plan([
                Command("close"), Command("folder", folder="projects")], active_address="0x1")
        self.assertEqual(hypr.dispatched, [])

    def test_plan_uses_original_captured_window_after_focus_changes(self):
        hypr = FakeHyprland([window("0x1"), window("0x2", 2)], "0x1")
        result = self.controller(hypr).execute_plan([
            Command("switch", workspace=2), Command("move", workspace=3)], active_address="0x1")
        self.assertTrue(result["ok"])
        self.assertEqual([c["workspace"]["id"] for c in hypr.clients], [3, 2])

    def test_plan_without_explicit_capture_snapshots_before_first_step_changes_focus(self):
        hypr = FakeHyprland([window("0x1", stableId=41), window("0x2", 2, stableId=42)], "0x1")
        result = self.controller(hypr).execute_plan([
            Command("switch", workspace=2), Command("move", workspace=3)])
        self.assertTrue(result["ok"])
        self.assertEqual([c["workspace"]["id"] for c in hypr.clients], [3, 2])

    def test_plan_without_initial_window_does_not_capture_a_later_focused_window(self):
        hypr = FakeHyprland([window("0x1", 2, stableId=41)], None)
        result = self.controller(hypr).execute_plan([
            Command("switch", workspace=2), Command("close")])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_step"], 2)
        self.assertEqual([c["address"] for c in hypr.clients], ["0x1"])

    def test_closed_captured_target_stays_invalid_when_hyprland_reuses_its_address(self):
        hypr = FakeHyprland([window("0x1")], "0x1")
        _, launch = self.launch_stub(hypr, new_window=window("0x1", workspace=2))
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute_plan([
                Command("close", "example"), Command("open", "example", 2),
                Command("move", workspace=3)], active_address="0x1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_step"], 3)
        self.assertEqual(len(result["completed"]), 2)
        self.assertEqual(hypr.clients[0]["workspace"]["id"], 2)
        self.assertFalse(any("window.move" in operation for operation in hypr.dispatched))

    def test_example_chain_closes_scoped_files_and_places_all_requested_apps(self):
        apps = {name: replace(self.app, id=name + ".desktop", name=name, wm_class=name)
                for name in ("files", "chromium", "terminal")}
        self.catalog.resolve.side_effect = lambda name: apps["files" if name == "file manager" else name]
        hypr = FakeHyprland([
            window("0x1", 1, app_class="files"), window("0x2", 3, app_class="files"),
            window("0x3", 4, app_class="chromium"), window("0x4", 3, app_class="terminal"),
            window("0xa", 1, app_class="other")], "0xa")
        commands = parse_plan("close files on workspace 1 and open chromium there "
                              "then open a terminal and file manager on workspace 2")
        result = self.controller(hypr).execute_plan(commands, active_address="0xa")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["completed"]), 4)
        self.assertEqual({c["address"]: c["workspace"]["id"] for c in hypr.clients},
                         {"0x2": 2, "0x3": 1, "0x4": 2, "0xa": 1})
        self.assertEqual((hypr.workspace, hypr.active), (2, "0x2"))

    def test_folder_launcher_receives_literal_path_and_places_new_window(self):
        directory = Path(self.temporary.name) / "Project notes; $(literal)"
        directory.mkdir()
        self.config["folders"] = {"projects": str(directory)}
        hypr = FakeHyprland([window("0xa", app_class="other")], "0xa")
        _, launch = self.launch_stub(hypr, new_window=window("0x1", workspace=2), files=[str(directory)])
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute(Command("folder", workspace=3, folder="projects"))
        self.assertEqual((result["window"], result["workspace"]), ("0x1", 3))
        self.catalog.resolve.assert_called_with("file manager")

    def test_folder_launcher_accepts_existing_window_activated_by_handoff(self):
        directory = Path(self.temporary.name)
        self.config["folders"] = {"projects": str(directory)}
        hypr = FakeHyprland([window("0x1", 2), window("0xa", app_class="other")], "0xa")
        process, launch = self.launch_stub(hypr, files=[str(directory)])

        def activate_existing(arguments, **kwargs):
            launched = launch(arguments, **kwargs)
            hypr.active, hypr.workspace = "0x1", 2
            return launched

        with patch("desktop_core.desktop.subprocess.Popen", side_effect=activate_existing):
            result = self.controller(hypr).execute(Command("folder", workspace=3, folder="projects"))
        self.assertEqual((result["window"], result["workspace"]), ("0x1", 3))
        self.assertEqual(len(hypr.clients), 2)
        process.terminate.assert_not_called()

    def test_folder_failed_handoff_does_not_accept_an_existing_matching_window(self):
        directory = Path(self.temporary.name)
        self.config["folders"] = {"projects": str(directory)}
        hypr = FakeHyprland([window("0x1")], "0x1")
        _, launch = self.launch_stub(hypr, error="Directory handoff failed", returncode=1,
                                     files=[str(directory)])
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            with self.assertRaisesRegex(RuntimeError, "Directory handoff failed"):
                self.controller(hypr).execute(Command("folder", folder="projects"))

    def test_folder_waits_for_pending_new_window_instead_of_reusing_already_active_window(self):
        directory = Path(self.temporary.name)
        self.config["folders"] = {"projects": str(directory)}
        hypr = FakeHyprland([window("0x1", title="Old folder")], "0x1")
        _, launch = self.launch_stub(hypr, files=[str(directory)])

        def new_window_event(count):
            hypr.clients.append(window("0x2", title="Projects"))
            hypr.active = "0x2"
            return b"openwindow>>0x2"

        hypr.event_socket.recv = new_window_event
        with patch("desktop_core.desktop.subprocess.Popen", side_effect=launch):
            result = self.controller(hypr).execute(Command("folder", workspace=1, folder="projects"))
        self.assertEqual(result["window"], "0x2")
        self.assertEqual(len(hypr.clients), 2)

    def test_folder_accepts_title_change_in_already_active_matching_window(self):
        directory = Path(self.temporary.name)
        self.config["folders"] = {"projects": str(directory)}
        hypr = FakeHyprland([window("0x1", title="Old folder")], "0x1")
        _, launch = self.launch_stub(hypr, files=[str(directory)])

        def update_folder(arguments, **kwargs):
            process = launch(arguments, **kwargs)
            hypr.clients[0]["title"] = "Projects"
            return process

        with patch("desktop_core.desktop.subprocess.Popen", side_effect=update_folder):
            result = self.controller(hypr).execute(Command("folder", folder="projects"))
        self.assertEqual(result["window"], "0x1")
        self.assertEqual(len(hypr.clients), 1)


def replace_client(client, **changes):
    return {**client, **changes}


if __name__ == "__main__":
    unittest.main()
