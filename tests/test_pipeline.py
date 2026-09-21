import copy
from dataclasses import replace
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
from daemon import Engine, Segmenter
from desktop_core.apps import AppCatalog
from desktop_core.commands import Command
from desktop_core.desktop import WindowTarget
from plan import Confirmation, Planner, Plan, Step
from router import Router, Proposal
from vt import Jev, strip_wake


class Fixture(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
  root=Path(self.tmp.name)
  for name in ('Teams','Files','Outlook'):
   (root/(name.lower()+'.desktop')).write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec={name.lower()}\nStartupWMClass={name.lower()}\n')
  self.catalog=AppCatalog([root],default_browser='',default_file_manager='files.desktop')
  self.cfg={'desktop':{'focus_by_default':True,'launch_timeout_seconds':1,'close_timeout_seconds':.01}}
  self.router=Router(self.catalog,self.cfg)

class Routing(Fixture):
 def test_real_transcripts(self):
  cases={'open teams on workspace 5':('open','teams',5), 'move, teams to workspace 2':('move','teams',2),
         'make teams full screen':('fullscreen','teams',None), 'exit full screen':('restore',None,None),
         'closed downloads':('close','file manager',None), 'close files':('close','files',None)}
  for text,expect in cases.items():
   with self.subTest(text=text):
    p=self.router.decide(text); c=p.commands[0]
    self.assertEqual((c.action,c.app,c.workspace),expect)
    self.assertEqual(p.route,'local')
 def test_whole_plan_or_nothing(self):
  self.assertEqual([c.workspace for c in self.router.decide('open teams and outlook on workspace five').commands],[5,5])
  for text in ('open teams then delete documents','do not close teams','open teams but not now','open teams on workspace 100','open unknownapp'):
   with self.subTest(text=text): self.assertEqual(self.router.decide(text).verdict,'drop')
 def test_local_command_remains_available_without_an_api_key(self):
  self.router.jev=Mock(key=None)
  self.assertEqual(self.router.decide('open teams on workspace 5').route,'local')
  self.router.jev.ask.assert_not_called()
 def test_semantic_request_skips_irrelevant_volume_work(self):
  self.assertNotIn('volume0',self.router.questions(['bring me teams']))
  options=self.router.questions(['set sound to seventy five percent'])['volume0']['criteria']
  self.assertIn('75',options); self.assertIn('none',options); self.assertNotIn('96',options)
 def test_best_valid_interpretation_acts_without_confidence_confirmation(self):
  self.router.jev=Mock()
  qs=self.router.questions(['bring up teams'])
  values={'action0':'open','target0':'teams','workspace0':'none','focus0':'follow','new0':'existing','folder0':'none','volume0':'none'}
  answers={k:{'choice':v,'probabilities':{v:.99}} for k,v in values.items()}
  answers['target0']['probabilities']['teams']=.55
  answers.update(complete0={'noul':.99},addressed={'noul':.99})
  self.router.jev.ask.return_value=(300,{'answers':answers})
  p=self.router.decide('I need teams'); self.assertEqual(p.verdict,'act'); self.assertEqual(p.confidence,.55)
  answers['target0']['probabilities']['teams']=.25
  p=self.router.decide('I need teams'); self.assertEqual(p.verdict,'act')
 def test_recent_spoken_variations_are_local_and_immediate(self):
  self.router.jev=Mock(key=None)
  for text,action in [('and make teams fullscreen','fullscreen'),('make teams not full screen','restore'),
                      ('clothes files','close'),('course files','close'),('get teams out of fullscreen','restore')]:
   with self.subTest(text=text):
    p=self.router.decide(text); self.assertEqual(p.route,'local'); self.assertEqual(p.verdict,'act')
    self.assertEqual(p.commands[0].action,action)
  self.router.jev.ask.assert_not_called()
 def test_no_partial_execution_for_incomplete_request(self):
  self.router.interpretation['reject_low_confidence']=True
  self.router.jev=Mock(); self.router.jev.ask.return_value=(1,{'answers':{'addressed':{'noul':.99},'action0':{'choice':'open','probabilities':{'open':.99}},'complete0':{'noul':.1}}})
  p=self.router.decide('open teams and do something impossible'); self.assertEqual(p.verdict,'drop')
 def test_one_request_for_multiple_semantic_clauses(self):
  self.router.interpretation['reject_low_confidence']=True
  self.router.jev=Mock()
  self.router.jev.ask.return_value=(1,{'answers':{'addressed':{'noul':.1}}})
  self.router.decide('give me teams then bring me outlook')
  self.router.jev.ask.assert_called_once()
  self.assertEqual(self.router.jev.ask.call_args.args[1]['clauses'],['give me teams','bring me outlook'])
 def test_close_all_requires_confirmation(self):
  self.assertEqual(self.router.decide('close all windows').verdict,'confirm')

class ConfirmTests(unittest.TestCase):
 def test_one_shot_expiry_cancel(self):
  now=[0.]; c=Confirmation(clock=lambda:now[0]); c.set('a'); self.assertEqual(c.take(),'a'); self.assertIsNone(c.take())
  c.set('b'); now[0]=8.; self.assertIsNone(c.take())
  c.set('c'); self.assertEqual(c.cancel(),'c'); self.assertIsNone(c.take())
 def engine(self):
  self.now=[0.]; self.router=Mock(); self.planner=Mock(); self.feedback=Mock()
  self.router.decide.return_value=Proposal(commands=[Command('close')],verdict='confirm')
  self.plan=Plan([Step(Command('close'),WindowTarget('0x123',1),'Close captured window')])
  self.planner.prepare.return_value=self.plan
  self.planner.execute.return_value={'ok':True,'message':'Closed'}
  return Engine(self.router,self.planner,True,self.feedback,clock=lambda:self.now[0])
 def call(self, engine, text):
  with redirect_stdout(StringIO()): return engine.process(text)
 def test_confirm_executes_exact_plan_once(self):
  e=self.engine(); self.call(e,'computer close this window'); self.planner.execute.assert_not_called()
  self.call(e,'yes'); self.planner.execute.assert_not_called()
  self.call(e,'computer yes'); self.planner.execute.assert_called_once_with(self.plan)
  self.call(e,'computer yes'); self.assertEqual(self.planner.execute.call_count,1)
 def test_fresh_bad_request_clears_pending(self):
  e=self.engine(); self.call(e,'computer close this'); self.router.decide.side_effect=RuntimeError('network failed')
  self.call(e,'computer something else'); self.call(e,'computer yes'); self.planner.execute.assert_not_called()
 def test_expiration_notifies(self):
  e=self.engine(); self.call(e,'computer close this'); self.now[0]=9
  self.call(e,'computer yes'); self.planner.execute.assert_not_called(); self.assertIsNone(e.confirmation.pending)
 def test_cancel_and_dry_run_do_not_execute(self):
  e=self.engine(); self.call(e,'computer close this'); self.call(e,'computer cancel'); self.call(e,'computer yes')
  self.planner.execute.assert_not_called()
  e.live=False; self.call(e,'computer close this'); r=self.call(e,'computer yes')
  self.assertTrue(r['result']['dry_run']); self.planner.execute.assert_not_called()
 def test_unaddressed_never_reaches_router(self):
  e=self.engine()
  for text in ['come here','commuter close teams','I told the computer close this','']:
   self.assertIsNone(self.call(e,text))
  self.router.decide.assert_not_called()
 def test_worker_can_process_after_failure(self):
  e=self.engine(); self.router.decide.side_effect=[RuntimeError('offline'),Proposal(commands=[Command('close')],verdict='confirm')]
  self.assertEqual(self.call(e,'computer some command')['verdict'],'error')
  self.assertEqual(self.call(e,'computer close this')['verdict'],'confirm')
 def test_stale_request_never_executes(self):
  e=self.engine(); self.router.decide.return_value=Proposal(commands=[Command('close')]); self.now[0]=6
  with redirect_stdout(StringIO()): r=e.process('computer close this',speech_end=0)
  self.assertEqual(r['verdict'],'drop'); self.planner.execute.assert_not_called()

class Binding(Fixture):
 def setUp(self):
  super().setUp()
  self.clients=[{'address':'0x1','stableId':10,'class':'teams','initialClass':'teams','workspace':{'id':1},'title':'Teams'},
                {'address':'0x2','stableId':20,'class':'files','initialClass':'files','workspace':{'id':2},'title':'Files'}]
  self.hypr=Mock(); self.hypr.query.side_effect=lambda q:copy.deepcopy(self.clients if q=='clients' else self.clients[1])
  self.planner=Planner(self.catalog,self.cfg,self.hypr)
 def test_named_target_is_bound_before_confirm(self):
  plan=self.planner.prepare([Command('close',app='teams')]); step=plan.steps[0]
  self.assertIsNone(step.command.app); self.assertEqual(step.target,WindowTarget('0x1',10))
 def test_missing_named_target_never_closes_active(self):
  with self.assertRaisesRegex(ValueError,'no matching'): self.planner.prepare([Command('close',app='outlook')])
 def test_existing_launch_moves_exact_app(self):
  step=self.planner.prepare([Command('open',app='teams',workspace=5)]).steps[0]
  self.assertEqual(step.command.action,'focus'); self.assertEqual(step.command.workspace,5); self.assertEqual(step.target.address,'0x1')
 def test_reused_address_is_rejected(self):
  plan=self.planner.prepare([Command('close',app='teams')]); self.clients[0]['stableId']=999
  result=self.planner.execute(plan); self.assertFalse(result['ok']); self.hypr.dispatch.assert_not_called()
 def test_named_layout_also_focuses_and_places(self):
  p=self.planner.prepare([Command('fullscreen',app='teams',workspace=5)])
  self.assertEqual([s.command.action for s in p.steps],['focus','fullscreen'])
  self.assertEqual(p.steps[0].command.workspace,5)
  self.assertIsNone(p.steps[1].command.workspace)
  self.assertEqual(p.steps[0].target,p.steps[1].target)
 def test_background_layout_does_not_steal_focus(self):
  p=self.planner.prepare([Command('fullscreen',app='teams',focus=False)])
  self.assertEqual([s.command.action for s in p.steps],['fullscreen'])
 def test_plan_preflight_prevents_partial_effect(self):
  with self.assertRaises(ValueError): self.planner.prepare([Command('move',app='teams',workspace=5),Command('close',app='outlook')])
  self.hypr.dispatch.assert_not_called()

class AudioTests(unittest.TestCase):
 def test_wake(self):
  self.assertEqual(strip_wake('Hey computer, open Teams.'),(True,'open Teams.'))
  for s in ('commuter open teams','I computed the totals','come here','computerized'):
   self.assertFalse(strip_wake(s)[0])
 def test_no_duplicate_onset_and_clean_endpoint(self):
  g=Segmenter(320); loud=np.full(320,1000,dtype=np.int16); quiet=np.zeros(320,dtype=np.int16); result=None
  for i in range(5): _,result=g.feed(loud,i*.02)
  for i in range(16): _,result=g.feed(quiet,.1+i*.02)
  self.assertIsNotNone(result); self.assertEqual(len(result[0]),21*320); self.assertAlmostEqual(result[1],.08)
 def test_silence_never_opens(self):
  g=Segmenter()
  for i in range(1000): self.assertEqual(g.feed(np.zeros(320,dtype=np.int16),i*.02),(False,None))
 def test_overlong_does_not_execute_fragment(self):
  g=Segmenter(); loud=np.full(320,1000,dtype=np.int16)
  dropped=[]
  for i in range(1100):
   _,r=g.feed(loud,i*.02)
   if r is not None: dropped.append(r)
  self.assertEqual(len(dropped),1); self.assertIsNone(dropped[0][0])
  self.assertFalse(g.capturing)
  self.assertGreater(g.noise,900)
 def test_calibrated_laptop_noise_does_not_hold_gate_open(self):
  g=Segmenter(); g.calibrate([550.]*25)
  for i in range(200):
   began,segment=g.feed(np.full(320,600,dtype=np.int16),i*.02)
   self.assertFalse(began); self.assertIsNone(segment)
  for i in range(30): g.feed(np.full(320,3000,dtype=np.int16),4+i*.02)
  events=[]
  for i in range(40):
   _,r=g.feed(np.full(320,600,dtype=np.int16),4.6+i*.02)
   if r: events.append(r)
  self.assertEqual(len(events),1); self.assertIsNotNone(events[0][0]); self.assertFalse(g.capturing)
 def test_overlong_recovers_without_silence_then_hears_next_utterance(self):
  g=Segmenter(); results=[]
  for i in range(1150):
   _,r=g.feed(np.full(320,600,dtype=np.int16),i*.02)
   if r: results.append(r)
  self.assertEqual(len(results),1); self.assertIsNone(results[0][0])
  for i in range(20): g.feed(np.full(320,3000,dtype=np.int16),23+i*.02)
  for i in range(40):
   _,r=g.feed(np.full(320,600,dtype=np.int16),23.4+i*.02)
   if r: results.append(r)
  self.assertEqual(len(results),2); self.assertIsNotNone(results[1][0])

class Transport(unittest.TestCase):
 def test_http_errors_never_look_like_success(self):
  j=Jev('test'); conn=Mock(); response=Mock(status=401); response.read.return_value=b'{"error":"no"}'; conn.getresponse.return_value=response; j.conn=conn
  with self.assertRaisesRegex(RuntimeError,'HTTP 401'): j.ask('open teams',{}, {'a':{}})
  self.assertEqual(conn.request.call_count,1)


class SettingTests(unittest.TestCase):
 def test_nightlight_is_idempotent(self):
  from settings import execute_setting
  with patch('settings.run',return_value='{"enabled": true}') as run:
   self.assertTrue(execute_setting('nightlight.on')['ok'])
   self.assertEqual(run.call_count,2)
   self.assertTrue(all(c.args[0][-1]=='--status' for c in run.call_args_list))
 def test_notifications_set_instead_of_toggle(self):
  from settings import execute_setting
  with patch('settings.run',return_value='on') as run:
   execute_setting('notifications.quiet')
   self.assertEqual(run.call_args_list[0].args[0],['omarchy','shell','notifications','setDnd','true'])
 def test_failed_state_change_is_reported(self):
  from settings import execute_setting
  with patch('settings.run',return_value='off'):
   with self.assertRaisesRegex(RuntimeError,'did not take effect'): execute_setting('notifications.quiet')

class PlacementTests(Fixture):
 def test_colloquial_placement_is_move_not_launch(self):
  for phrase in ['put teams back on workspace one','could you put teams over on desktop four','chuck this on desktop five']:
   with self.subTest(phrase=phrase): self.assertEqual(self.router.local(phrase).commands[0].action,'move')
 def test_placement_does_not_hide_conditions(self):
  for phrase in ['put teams on workspace one if it is open','could you put teams on desktop four later']:
   with self.assertRaises(ValueError): self.router.local(phrase)
 def test_only_mentioned_names_leave_machine(self):
  self.router.interpretation['reject_low_confidence']=True
  self.router.jev=Mock(); self.router.jev.ask.return_value=(1,{'answers':{'addressed':{'noul':.1}}})
  self.router.decide('I need teams')
  call=self.router.jev.ask.call_args
  body=json.dumps([call.args,call.kwargs])
  self.assertNotIn('outlook',body.lower()); self.assertNotIn('teams.desktop',body)
  self.assertEqual(call.args[1]['named_apps'],[['teams']])

class ConfigurationTests(Fixture):
 def test_folder_handler_follows_configured_files_app(self):
  from daemon import components
  cfg={**self.cfg, 'apps':{'files':'files.desktop'}}
  with patch('daemon.configuration',return_value=cfg), patch('daemon.AppCatalog',return_value=self.catalog) as factory:
   components(hypr=Mock())
   self.assertEqual(factory.call_args.kwargs['alias_overrides']['file manager'],'files.desktop')

if __name__=='__main__': unittest.main()
