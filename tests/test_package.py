"""The public checkout must be a self-contained Omarchy root plugin."""
import json
from pathlib import Path
import unittest

from runtime_paths import PLUGIN_ID

ROOT = Path(__file__).resolve().parents[1]


class Package(unittest.TestCase):
    def test_manifest_and_entry_points(self):
        paths = [*ROOT.glob('manifest.json'), *ROOT.glob('*/manifest.json')]
        self.assertEqual(paths, [ROOT/'manifest.json'])
        manifest = json.loads(paths[0].read_text())
        self.assertEqual(manifest['id'], PLUGIN_ID)
        for field in ('author', 'name', 'version', 'description', 'license'):
            self.assertTrue(manifest[field])
        self.assertEqual(manifest['schemaVersion'], 1)
        for entry in manifest['entryPoints'].values():
            path = ROOT/entry
            self.assertTrue(path.resolve().is_relative_to(ROOT))
            self.assertTrue(path.is_file())
        self.assertIn('moduleName: "'+PLUGIN_ID+'"', (ROOT/manifest['entryPoints']['barWidget']).read_text())
