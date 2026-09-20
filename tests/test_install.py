"""Release setup regressions: no live desktop, network or personal files."""
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install


class Setup(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for mock in (patch.object(install, 'ROOT', self.root/'voice project'),
                     patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root/'config')})):
            mock.start()
            self.addCleanup(mock.stop)

    def test_service_uses_checkout_path_and_escapes_specifiers(self):
        rendered = install.service_text(Path('/tmp/a space/100%/$project'))
        self.assertIn('WorkingDirectory=/tmp/a space/100%%/$project', rendered)
        self.assertIn('ExecStart="/tmp/a space/100%%/$$project/run.sh" --live', rendered)
        self.assertIn('UMask=0077', rendered)

    def test_service_install_and_update_preserve_original(self):
        path = self.root/'config/systemd/user/jev-voice.service'
        path.parent.mkdir(parents=True)
        path.write_text('previous service')
        install.write_service()
        install.write_service()
        self.assertEqual(path.read_text(), install.service_text(install.ROOT))
        self.assertEqual((install.ROOT/'backups/previous-jev-voice.service').read_text(), 'previous service')

    def test_model_is_verified_before_replacing_and_temporary_file_removed(self):
        source = self.root/'model.bin'
        source.write_bytes(b'valid test model')
        with patch.object(install, 'MODEL_SHA256', hashlib.sha256(source.read_bytes()).hexdigest()):
            install.prepare_model(source)
            target = install.ROOT/'models/ggml-base.en.bin'
            self.assertEqual(target.read_bytes(), source.read_bytes())
            target.write_bytes(b'old model')
            source.write_bytes(b'corrupt download')
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                install.prepare_model(source)
            self.assertEqual(target.read_bytes(), b'old model')
            self.assertEqual(list(target.parent.glob('.download-*')), [])

    def test_model_download_uses_verification_too(self):
        from io import BytesIO
        data = b'download fixture'
        with patch.object(install, 'MODEL_SHA256', hashlib.sha256(data).hexdigest()), \
             patch('install.urllib.request.urlopen', return_value=BytesIO(data)) as request, \
             redirect_stdout(StringIO()):
            install.prepare_model()
        request.assert_called_once_with(install.MODEL_URL, timeout=60)
        self.assertEqual((install.ROOT/'models/ggml-base.en.bin').read_bytes(), data)

    def test_missing_desktop_is_rejected_before_setup(self):
        with patch('install.shutil.which', return_value='/bin/example'):
            with self.assertRaisesRegex(RuntimeError, 'Requires Omarchy'):
                install.check()
        self.assertFalse(install.ROOT.exists())

    def test_uninstall_refuses_service_from_another_checkout(self):
        path = self.root/'config/systemd/user/jev-voice.service'
        path.parent.mkdir(parents=True)
        path.write_text('some other service')
        with patch('install.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'another checkout'):
                install.uninstall()
        run.assert_not_called()
        self.assertEqual(path.read_text(), 'some other service')
