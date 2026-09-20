import unittest

from desktop_core.commands import Command
from desktop_core.plans import MAX_PLAN_CHARACTERS, MAX_PLAN_COMMANDS, parse_plan


class PlanTests(unittest.TestCase):
    def test_short_window_modes_are_complete_commands_in_chains(self):
        self.assertEqual(parse_plan("browser full screen"), (Command("fullscreen", "browser"),))
        self.assertEqual(parse_plan("open browser then browser fullscreen and terminal floating then terminal tiled"),
                         (Command("open", "browser"), Command("fullscreen", "browser"),
                          Command("float", "terminal"), Command("tile", "terminal")))
        self.assertEqual(parse_plan("browser restore and open terminal and file manager on workspace two"),
                         (Command("restore", "browser"), Command("open", "terminal", 2),
                          Command("open", "file manager", 2)))

    def test_short_modes_keep_configured_shortcut_priority_and_compound_targets(self):
        self.assertEqual(parse_plan("browser fullscreen", shortcuts={"browser fullscreen": "open browser"}),
                         (Command("open", "browser"),))
        self.assertEqual(parse_plan("notes and tasks full screen", app_aliases=("notes and tasks",)),
                         (Command("fullscreen", "notes and tasks"),))
        for phrase in ("browser fullscreen and terminal", "do not browser fullscreen", "browser not fullscreen"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                parse_plan(phrase)

    def test_requested_example(self):
        self.assertEqual(
            parse_plan("close files on workspace 1 and open chromium there then open a terminal and file manager on workspace 2"),
            (Command("close", "files", 1), Command("open", "chromium", 1),
             Command("open", "terminal", 2), Command("open", "file manager", 2)),
        )

    def test_shared_scope_for_coordinated_objects(self):
        self.assertEqual(
            parse_plan("open terminal and file manager on workspace 2"),
            (Command("open", "terminal", 2), Command("open", "file manager", 2)),
        )

    def test_independent_scopes_are_preserved(self):
        self.assertEqual(
            parse_plan("open chromium on workspace 1 and terminal on workspace 2"),
            (Command("open", "chromium", 1), Command("open", "terminal", 2)),
        )

    def test_scope_applies_backwards_to_unscoped_objects_only(self):
        self.assertEqual(
            parse_plan("open terminal and chromium on workspace 1 and files on workspace 2 and calculator"),
            (Command("open", "terminal", 1), Command("open", "chromium", 1),
             Command("open", "files", 2), Command("open", "calculator")),
        )

    def test_explicit_verbs_do_not_inherit_workspace(self):
        self.assertEqual(
            parse_plan("open terminal on workspace 1 and open chromium then open files on workspace 2"),
            (Command("open", "terminal", 1), Command("open", "chromium"), Command("open", "files", 2)),
        )

    def test_there_tracks_latest_workspace(self):
        self.assertEqual(
            parse_plan("open workspace 4 then open terminal there and open files on workspace 2 then move chromium there"),
            (Command("switch", workspace=4), Command("open", "terminal", 4),
             Command("open", "files", 2), Command("move", "chromium", 2)),
        )

    def test_there_prepositions_and_background(self):
        self.assertEqual(
            parse_plan("open terminal on workspace 3 then open files in there in the background then go there"),
            (Command("open", "terminal", 3), Command("open", "files", 3, focus=False),
             Command("switch", workspace=3)),
        )

    def test_there_shared_scope_and_workspace_alias(self):
        self.assertEqual(
            parse_plan("open workspace work then open terminal and files there", {"work": 6}),
            (Command("switch", workspace=6), Command("open", "terminal", 6), Command("open", "files", 6)),
        )

    def test_folder_aliases_and_shared_scope(self):
        self.assertEqual(
            parse_plan("open the projects folder and terminal on workspace 2", folder_aliases={"projects": "~/Projects"}),
            (Command("folder", workspace=2, folder="projects"), Command("open", "terminal", 2)),
        )

    def test_window_and_volume_actions_can_be_chained(self):
        self.assertEqual(
            parse_plan("focus chromium then make chromium full screen and volume down then mute"),
            (Command("focus", "chromium"), Command("fullscreen", "chromium"),
             Command("volume", value=-5, relative=True), Command("mute")),
        )

    def test_close_coordinated_apps(self):
        self.assertEqual(
            parse_plan("close terminal and files on workspace 1 then open chromium there"),
            (Command("close", "terminal", 1), Command("close", "files", 1), Command("open", "chromium", 1)),
        )

    def test_supported_connectors_and_politeness(self):
        for connector in (" and ", " then ", " and then ", ", ", ", then ", ", and ", ", and then "):
            with self.subTest(connector=connector):
                self.assertEqual(
                    parse_plan("Please, open terminal" + connector + "open files, please!"),
                    (Command("open", "terminal"), Command("open", "files")),
                )

    def test_single_command_is_a_one_item_plan(self):
        self.assertEqual(parse_plan("move herdr to workspace 3"), (Command("move", "herdr", 3),))

    def test_single_parser_variants_remain_available(self):
        for text, expected in (
            ("sound on", Command("unmute")),
            ("sound off", Command("mute")),
            ("un mute the sound", Command("unmute")),
            ("turn the sound on", Command("unmute")),
            ("focus on chromium", Command("focus", "chromium")),
            ("full screen", Command("fullscreen")),
            ("exit full screen", Command("restore")),
            ("Please, set the volume to twenty, please!", Command("volume", value=20)),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_plan(text), (expected,))

    def test_trusted_compound_app_alias_stays_one_app(self):
        self.assertEqual(
            parse_plan("open fish and chips then open terminal", app_aliases={"fish and chips", "terminal"}),
            (Command("open", "fish and chips"), Command("open", "terminal")),
        )

    def test_compound_alias_can_be_a_coordinated_object(self):
        self.assertEqual(
            parse_plan("open terminal and fish and chips on workspace 2", app_aliases={"fish and chips", "terminal"}),
            (Command("open", "terminal", 2), Command("open", "fish and chips", 2)),
        )

    def test_catalog_alias_with_spaced_hyphen(self):
        self.assertEqual(
            parse_plan("open code - oss and file manager on workspace 2", app_aliases={"code oss", "file manager"}),
            (Command("open", "code oss", 2), Command("open", "file manager", 2)),
        )

    def test_app_alias_does_not_shadow_workspace_or_folder_names(self):
        self.assertEqual(
            parse_plan("open terminal on workspace deep work then open the my projects folder",
                       {"deep work": 3}, {"my projects": "~/Projects"},
                       app_aliases={"deep work", "my projects"}),
            (Command("open", "terminal", 3), Command("folder", folder="my projects")),
        )

    def test_ambiguous_compound_alias_fails(self):
        with self.assertRaisesRegex(ValueError, "also describes separate apps"):
            parse_plan("open fish and chips", app_aliases={"fish", "chips", "fish and chips"})

    def test_catalog_alias_cannot_hide_negation_or_unsupported_actions(self):
        for text, aliases in (
            ("open browser and reboot", {"browser and reboot"}),
            ("open browser and do not close files", {"browser and do not close files"}),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_plan(text, app_aliases=aliases)

    def test_rejects_unbound_or_misplaced_there(self):
        for text in (
            "open chromium there", "open terminal there and files on workspace 2",
            "open workspace 2 then open there chromium", "open workspace 2 then open chromium there there",
        ):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "[Tt]here"):
                parse_plan(text)

    def test_rejects_complete_plan_if_any_part_is_invalid(self):
        for text in (
            "open terminal then reboot", "open terminal and don't open chromium",
            "open terminal and open", "open terminal then open open files",
            "open terminal then open files without focusing", "open terminal and then",
            "and open terminal", "open terminal,", "open terminal and and files",
            "open workspace 2 and terminal", "focus terminal and files",
            "open terminal then files", "open terminal then open files on workspace zero",
            "close file on workspace one and open chromium and open a terminal on file manager on workspace two",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_plan(text)

    def test_rejects_shell_characters_before_splitting(self):
        for text in (
            "open terminal; open files", "open terminal && open files",
            "open terminal\nopen files", "open terminal and open $(reboot)",
            "open terminal and open files | reboot", "open terminal and open /tmp/file",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_plan(text)

    def test_plan_limit_counts_expanded_objects(self):
        at_limit = "open " + " and ".join(["terminal"] * MAX_PLAN_COMMANDS)
        self.assertEqual(len(parse_plan(at_limit)), MAX_PLAN_COMMANDS)
        with self.assertRaisesRegex(ValueError, "at most 12"):
            parse_plan(at_limit + " and terminal")
        with self.assertRaisesRegex(ValueError, "at most 12"):
            parse_plan(" then ".join(["open terminal"] * (MAX_PLAN_COMMANDS + 1)))

    def test_empty_and_oversized_input(self):
        for text in ("", "please", None, "open " + "a" * 3600):
            with self.subTest(text=str(text)[:20]), self.assertRaises(ValueError):
                parse_plan(text)

    def test_shortcut_expands_to_the_same_four_step_plan(self):
        expansion = "close files on workspace 1 and open chromium there then open a terminal and file manager on workspace 2"
        self.assertEqual(
            parse_plan("work mode", shortcuts={"work mode": expansion}),
            (Command("close", "files", 1), Command("open", "chromium", 1),
             Command("open", "terminal", 2), Command("open", "file manager", 2)),
        )

    def test_shortcut_matches_case_space_and_politeness(self):
        for phrase in ("work mode", "  WORK   mode  ", "Please, work mode!", "work mode, please."):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    parse_plan(phrase, shortcuts={"Work   Mode": "open terminal"}),
                    (Command("open", "terminal"),),
                )

    def test_shortcut_uses_existing_aliases_and_guards(self):
        self.assertEqual(
            parse_plan("project mode", {"work": 3}, {"projects": "~/Projects"},
                       app_aliases={"fish and chips"}, shortcuts={
                           "project mode": "open fish and chips on workspace work then open the projects folder there"
                       }),
            (Command("open", "fish and chips", 3), Command("folder", workspace=3, folder="projects")),
        )
        with self.assertRaisesRegex(ValueError, "also describes separate apps"):
            parse_plan("game mode", app_aliases={"fish", "chips", "fish and chips"},
                       shortcuts={"game mode": "open fish and chips"})

    def test_invalid_shortcut_value_rejects_the_entire_plan(self):
        for expansion in (
            "open terminal then reboot", "open terminal and don't close files",
            "open terminal then open", "open files there", "open terminal; reboot",
            "open terminal\nopen files", "", None,
        ):
            with self.subTest(expansion=expansion), self.assertRaises(ValueError):
                parse_plan("work mode", shortcuts={"work mode": expansion})

    def test_shortcuts_do_not_bypass_limits(self):
        for expansion in (
            "open " + "a" * MAX_PLAN_CHARACTERS,
            " then ".join(["open terminal"] * (MAX_PLAN_COMMANDS + 1)),
        ):
            with self.subTest(length=len(expansion)), self.assertRaisesRegex(ValueError, "at most 12"):
                parse_plan("work mode", shortcuts={"work mode": expansion})

    def test_shortcut_names_cannot_contain_shell_operators(self):
        for phrase in ("work; reboot", "work && reboot", "work\nmode", "work $(reboot)"):
            with self.subTest(phrase=phrase), self.assertRaises(ValueError):
                parse_plan(phrase, shortcuts={phrase: "open terminal"})

    def test_shortcut_name_only_matches_the_entire_utterance(self):
        for phrase in (
            "work mode then open terminal", "open terminal then work mode",
            "open terminal and work mode", "work mode and game mode",
            "open terminal then work and play",
        ):
            with self.subTest(phrase=phrase), self.assertRaisesRegex(ValueError, "complete command"):
                parse_plan(phrase, shortcuts={
                    "work mode": "open terminal", "game mode": "open chromium",
                    "work and play": "open files",
                })
        self.assertEqual(
            parse_plan("work mode then open terminal", shortcuts={
                "work mode": "open terminal", "work mode then open terminal": "open files",
            }),
            (Command("open", "files"),),
        )

    def test_shortcuts_do_not_recurse(self):
        for shortcuts in (
            {"work mode": "game mode", "game mode": "open chromium"},
            {"work mode": "work mode"},
            {"work mode": "open terminal then game mode", "game mode": "open chromium"},
        ):
            with self.subTest(shortcuts=shortcuts), self.assertRaisesRegex(ValueError, "Nested shortcuts"):
                parse_plan("work mode", shortcuts=shortcuts)

    def test_normalized_shortcut_collisions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting voice shortcuts"):
            parse_plan("work mode", shortcuts={"Work Mode": "open terminal", "work   mode": "open files"})
        self.assertEqual(
            parse_plan("work mode", shortcuts={"Work Mode": "Open Terminal", "work   mode": "open  terminal"}),
            (Command("open", "terminal"),),
        )

    def test_ordinary_built_in_commands_are_unchanged(self):
        shortcuts = {"work mode": "open terminal then open files on workspace 2"}
        for phrase in ("open terminal", "sound off", "open workspace 3 then open chromium there"):
            with self.subTest(phrase=phrase):
                self.assertEqual(parse_plan(phrase, shortcuts=shortcuts), parse_plan(phrase))


if __name__ == "__main__":
    unittest.main()
