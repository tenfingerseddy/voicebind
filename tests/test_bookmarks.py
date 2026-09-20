import copy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bookmarks import Bookmarks, Store, name_key, validate_entries
from daemon import Engine
from desktop_core.desktop import WindowTarget
from tests.test_pipeline import Fixture
from personalization import expand_phrase, validate_phrases
from configuration import DEFAULTS, save_config, load_config


def window(address='0x1', stable=11, title='Alpha', app='teams', workspace=1):
    return dict(address=address,stableId=stable,title=title,**{'class':app},initialClass=app,
                workspace={'id':workspace},monitor=0,at=[100,100],size=[600,400],mapped=True,
                swallowing='0x0',floating=False,fullscreen=0,fullscreenClient=0,focusHistoryID=0)


class BookmarksTests(Fixture):
    def setUp(self):
        super().setUp()
        self.clients=[window(),window('0x2',22,'Beta','files',2)]
        self.monitors=[dict(id=0,name='screen',x=0,y=0,width=1600,height=1000,scale=1,
                            activeWorkspace={'id':1},dpmsStatus=True)]
        self.hypr=Mock(); self.hypr.directory='/run/hypr/session'
        self.hypr.request.return_value='false'
        self.hypr.query.side_effect=self.query
        from plan import Planner
        self.planner=Planner(self.catalog,self.cfg,self.hypr)
        self.store=Store(Path(self.tmp.name)/'bookmarks.json')
        self.manager=Bookmarks(self.planner.desktop,self.store)

    def query(self,name):
        return copy.deepcopy({'clients':self.clients,'monitors':self.monitors,
                              'activewindow':self.clients[0], 'activeworkspace':{'id':1},
                              'workspaces':[{'id':1,'monitor':'screen'},{'id':2,'monitor':'screen'}]}[name])

    def saved(self):
        p=self.manager.prepare('save bookmark work mode',self.cfg)
        self.manager.execute(p)
        return p.value

    def call(self,engine,text,**kwargs):
        with redirect_stdout(StringIO()): return engine.process(text,**kwargs)

    def test_capture_known_apps_and_private_roundtrip(self):
        value=self.saved(); loaded=self.store.read()['work mode']
        self.assertEqual(value,loaded); self.assertEqual([w['app'] for w in loaded['windows']],['teams.desktop','files.desktop'])
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.manager.prepare('work mode').targets,[WindowTarget('0x1',11),WindowTarget('0x2',22)])

    def test_save_variations(self):
        for text in ('save bookmark work mode','save this as a bookmark called work mode','bookmark this as work mode','please save a bookmark named work mode'):
            self.assertEqual(self.manager.prepare(text,self.cfg).name,'work mode')

    def test_explicit_restore_and_extra_phrases(self):
        self.saved(); self.store.aliases('work mode',['office time'],self.cfg)
        for text in ('work mode','office time','restore bookmark work mode','load bookmark office time','open bookmark work mode'):
            self.assertEqual(self.manager.prepare(text).action,'restore')

    def test_unknown_bookmark_never_falls_through_to_model(self):
        engine=Engine(self.router,self.planner,True,feedback=Mock(),bookmarks=self.manager)
        self.router.jev=Mock()
        result=self.call(engine,'computer restore bookmark missing')
        self.assertEqual(result['verdict'],'error'); self.router.jev.ask.assert_not_called()
        self.hypr.dispatch.assert_not_called()

    def test_replace_and_delete_require_confirmation_and_are_one_shot(self):
        self.saved(); engine=Engine(self.router,self.planner,True,feedback=Mock(),bookmarks=self.manager)
        old=self.store.path.read_bytes()
        self.clients[0]['workspace']['id']=4
        self.assertEqual(self.call(engine,'computer save bookmark work mode')['verdict'],'confirm')
        self.assertEqual(self.store.path.read_bytes(),old)
        self.call(engine,'computer cancel'); self.call(engine,'computer yes')
        self.assertEqual(self.store.path.read_bytes(),old)
        self.call(engine,'computer save bookmark work mode'); self.call(engine,'computer yes')
        self.assertEqual(self.store.read()['work mode']['windows'][0]['workspace'],4)
        self.assertEqual(self.call(engine,'computer delete bookmark work mode')['verdict'],'confirm')
        self.assertIn('work mode',self.store.read()); self.call(engine,'computer yes'); self.assertFalse(self.store.read())

    def test_dryrun_stale_cancel_and_unaddressed_never_write(self):
        engine=Engine(self.router,self.planner,False,feedback=Mock(),bookmarks=self.manager,clock=lambda:10)
        self.call(engine,'computer save bookmark work mode'); self.assertFalse(self.store.path.exists())
        engine.live=True
        self.call(engine,'computer save bookmark work mode',speech_end=0)
        self.call(engine,'computer save bookmark work mode',valid=lambda:False)
        self.call(engine,'save bookmark work mode')
        self.assertFalse(self.store.path.exists())

    def test_revision_prevents_overwrite_from_an_old_confirmation(self):
        self.saved(); plan=self.manager.prepare('save bookmark work mode')
        self.store.aliases('work mode',['office time'],self.cfg)
        with self.assertRaisesRegex(ValueError,'changed'): self.manager.execute(plan)
        self.assertEqual(self.store.read()['work mode']['phrases'],['office time'])

    def test_exclusions_and_settings_focus(self):
        self.clients.extend([dict(window('0x3',33),pinned=True),dict(window('0x4',44),grouped=['0x4']),window('0x5',55,app='org.omarchy.JevVoiceSettings'),window('0x6',66,workspace=-99)])
        self.assertEqual(len(self.manager.capture()['windows']),2)

    def test_reused_address_cannot_restore_a_different_app(self):
        value=self.saved(); self.clients[0]=window('0x1',999,app='other')
        self.assertEqual(self.manager.match(value)[0],None)
        self.hypr.dispatch.assert_not_called()

    def test_closed_same_session_window_does_not_claim_an_unrelated_duplicate(self):
        value=self.saved()
        self.clients.pop(0)
        self.clients.append(window('0x9','18000ace','Alpha','teams'))
        # Teams fixture has no advertised new-window action: stop instead of
        # repurposing the unrelated window, even though its title is identical.
        with self.assertRaisesRegex(ValueError,'new-window action'): self.manager.match(value)
        self.hypr.dispatch.assert_not_called()

    def test_real_string_stable_id_roundtrips(self):
        self.clients[0]['stableId']='18000fed'
        value=self.saved()
        self.assertEqual(self.manager.match(value)[0],WindowTarget('0x1','18000fed'))

    def test_new_session_matches_unique_app_when_title_changed(self):
        value=self.saved(); self.hypr.directory='/new/session'
        self.clients[0]['stableId']=99; self.clients[0]['title']='Another title'
        self.assertEqual(self.manager.match(value)[0],WindowTarget('0x1',99))

    def test_ambiguous_same_app_is_rejected_before_mutation(self):
        value=self.saved(); self.hypr.directory='/new/session'; self.clients[0]['title']='Changed'
        self.clients.append(window('0x3',33,'Other'))
        with self.assertRaisesRegex(ValueError,'ambiguous'): self.manager.match(value)
        self.hypr.dispatch.assert_not_called()

    def test_duplicate_app_exact_titles_are_claimed_first(self):
        self.clients.append(window('0x3',33,'Other'))
        value=self.saved(); self.hypr.directory='/new/session'; self.clients[0]['title']='Changed'
        self.assertEqual(self.manager.match(value),[WindowTarget('0x1',11),WindowTarget('0x2',22),WindowTarget('0x3',33)])

    def test_unknown_missing_app_fails_before_mutation(self):
        self.clients[0]['class']='unlisted'; self.clients[0]['initialClass']='unlisted'; value=self.saved(); self.clients.pop(0)
        with self.assertRaisesRegex(ValueError,'Open unlisted first'): self.manager.match(value)
        self.hypr.dispatch.assert_not_called()

    def test_locked_desktop_blocks_save_and_restore(self):
        self.saved(); self.hypr.request.return_value='true'
        for text in ('save bookmark quiet time','work mode'):
            with self.assertRaisesRegex(RuntimeError,'Unlock'): self.manager.prepare(text)
        self.hypr.dispatch.assert_not_called()

    def test_bound_window_disappearing_stops_restore(self):
        self.saved(); plan=self.manager.prepare('work mode'); self.clients[0]['stableId']=500
        self.assertFalse(self.manager.execute(plan)['ok']); self.hypr.dispatch.assert_not_called()

    def test_cancel_before_restore_is_checked(self):
        self.saved(); plan=self.manager.prepare('work mode')
        with self.assertRaisesRegex(RuntimeError,'cancelled'): self.manager.execute(plan,lambda:False)
        self.hypr.dispatch.assert_not_called()

    def test_no_op_restore_keeps_states_and_other_windows(self):
        self.saved(); self.clients.append(window('0x3',33,'Extra','outlook',3))
        plan=self.manager.prepare('work mode'); self.assertTrue(self.manager.execute(plan)['ok'])
        self.assertFalse(any('close' in c.args[0] for c in self.hypr.dispatch.call_args_list))

    def test_bad_numeric_fields_rejected_before_any_dispatch(self):
        value=self.saved(); value['windows'][0]['fullscreen']='2; bad()'
        with self.assertRaisesRegex(ValueError,'fullscreen'): validate_entries({'work mode':value})
        self.hypr.dispatch.assert_not_called()

    def test_reserved_names_and_phrase_collision(self):
        for name in ('yes','computer','open teams','../work','cancel','dictate'):
            with self.assertRaises(ValueError): name_key(name)
        self.saved()
        with self.assertRaisesRegex(ValueError,'custom command'):
            self.store.aliases('work mode',['quiet time'],{'phrases':{'quiet time':'open files'}})

    def test_alias_enters_real_engine_and_dangerous_action_still_confirms(self):
        self.router.config['phrases']={'end the day':'close all windows'}
        engine=Engine(self.router,self.planner,True,feedback=Mock(),bookmarks=self.manager)
        result=self.call(engine,'computer end the day')
        self.assertEqual(result['verdict'],'confirm'); self.hypr.dispatch.assert_not_called()


