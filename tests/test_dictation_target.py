from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_core.apps import AppCatalog
from desktop_core.desktop import capture_window
from dictation_target import Destination, parse_start, prepare_destination


class Targets(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        for name,exe in [('Teams','teams'),('Chromium','chromium'),('Editor','editor')]:
            (root/(name.lower()+'.desktop')).write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec={exe}\n')
        self.catalog=AppCatalog([root],default_browser='chromium.desktop')
        self.old={'address':'0x1','stableId':1}; self.new={'address':'0x2','stableId':2}
        self.current=self.old; self.layers={}; self.locked='false'; self.awake=True
        self.desktop=Mock(); self.desktop.catalog=self.catalog
        self.planner=Mock(desktop=self.desktop,catalog=self.catalog)
        self.desktop.hypr.query.side_effect=lambda cmd: self.current if cmd=='activewindow' else self.layers if cmd=='layers' else [self.old,self.new] if cmd=='clients' else [{'name':'DP-1','focused':True,'dpmsStatus':self.awake}]
        self.desktop.hypr.request.side_effect=lambda cmd:self.locked
        def opened(*args):
            self.current=self.new
            return {'ok':True,'window':'0x2','stable_id':2}
        self.desktop.execute.side_effect=opened
        for item in [patch('dictation_target.shutil.which',return_value='/test/wtype'),
                     patch('dictation.time.sleep')]:
            item.start(); self.addCleanup(item.stop)
        runner=patch('dictation.subprocess.run',return_value=Mock(returncode=0))
        self.run=runner.start(); self.addCleanup(runner.stop)

    def prepare(self,destination,**kwargs):
        return prepare_destination(destination,self.planner,capture_window(self.old),**kwargs)

    def test_target_phrases_and_fields(self):
        for phrase in ['dictate in teams','dictate this into Teams','please open dictation in the Teams app',
                       'dictated teams','dictation teams','dictating in teams']:
            self.assertEqual(parse_start(phrase,self.catalog),Destination('teams.desktop','compose'))
        for phrase in ['dictate in browser address bar',"dictate into the browser's address bar",
                       'dictate in the address bar','start dictation in chromium url bar',
                       'dictating browser address bar']:
            self.assertEqual(parse_start(phrase,self.catalog),Destination('chromium.desktop','address'))
        self.assertEqual(parse_start('dictate in editor',self.catalog),Destination('editor.desktop'))
        self.assertEqual(parse_start('dictate this',self.catalog),Destination())

    def test_negation_unknown_apps_and_extra_clauses(self):
        for phrase in ['do not dictate in teams','explain dictation in teams']:
            self.assertIsNone(parse_start(phrase,self.catalog))
        for phrase in ['dictate in unknown app','dictate in teams and close browser','dictate in teams address bar']:
            with self.assertRaises(ValueError): parse_start(phrase,self.catalog)

    def test_browser_focuses_address_bar_without_text_or_enter(self):
        target=self.prepare(Destination('chromium.desktop','address'))
        self.assertEqual(target,capture_window(self.new))
        self.assertEqual(self.run.call_args.args[0],['wtype','-M','ctrl','-k','l','-m','ctrl'])
        self.assertNotIn('input',self.run.call_args.kwargs)

    def test_teams_uses_web_compose_shortcut_without_sending(self):
        self.prepare(Destination('teams.desktop','compose'))
        self.assertEqual(self.run.call_args.args[0],['wtype','-M','alt','-M','shift','-k','r','-m','shift','-m','alt'])

    def test_generic_app_focus_keeps_its_existing_input_field(self):
        self.prepare(Destination('editor.desktop')); self.run.assert_not_called()

    def test_cancelled_before_focus_does_nothing(self):
        self.assertIsNone(self.prepare(Destination('teams.desktop','compose'),valid=lambda:False))
        self.desktop.execute.assert_not_called(); self.run.assert_not_called()

    def test_cancel_during_open_skips_field_shortcut(self):
        valid=[True]
        def opened(*args):
            self.current=self.new; valid[0]=False
            return {'ok':True,'window':'0x2','stable_id':2}
        self.desktop.execute.side_effect=opened
        self.assertIsNone(self.prepare(Destination('teams.desktop','compose'),valid=lambda:valid[0]))
        self.run.assert_not_called()

    def test_lock_sleep_and_changed_focus_prevent_open(self):
        for change in ['lock','sleep','focus']:
            self.locked='true' if change=='lock' else 'false'
            self.awake=change!='sleep'; self.current=self.new if change=='focus' else self.old
            with self.assertRaises(ValueError): self.prepare(Destination('teams.desktop','compose'))
        self.desktop.execute.assert_not_called(); self.run.assert_not_called()

    def test_keyboard_panel_prevents_shortcut_leaking_to_it(self):
        self.layers={'DP-1':{'levels':{'3':[{'namespace':'omarchy-keyboard-panel','address':'0xa'}]}}}
        with self.assertRaises(ValueError): self.prepare(Destination('teams.desktop','compose'))
        self.run.assert_not_called()

    def test_focus_changed_during_shortcut_prevents_capture(self):
        def keys(*args,**kwargs): self.current=self.old
        self.run.side_effect=keys
        with self.assertRaises(ValueError): self.prepare(Destination('teams.desktop','compose'))

    def test_background_launch_preference_still_focuses_explicit_destination(self):
        self.desktop.execute.side_effect=None
        self.desktop.execute.return_value={'ok':True,'window':'0x2','stable_id':2}
        self.desktop.place.side_effect=lambda *args:setattr(self,'current',self.new)
        self.prepare(Destination('editor.desktop'))
        self.desktop.place.assert_called_once_with(self.new,None,True)


if __name__=='__main__': unittest.main()
