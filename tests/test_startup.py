"""Exercise the CLI on a fresh machine using fake system commands."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class FirstStartup(unittest.TestCase):
    def test_start_without_a_legacy_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root/'checkout'
            project.mkdir()
            (root/'data/voicebind/venv/bin').mkdir(parents=True)
            (root/'data/voicebind/venv/bin/python').symlink_to(sys.executable)
            command = project/'voice-control'
            shutil.copy2(Path(__file__).resolve().parents[1]/'voice-control', command)
            shutil.copy2(Path(__file__).resolve().parents[1]/'paths.sh', project/'paths.sh')
            (project/'desktop_integration.py').write_text('import sys\nassert sys.argv[1] == "install"\n')
            (project/'control.py').write_text('print("status fixture")\n')
            runtime = root/'runtime/jev-voice'
            runtime.mkdir(parents=True)
            bin_dir = root/'bin'
            bin_dir.mkdir()
            systemctl = bin_dir/'systemctl'
            systemctl.write_text('#!'+sys.executable+'\n'+'''
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['TEST_CALLS'], 'a') as stream:
    stream.write(json.dumps(args)+'\\n')
if 'omarchy-voice.service' in args:
    if 'cat' in args or 'is-enabled' in args:
        sys.exit(1)
    sys.exit('A missing legacy service must not be controlled')
if 'enable' in args:
    path = pathlib.Path(os.environ['XDG_RUNTIME_DIR'])/'jev-voice/status.json'
    path.write_text(json.dumps({'pid':123, 'state':'listening'}))
if 'show' in args:
    print('123')
''')
            systemctl.chmod(0o755)
            pactl = bin_dir/'pactl'
            pactl.write_text('#!/bin/sh\nprintf "[]\\n"\n')
            pactl.chmod(0o755)
            calls = root/'calls.jsonl'
            env = dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ.get('PATH',''),
                       XDG_RUNTIME_DIR=str(root/'runtime'), XDG_DATA_HOME=str(root/'data'),
                       XDG_STATE_HOME=str(root/'state'), TEST_CALLS=str(calls))
            result = subprocess.run([str(command), 'start'], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            actions = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertIn(['--user','enable','--now','jev-voice.service'], actions)
            for args in actions:
                if 'omarchy-voice.service' in args:
                    self.assertTrue('cat' in args or 'is-enabled' in args, args)
