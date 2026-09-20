import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_core.apps import AppCatalog, normalize_alias


class AppCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user = self.root / "user"
        self.system = self.root / "system"
        self.user.mkdir()
        self.system.mkdir()

    def entry(self, root, filename, name="Example", executable="example %U", extra=""):
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={executable}\n{extra}", encoding="utf-8")
        return path

    def catalog(self, **kwargs):
        return AppCatalog([self.user, self.system], **kwargs)

    def test_reads_and_resolves_desktop_entry(self):
        path = self.entry(self.system, "org.mozilla.firefox.desktop", "Firefox", "/usr/bin/firefox %U", "StartupWMClass=firefox\n")
        catalog = self.catalog()
        entry = catalog.resolve("FIREFOX")
        self.assertEqual(entry.id, "org.mozilla.firefox.desktop")
        self.assertEqual(entry.path, path)
        self.assertEqual(entry.exec, "/usr/bin/firefox %U")
        self.assertEqual(entry.wm_class, "firefox")
        self.assertEqual(catalog.resolve("org.mozilla.firefox.desktop"), entry)

    def test_user_override_wins(self):
        self.entry(self.system, "example.desktop", "System App")
        path = self.entry(self.user, "example.desktop", "User App")
        catalog = self.catalog()
        self.assertEqual(catalog.resolve("user app").path, path)
        with self.assertRaises(ValueError):
            catalog.resolve("system app")

    def test_hidden_override_masks_system_even_without_name(self):
        self.entry(self.system, "example.desktop")
        (self.user / "example.desktop").write_text("[Desktop Entry]\nHidden=true\n", encoding="utf-8")
        self.assertEqual(self.catalog().apps, {})

    def test_no_display_and_non_application_entries_are_excluded(self):
        self.entry(self.system, "visible.desktop")
        self.entry(self.system, "hidden.desktop", extra="Hidden=true\n")
        self.entry(self.system, "internal.desktop", extra="NoDisplay=true\n")
        self.entry(self.system, "link.desktop", extra="Type=Link\n")
        self.assertEqual(set(self.catalog().apps), {"visible.desktop"})

    def test_no_display_user_override_masks_system(self):
        self.entry(self.system, "example.desktop")
        self.entry(self.user, "example.desktop", extra="NoDisplay=true\n")
        self.assertEqual(self.catalog().apps, {})

    def test_ambiguous_names_require_choice(self):
        self.entry(self.system, "one.desktop", "Same Name", "one")
        self.entry(self.system, "two.desktop", "Same Name", "two")
        catalog = self.catalog()
        with self.assertRaisesRegex(ValueError, "ambiguous.*one.desktop.*two.desktop"):
            catalog.resolve("same name")
        self.assertEqual(catalog.resolve("one.desktop").id, "one.desktop")

    def test_explicit_alias_can_resolve_ambiguity(self):
        self.entry(self.system, "one.desktop", "Same Name", "one")
        self.entry(self.system, "two.desktop", "Same Name", "two")
        catalog = self.catalog(alias_overrides={"same name": "two.desktop", "my editor": "one.desktop"})
        self.assertEqual(catalog.resolve("same name").id, "two.desktop")
        self.assertEqual(catalog.resolve("my editor").id, "one.desktop")

    def test_invalid_alias_fails_clearly(self):
        for overrides in ({"browser": "missing.desktop"}, {"": "missing.desktop"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.catalog(alias_overrides=overrides)

    def test_default_browser_and_file_manager_take_precedence(self):
        self.entry(self.system, "firefox.desktop", "Firefox", "firefox")
        self.entry(self.system, "other.desktop", "Other Browser", "other-browser")
        self.entry(self.system, "strata.desktop", "Strata", "strata")
        self.entry(self.system, "thunar.desktop", "Thunar", "thunar")
        catalog = self.catalog(default_browser="other.desktop", default_file_manager="thunar.desktop")
        self.assertEqual(catalog.resolve("browser").id, "other.desktop")
        self.assertEqual(catalog.resolve("files").id, "thunar.desktop")
        self.assertEqual(catalog.resolve("file manager").id, "thunar.desktop")

    def test_environment_terminal_precedes_fallbacks(self):
        self.entry(self.system, "ghostty.desktop", "Ghostty", "ghostty")
        self.entry(self.system, "kitty.desktop", "Kitty", "kitty")
        with patch.dict(os.environ, {"TERMINAL": "/usr/bin/kitty"}):
            self.assertEqual(self.catalog().resolve("terminal").id, "kitty.desktop")

    def test_code_aliases(self):
        self.entry(self.system, "code.desktop", "Visual Studio Code", "code %F")
        catalog = self.catalog()
        for phrase in ("code", "vs code", "v s code", "Visual Studio Code"):
            self.assertEqual(catalog.resolve(phrase).id, "code.desktop")

    def test_nested_desktop_id(self):
        self.entry(self.system, "vendor/tool.desktop", "Tool", "tool")
        self.assertEqual(self.catalog().resolve("tool").id, "vendor-tool.desktop")

    def test_exec_is_only_stored(self):
        marker = self.root / "must-not-exist"
        executable = f"sh -c 'touch {marker}' %U"
        self.entry(self.system, "example.desktop", executable=executable)
        self.assertEqual(self.catalog().resolve("example").exec, executable)
        self.assertFalse(marker.exists())

    def test_malformed_entries_do_not_break_scan(self):
        (self.system / "malformed.desktop").write_text("not a desktop entry", encoding="utf-8")
        self.entry(self.system, "valid.desktop", "Valid", "valid")
        self.assertEqual(set(self.catalog().apps), {"valid.desktop"})

    def test_fixture_catalog_never_calls_desktop_utilities(self):
        with patch("desktop_core.apps.subprocess.run", side_effect=AssertionError("must not run")):
            self.catalog()

    def test_normalization(self):
        self.assertEqual(normalize_alias("  VS-Code "), "vs code")
        self.assertEqual(normalize_alias("org.example.App.desktop"), "org example app")

    def test_browser_name_wins_over_web_app_executable_alias(self):
        self.entry(self.system, "chromium.desktop", "Chromium", "/usr/bin/chromium %U")
        self.entry(self.user, "chrome-notes.desktop", "Notes", "/usr/bin/chromium --app-id=example")
        catalog = self.catalog()
        self.assertEqual(catalog.resolve("chromium").id, "chromium.desktop")
        self.assertEqual(catalog.resolve("notes").id, "chrome-notes.desktop")


if __name__ == "__main__":
    unittest.main()
