"""Exercise the real installer and pip offline against tiny wheel fixtures."""
from contextlib import redirect_stdout
import hashlib
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import install


class HashEnforcement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.wheels = self.root/'wheels'
        self.wheels.mkdir()
        self.data = self.base/'data'
        self.python = self.data/'voicebind/venv/bin/python'
        for mock in (patch.object(install, 'ROOT', self.root),
                     patch.dict(os.environ, {'XDG_DATA_HOME': str(self.data)})):
            mock.start()
            self.addCleanup(mock.stop)

    def wheel(self):
        path = self.wheels/'numpy-2.5.3-py3-none-any.whl'
        with zipfile.ZipFile(path, 'w') as wheel:
            wheel.writestr('numpy/__init__.py', '__version__ = "2.5.3"\n')
            wheel.writestr('numpy-2.5.3.dist-info/METADATA',
                          'Metadata-Version: 2.1\nName: numpy\nVersion: 2.5.3\n')
            wheel.writestr('numpy-2.5.3.dist-info/WHEEL',
                          'Wheel-Version: 1.0\nGenerator: voicebind-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            wheel.writestr('numpy-2.5.3.dist-info/RECORD', '')
        return path

    def lock(self, artifact, hashed=True):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (self.root/'requirements.txt').write_text(
            f'--no-index\n--find-links {self.wheels}\nnumpy==2.5.3'
            +(f' --hash=sha256:{digest}' if hashed else '')+'\n')

    def setup_backend(self):
        # Capture pip output while exercising the production install flags.
        run = subprocess.run
        def captured(*args, **kwargs):
            return run(*args, **kwargs, capture_output=True, text=True, timeout=60)
        with patch('install.subprocess.run', side_effect=captured):
            install.prepare_python()

    def test_tampered_wheel_rejected_even_when_matching_version_is_installed(self):
        wheel = self.wheel()
        self.lock(wheel)
        self.setup_backend()
        result = subprocess.check_output([str(self.python), '-I', '-c', 'import numpy; print(numpy.__version__)'], text=True)
        self.assertEqual(result.strip(), '2.5.3')
        wheel.write_bytes(wheel.read_bytes()+b'tampered artifact')
        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.setup_backend()
        self.assertIn('DO NOT MATCH THE HASHES', error.exception.stderr)
        # Failed verification must leave the already installed good copy intact.
        result = subprocess.check_output([str(self.python), '-I', '-c', 'import numpy; print(numpy.__version__)'], text=True)
        self.assertEqual(result.strip(), '2.5.3')

    def test_missing_hash_is_rejected_by_installer(self):
        self.lock(self.wheel(), hashed=False)
        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.setup_backend()
        self.assertIn('Hashes are required', error.exception.stderr)

    def test_source_archive_is_never_built_even_with_a_valid_hash(self):
        path = self.wheels/'numpy-2.5.3.tar.gz'
        marker = self.root/'source-build-ran'
        script = f'from pathlib import Path\nPath({str(marker)!r}).touch()\n'.encode()
        with tarfile.open(path, 'w:gz') as archive:
            info = tarfile.TarInfo('numpy-2.5.3/setup.py')
            info.size = len(script)
            archive.addfile(info, BytesIO(script))
        self.lock(path)
        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.setup_backend()
        self.assertIn('No matching distribution', error.exception.stderr)
        self.assertFalse(marker.exists())


class SupportedRuntimes(unittest.TestCase):
    def test_unsupported_runtime_fails_before_any_setup(self):
        for mocked in (patch.object(install.sys, 'version_info', (3, 11)),
                       patch.object(install.sys, 'version_info', (3, 15)),
                       patch.object(install.sys, 'platform', 'darwin'),
                       patch('install.platform.machine', return_value='riscv64'),
                       patch('install.platform.libc_ver', return_value=('musl', '1.2.5')),
                       patch('install.platform.libc_ver', return_value=('glibc', '2.26')),
                       patch('install.sysconfig.get_config_var', return_value=1)):
            with mocked, patch.object(install, 'prepare_python') as prepare, \
                 patch.object(install, 'write_service') as service, \
                 patch.object(install.sys, 'argv', ['install.py']), redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'Verified wheels require'):
                    install.main()
                prepare.assert_not_called()
                service.assert_not_called()
