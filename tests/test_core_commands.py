import unittest

from desktop_core.commands import Command, MAX_WORKSPACE, NUMBERS, grammar_phrases, parse_command


class CommandTests(unittest.TestCase):
    def test_requested_expanded_examples(self):
        examples = {
            "open the downloads folder": Command("folder", folder="downloads"),
            "make chromium full screen": Command("fullscreen", app="chromium"),
            "open workspace 4": Command("switch", workspace=4),
            "move herdr to workspace 3": Command("move", app="herdr", workspace=3),
            "close files on workspace 1": Command("close", app="files", workspace=1),
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), expected)

    def test_standard_folders_and_music_app_disambiguation(self):
        for name in ("home", "downloads", "documents", "pictures", "music", "videos", "desktop"):
            with self.subTest(name=name):
                self.assertEqual(parse_command(f"open the {name} folder"), Command("folder", folder=name))
        self.assertEqual(parse_command("open downloads"), Command("folder", folder="downloads"))
        self.assertEqual(parse_command("open music"), Command("open", app="music"))

    def test_folder_destinations_background_and_custom_aliases(self):
        self.assertEqual(
            parse_command("open the projects folder in workspace work in the background", {"work": 3}, {"projects"}),
            Command("folder", folder="projects", workspace=3, focus=False),
        )
        self.assertEqual(parse_command("show folder documents on 4"), Command("folder", folder="documents", workspace=4))
        with self.assertRaises(ValueError):
            parse_command("open the unknown folder")
        with self.assertRaises(ValueError):
            parse_command("open the projects folder")

    def test_named_move_in_background(self):
        self.assertEqual(parse_command("send browser to four in the background"), Command("move", app="browser", workspace=4, focus=False))

    def test_focus_existing_app_forms(self):
        for text in ("focus chromium", "focus on chromium", "switch to chromium"):
            with self.subTest(text=text):
                self.assertEqual(parse_command(text), Command("focus", app="chromium"))
        self.assertEqual(parse_command("show chromium"), Command("open", app="chromium"))

    def test_window_modes_by_app_or_captured_window(self):
        for phrase, action in (("fullscreen", "fullscreen"), ("full screen", "fullscreen"),
                               ("maximize", "maximize"), ("restore", "restore"),
                               ("float", "float"), ("tile", "tile")):
            with self.subTest(phrase=phrase):
                self.assertEqual(parse_command(f"{phrase} chromium"), Command(action, app="chromium"))
                self.assertEqual(parse_command(f"{phrase} this window"), Command(action))
                self.assertEqual(parse_command(f"{phrase} current window"), Command(action))
                self.assertEqual(parse_command(phrase), Command(action))

    def test_make_window_modes_and_exit_fullscreen(self):
        self.assertEqual(parse_command("make the current window full screen"), Command("fullscreen"))
        self.assertEqual(parse_command("make terminal maximized"), Command("maximize", app="terminal"))
        self.assertEqual(parse_command("make terminal floating"), Command("float", app="terminal"))
        self.assertEqual(parse_command("make terminal tiled"), Command("tile", app="terminal"))
        self.assertEqual(parse_command("exit full screen for chromium"), Command("restore", app="chromium"))
        self.assertEqual(parse_command("exit full screen"), Command("restore"))

    def test_target_first_window_modes(self):
        for suffix, action in (("fullscreen", "fullscreen"), ("full screen", "fullscreen"),
                               ("maximize", "maximize"), ("maximise", "maximize"),
                               ("maximized", "maximize"), ("maximised", "maximize"),
                               ("restore", "restore"), ("float", "float"), ("floating", "float"),
                               ("tile", "tile"), ("tiled", "tile")):
            with self.subTest(suffix=suffix):
                self.assertEqual(parse_command(f"browser {suffix}"), Command(action, "browser"))
                self.assertEqual(parse_command(f"file manager {suffix}"), Command(action, "file manager"))
                self.assertEqual(parse_command(f"this window {suffix}"), Command(action))
        self.assertEqual(parse_command("Please, browser full screen!"), Command("fullscreen", "browser"))

    def test_short_window_modes_do_not_swallow_negation_or_extra_actions(self):
        for phrase in ("do not browser fullscreen", "browser not fullscreen", "all windows fullscreen",
                       "browser fullscreen then close terminal", "browser fullscreen except terminal",
                       "browser fullscreen restore", "open browser fullscreen"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                parse_command(phrase)

    def test_close_keeps_named_app_and_workspace_scope(self):
        self.assertEqual(parse_command("close files on workspace three"), Command("close", app="files", workspace=3))
        self.assertEqual(parse_command("close this window"), Command("close"))
        self.assertEqual(parse_command("close current window in three"), Command("close", workspace=3))
        with self.assertRaises(ValueError):
            parse_command("close all windows")

    def test_volume_absolute_and_relative(self):
        self.assertEqual(parse_command("set volume to 0 percent"), Command("volume", value=0))
        self.assertEqual(parse_command("set the volume to fifty percent"), Command("volume", value=50))
        self.assertEqual(parse_command("set volume to one hundred"), Command("volume", value=100))
        self.assertEqual(parse_command("volume to 75%"), Command("volume", value=75))
        self.assertEqual(parse_command("volume up"), Command("volume", value=5, relative=True))
        self.assertEqual(parse_command("turn volume down"), Command("volume", value=-5, relative=True))
        self.assertEqual(parse_command("turn the volume down by twenty five percent"), Command("volume", value=-25, relative=True))
        self.assertEqual(parse_command("volume up ten"), Command("volume", value=10, relative=True))

    def test_mute_and_pronounceable_unmute_forms(self):
        for text in ("mute", "mute audio", "mute the sound", "sound off"):
            self.assertEqual(parse_command(text), Command("mute"))
        for text in ("unmute", "un mute", "unmute sound", "un mute audio", "sound on", "turn sound on"):
            self.assertEqual(parse_command(text), Command("unmute"))

    def test_expanded_commands_reject_extra_prose_and_unsafe_values(self):
        for text in (
            "make chromium full screen and move terminal", "do not maximize chromium",
            "restore chromium then open files", "mute and open chromium", "un mute except chromium",
            "close all files", "close files and open chromium", "open a new downloads folder",
            "open the downloads folder and delete everything", "open /tmp folder",
            "set volume to 101", "set volume to -5", "set volume to 1.5", "volume down by minus five",
            "volume up by one hundred and one", "set volume to fifty then mute", "open chromium there",
            "close files on workspace 1 and open chromium there then open a terminal and file manager on workspace 2",
            "delete downloads", "shutdown", "reboot",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text)

    def test_target_example(self):
        self.assertEqual(parse_command("open Firefox in workspace three"), Command("open", "firefox", 3))

    def test_recognizer_can_omit_preposition_before_workspace(self):
        self.assertEqual(parse_command("open terminal workspace three"), Command("open", "terminal", 3))
        self.assertEqual(parse_command("open terminal desktop 3 in the background"), Command("open", "terminal", 3, False))

    def test_spoken_workspace_numbers(self):
        for number, word in NUMBERS.items():
            with self.subTest(number=number):
                self.assertEqual(parse_command(f"open terminal on desktop {word}").workspace, number)
        self.assertEqual(parse_command("open terminal in twenty-one").workspace, 21)

    def test_digit_workspace_range(self):
        for number in (1, 3, 99, 1000, MAX_WORKSPACE):
            with self.subTest(number=number):
                self.assertEqual(parse_command(f"open browser in {number}").workspace, number)

    def test_new_background_and_politeness(self):
        self.assertEqual(
            parse_command("Please, OPEN a new Terminal in workspace 3 in the background, please!"),
            Command("open", "terminal", 3, focus=False, new=True),
        )

    def test_new_without_workspace(self):
        self.assertEqual(parse_command("launch new terminal"), Command("open", "terminal", new=True))
        self.assertEqual(parse_command("open a terminal"), Command("open", "terminal"))

    def test_background_without_workspace(self):
        self.assertEqual(parse_command("start spotify in the background"), Command("open", "spotify", focus=False))

    def test_multiword_app(self):
        self.assertEqual(parse_command("show Visual Studio Code on 12"), Command("open", "visual studio code", 12))

    def test_move_commands(self):
        for subject in ("this", "this window", "current window", "the current window"):
            for verb in ("move", "send"):
                with self.subTest(subject=subject, verb=verb):
                    self.assertEqual(parse_command(f"{verb} {subject} to workspace three"), Command("move", workspace=3))
        self.assertEqual(parse_command("move this to 3"), Command("move", workspace=3))

    def test_switch_commands(self):
        self.assertEqual(parse_command("switch to workspace nine"), Command("switch", workspace=9))
        self.assertEqual(parse_command("go to desktop 2"), Command("switch", workspace=2))

    def test_named_workspaces_are_configuration(self):
        aliases = {"work": 3, "deep work": 12}
        self.assertEqual(parse_command("open browser in workspace work", aliases), Command("open", "browser", 3))
        self.assertEqual(parse_command("open browser in work in the background", aliases), Command("open", "browser", 3, False))
        self.assertEqual(parse_command("move this to deep work", aliases), Command("move", workspace=12))
        self.assertEqual(parse_command("go to workspace work", aliases), Command("switch", workspace=3))
        with self.assertRaises(ValueError):
            parse_command("open browser in work")

    def test_invalid_workspace_configuration(self):
        for aliases in ({"three": 8}, {"work": 0}, {"work": True}, {"work": "3"}, {"work": MAX_WORKSPACE + 1}, {"work": 3, "WORK": 4}, {"work; reboot": 3}):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                parse_command("open browser", aliases)

    def test_rejects_unknown_or_incomplete_input(self):
        for text in ("", "please", "open", "workspace three", "open in workspace three", "move this", "do open firefox", "go to three", "open browser in workspace"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text)

    def test_rejects_negative_fractional_or_out_of_range_workspace(self):
        for value in ("0", "-3", "three point five", "3.5", "1/2", str(MAX_WORKSPACE + 1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_command(f"open firefox in workspace {value}")

    def test_rejects_negation_multiple_actions_and_extra_instructions(self):
        for text in (
            "don't open firefox", "do not open firefox", "never open firefox",
            "open firefox but don't move it", "open firefox and terminal",
            "open firefox then open terminal", "open firefox or terminal",
            "open firefox in three and four", "open firefox in three then stop",
            "open firefox in three please close everything", "open firefox without focusing",
            "open firefox in three unless terminal is there", "open firefox in three? open terminal",
            "open firefox; reboot", "open $(reboot)", "open firefox && touch /tmp/x",
            "open firefox\nopen terminal", "open firefox in workspace three in workspace four",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_command(text)

    def test_grammar_helper_only_contains_catalog_aliases(self):
        class Catalog:
            aliases = {"terminal": ("terminal.desktop",), "firefox": ("firefox.desktop",)}
        self.assertEqual(grammar_phrases(Catalog()), ["firefox", "terminal"])


if __name__ == "__main__":
    unittest.main()