class PersonalizationTests(unittest.TestCase):
    def test_exact_match_only(self):
        cfg={'phrases':{'My stuff':'open files'}}
        self.assertEqual(expand_phrase(' MY stuff. ',cfg),'open files')
        self.assertEqual(expand_phrase('do not my stuff',cfg),'do not my stuff')
        self.assertEqual(expand_phrase('my stuff and close teams',cfg),'my stuff and close teams')

    def test_controls_cycles_and_duplicate_aliases_rejected(self):
        for phrases in ({'yes':'open files'},{'any':'yes'},{'a':'b','b':'a'},{'a':'b','A':'c'}, {'x':'bookmark x','restore bookmark x':'open files'}):
            with self.assertRaises(ValueError): validate_phrases(phrases)

    def test_config_roundtrip_preserves_nested_mappings_and_permissions(self):
        with tempfile.TemporaryDirectory() as root, patch.dict('os.environ',{'JEV_VOICE_CONFIG':root+'/config.toml'}):
            cfg=copy.deepcopy(DEFAULTS); cfg['apps']={'my files':'files.desktop'}
            cfg['window_matches']={'terminal.desktop':[{'class':'exact','title':'Quotes " and new\nline'}]}
            save_config(cfg,expected=[None]); self.assertEqual(load_config(),cfg)
            self.assertEqual(Path(root+'/config.toml').stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(ValueError,'changed'): save_config(cfg,expected=[None])
