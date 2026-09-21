from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import desktop_integration as integration


class Integration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name); self.project=self.root/'project'; self.config=self.root/'config'
  (self.project/'omarchy-plugin').mkdir(parents=True)
  (self.project/'omarchy-plugin/Service.qml').write_text('test fixture')
  (self.project/'omarchy-plugin/BarWidget.qml').write_text('bar fixture')
  (self.project/'omarchy-plugin/Field.qml').write_text('field fixture')
  (self.project/'manifest.json').write_text(json.dumps({'entryPoints':{'service':'omarchy-plugin/Service.qml','barWidget':'omarchy-plugin/BarWidget.qml'}}))
  (self.config/'hypr').mkdir(parents=True); (self.config/'omarchy').mkdir()
  self.bindings=self.config/'hypr/bindings.lua'; self.shell=self.config/'omarchy/shell.json'
  self.old='-- BEGIN omarchy-voice managed shortcuts\no.bind("F10", "Voicebind", "voicebind start")\n-- END omarchy-voice managed shortcuts'
  self.bindings.write_text('-- personal binding\n'+self.old+'\n-- personal suffix\n')
  self.shell.write_text(json.dumps({'plugins':[{'id':'other.plugin'}],'bar':{'position':'left','layout':{'right':[{'id':'before'},{'id':'voicebind'},{'id':'after'}]}}}))
  self.bin=self.root/'bin'; self.bin.mkdir()
  (self.bin/'voicebind').write_text('#!/bin/sh\n# legacy entry point\n')
  for p in [patch.object(integration,'ROOT',self.project),patch.object(integration,'BIN_DIR',self.bin),patch.dict('os.environ',{'XDG_CONFIG_HOME':str(self.config),'XDG_DATA_HOME':str(self.root/'data'),'XDG_STATE_HOME':str(self.root/'state')}),
            patch.object(integration,'environment',return_value={}),
            patch('desktop_integration.subprocess.run',return_value=SimpleNamespace(stdout=''))]:
   p.start(); self.addCleanup(p.stop)
 def apply(self, original=False):
  with redirect_stdout(StringIO()): integration.integrate(original)
 def test_install_is_idempotent_and_preserves_other_settings(self):
  self.apply(); first=self.bindings.read_text(); self.apply()
  self.assertEqual(self.bindings.read_text(),first)
  self.assertIn('-- personal binding',first); self.assertIn('-- personal suffix',first)
  self.assertIn('hl.unbind("F10")',first); self.assertIn('voicebind release',first)
  self.assertEqual((self.bin/'voicebind').readlink(),self.project/'voice-control')
  cfg=json.loads(self.shell.read_text()); self.assertEqual(cfg['bar']['position'],'left')
  self.assertEqual(cfg['plugins'],[{'id':'other.plugin'},{'id':integration.PLUGIN_ID}])
  self.assertEqual(cfg['bar']['layout']['right'],[{'id':'before'},{'id':integration.PLUGIN_ID},{'id':'after'}])
  self.assertIn('voicebind',cfg['disabledPlugins'])
  launcher=self.root/'data/applications/voicebind-settings.desktop'
  self.assertIn(str(self.project/'voice-control'),launcher.read_text())
 def test_original_restoration_does_not_accumulate_unbinds(self):
  self.apply(); self.apply(True); first=self.bindings.read_text()
  self.assertIn('voicebind start',first); self.assertNotIn('jev-voice press',first)
  self.assertIn('legacy entry point',(self.bin/'voicebind').read_text())
  self.apply(); self.apply(True)
  self.assertEqual(self.bindings.read_text(),first)
  self.assertEqual(json.loads(self.shell.read_text())['plugins'],[{'id':'other.plugin'}])
  cfg=json.loads(self.shell.read_text())
  self.assertEqual(cfg['bar']['layout']['right'],[{'id':'before'},{'id':'voicebind'},{'id':'after'}])
  self.assertNotIn('voicebind',cfg['disabledPlugins'])
 def test_all_qml_files_share_new_revision_when_a_dependency_changes(self):
  self.apply(); dest=self.config/'omarchy/plugins'/integration.PLUGIN_ID
  first=json.loads((dest/'manifest.json').read_text())['entryPoints']
  self.assertEqual((dest/first['barWidget']).read_text(),'bar fixture')
  self.assertEqual((dest/first['service']).with_name('Field.qml').read_text(),'field fixture')
  (self.project/'omarchy-plugin/Field.qml').write_text('updated field')
  self.apply(); second=json.loads((dest/'manifest.json').read_text())['entryPoints']
  self.assertNotEqual(first['service'],second['service'])
  self.assertEqual((dest/second['barWidget']).with_name('Field.qml').read_text(),'updated field')
 def test_fresh_install_removal_preserves_personal_bindings(self):
  (self.bin/'voicebind').unlink()
  self.bindings.write_text('-- personal binding\no.bind("F10", "Personal action", "example")\n')
  self.shell.write_text(json.dumps({'plugins':[{'id':'other.plugin'}],'bar':{'layout':{'right':[{'id':'before'}]}}}))
  self.apply(); self.apply(True)
  self.assertIn('"Personal action"', self.bindings.read_text())
  self.assertNotIn('hl.unbind', self.bindings.read_text())
  self.assertFalse((self.bin/'voicebind').is_symlink())
  self.assertFalse((self.root/'data/applications/voicebind-settings.desktop').exists())
  cfg=json.loads(self.shell.read_text())
  self.assertEqual(cfg['plugins'],[{'id':'other.plugin'}])
  self.assertEqual(cfg['bar']['layout']['right'],[{'id':'before'}])
 def test_native_plugin_install_keeps_tracked_files_unchanged(self):
  dest=self.config/'omarchy/plugins'/integration.PLUGIN_ID
  import shutil
  shutil.copytree(self.project, dest)
  (dest/'.git').mkdir()
  before={str(p.relative_to(dest)):p.read_bytes() for p in dest.rglob('*') if p.is_file()}
  with patch.object(integration,'ROOT',dest): self.apply()
  after={str(p.relative_to(dest)):p.read_bytes() for p in dest.rglob('*') if p.is_file()}
  self.assertEqual(before,after)
  self.assertFalse(any(p.is_symlink() for p in dest.rglob('*')))
 def test_second_checkout_cannot_overwrite_managed_plugin(self):
  dest=self.config/'omarchy/plugins'/integration.PLUGIN_ID
  (dest/'.git').mkdir(parents=True)
  before=self.bindings.read_bytes(),self.shell.read_bytes(),(self.bin/'voicebind').read_bytes()
  with self.assertRaisesRegex(RuntimeError,'managed by Omarchy'): self.apply()
  self.assertEqual(before,(self.bindings.read_bytes(),self.shell.read_bytes(),(self.bin/'voicebind').read_bytes()))
 def test_removal_keeps_a_command_replaced_by_the_user(self):
  self.apply()
  (self.bin/'voicebind').unlink()
  (self.bin/'voicebind').write_text('personal replacement')
  self.apply(True)
  self.assertEqual((self.bin/'voicebind').read_text(),'personal replacement')
 def test_prepared_but_never_started_does_not_remove_existing_shortcuts(self):
  before=self.bindings.read_bytes(),self.shell.read_bytes()
  integration.install_command()
  self.apply(True)
  self.assertEqual(before,(self.bindings.read_bytes(),self.shell.read_bytes()))

if __name__=='__main__': unittest.main()
