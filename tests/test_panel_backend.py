from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import panel_backend as panel
from configuration import DEFAULTS, config_path, load_config, save_config


class PanelSettings(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for p in [patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root), 'JEV_VOICE_CONFIG': str(self.root/'jev-voice/config.toml')}),
                  patch.object(panel, 'idle'),
                  patch.object(panel, 'service', return_value=SimpleNamespace(returncode=1))]:
            p.start(); self.addCleanup(p.stop)
        save_config(deepcopy(DEFAULTS))

    def test_save_preserves_advanced_settings_and_private_key(self):
        cfg = load_config(); cfg['window_classes'] = {'teams': ['my-teams']}; save_config(cfg)
        doc = {'voice': {'wake_phrase':'laptop'}, 'recognition': {'end_silence_ms': 320}, 'phrases': {'writing time':'dictate'}}
        result = panel.save({'config': doc, 'revision': panel.revision(panel.raw_config()), 'api_key':'private-test-key'})
        self.assertIn('Saved', result)
        saved = load_config()
        self.assertEqual(saved['voice']['wake_phrase'], 'laptop')
        self.assertEqual(saved['window_classes'], cfg['window_classes'])
        self.assertEqual(saved['phrases'], doc['phrases'])
        key = self.root/'jev-voice/api-key'
        self.assertEqual(key.read_text().strip(), 'private-test-key')
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(config_path().stat().st_mode & 0o777, 0o600)

    def test_stale_or_invalid_saves_do_not_change_settings_or_credentials(self):
        raw = panel.raw_config()
        requests = [
            {'config': {'voice': {'wake_phrase':'laptop'}}, 'revision':'stale'},
            {'config': {'phrases': {'launch stuff':'run arbitrary shell'}}, 'revision': panel.revision(raw)},
            {'config': {}, 'revision': panel.revision(raw), 'api_key':'key with spaces'}]
        for data in requests:
            with self.subTest(data=data), self.assertRaises(ValueError): panel.save(data)
            self.assertEqual(panel.raw_config(), raw)
            self.assertFalse((self.root/'jev-voice/api-key').exists())

    def test_saved_configuration_is_restarted_only_when_listener_running(self):
        with patch.object(panel, 'service', return_value=SimpleNamespace(returncode=0)) as service:
            panel.save({'config':{}, 'revision':panel.revision(panel.raw_config())})
            self.assertEqual(service.call_args_list[-1].args, ('try-restart',))

    def test_unknown_requests_cannot_execute_commands(self):
        with self.assertRaisesRegex(ValueError, 'Unknown'): panel.handle({'action':'run','command':['touch','/tmp/not-allowed']})

    def test_snapshot_never_returns_key_value(self):
        with patch.object(panel, 'load_key', return_value='private-secret-value'):
            doc = panel.snapshot()
        self.assertTrue(doc['key_configured'])
        self.assertNotIn('private-secret-value', json.dumps(doc))


if __name__ == '__main__': unittest.main()
