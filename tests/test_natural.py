"""Natural phrases, complete-plan binding, panel selection and speech pauses."""
import copy
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch
import unittest
import numpy as np

from test_pipeline import Fixture
from daemon import Engine, Segmenter
from desktop_core.commands import Command
from desktop_core.desktop import WindowTarget
from plan import ResultRef, Question
from panels import parse_panel, resolve_panel, execute_panel


class Natural(Fixture):
 def setUp(self):
  super().setUp()
  self.catalog.aliases['browser'] = ('outlook.desktop',)
  from language import add_role_aliases
  add_role_aliases(self.catalog)

 def test_shared_verbs_articles_roles(self):
  for text,action,app in [
    ('bring up the browser','open','browser'), ('pull up my file explorer','open','file explorer'),
    ('could you launch the web browser please','open','web browser'),
    ('shut the files window','close','files'), ('quit the teams app','close','teams'),
    ('make this app full screen','fullscreen',None), ('go fullscreen','fullscreen',None)]:
   with self.subTest(text=text):
    p=self.router.decide(text)
    self.assertEqual(p.verdict,'act'); self.assertEqual(p.route,'local')
    self.assertEqual((p.commands[0].action,p.commands[0].app),(action,app))

 def test_three_step_chain(self):
  p=self.router.decide('move this app to workspace five, make it full screen and close the browser')
  self.assertEqual(p.verdict,'act')
  self.assertEqual([c.action for c in p.commands],['move','fullscreen','close'])
  self.assertEqual(p.commands[1].reference,'previous')

 def test_app_and_destination_imply_open_without_an_api_request(self):
  self.router.jev=Mock(key=None)
  for phrase,app,workspace in [('files workspace 2','files',2),
                              ('browser on three','browser',3),
                              ('Teams desktop five','teams',5),
                              ('the file explorer on workspace two please','file explorer',2),
                              ('teams','teams',None)]:
   with self.subTest(phrase=phrase):
    p=self.router.decide(phrase)
    self.assertEqual(p.verdict,'act'); self.assertEqual(p.route,'local')
    self.assertEqual((p.commands[0].action,p.commands[0].app,p.commands[0].workspace),('open',app,workspace))
  self.router.jev.ask.assert_not_called()

 def test_implicit_open_can_start_a_chain_and_share_a_destination(self):
  p=self.router.local('files workspace two then make it full screen')
  self.assertEqual([c.action for c in p.commands],['open','fullscreen'])
  self.assertEqual(p.commands[0].workspace,2)
  self.assertEqual(p.commands[1].reference,'previous')
  p=self.router.local('files and teams on workspace two')
  self.assertEqual([(c.action,c.workspace) for c in p.commands],[('open',2),('open',2)])

 def test_shorthand_still_consumes_the_entire_request(self):
  for phrase in ['files workspace 2 if it is open','files workspace 200',
                 'files workspace 2 then delete my documents','not files workspace 2',
                 'uninstalledapp workspace 2','files or teams workspace 2']:
   with self.subTest(phrase=phrase), self.assertRaises(ValueError): self.router.local(phrase)

 def test_conditions_do_not_become_unconditional_launches(self):
  self.router.jev=Mock()
  for phrase in ['files workspace two if it is open','files workspace two unless it is closed',
                 'files workspace two only when it is already running']:
   with self.subTest(phrase=phrase): self.assertEqual(self.router.decide(phrase).verdict,'drop')
  self.router.jev.ask.assert_not_called()

 def test_there_and_coordinated_objects(self):
  p=self.router.decide('open files and browser on workspace two then move teams there')
  self.assertEqual([c.workspace for c in p.commands],[2,2,2])

 def test_panel_chain_and_dnd_are_distinct(self):
  for phrase,action in [('show me notifications','panel.notifications.show'),
    ('open the notification center','panel.notifications.show'), ('dismiss notifications','panel.notifications.hide'),
    ('show AI usage','panel.usage.show'), ('open note','panel.notes.show'),
    ('bring up quick settings','panel.controls.show'), ('show my file shelf','panel.shelf.show')]:
   with self.subTest(phrase=phrase): self.assertEqual(self.router.decide(phrase).commands[0].action,action)
  p=self.router.decide('hide notifications then open notes')
  self.assertEqual([c.action for c in p.commands],['panel.notifications.hide','panel.notes.show'])
  self.assertIsNone(parse_panel('stop notifications for a bit'))

 def test_incomplete_does_not_guess_open(self):
  for phrase,slot in [('make files','layout'), ('move','workspace'),
    ('move this app','workspace'), ('move files to workspace','workspace'),
    ('open files works best','workspace')]:
   with self.subTest(phrase=phrase):
    p=self.router.decide(phrase); self.assertEqual((p.verdict,p.slot),('clarify',slot))

 def test_ambiguous_or_incomplete_chain_never_partly_acts(self):
  for text in ['open files then make it','move this app to five and close another app',
    'open files then close the browser if it is idle','open teams and delete my documents',
    'bring up the browser without closing files','shut the files window except the important one']:
   with self.subTest(text=text): self.assertEqual(self.router.decide(text).verdict,'drop')

 def test_semantic_pronoun_survives_model_route(self):
  answers={'addressed':{'noul':.99}, 'complete0':{'noul':.99}}
  for key,value in {'action0':'fullscreen','target0':'current','workspace0':'none','focus0':'follow'}.items():
   answers[key]={'choice':value,'probabilities':{value:.99}}
  self.router.jev=Mock(); self.router.jev.ask.return_value=(1,{'answers':answers})
  p=self.router.decide('could it fill the screen',force_jev=True)
  self.assertEqual(p.commands[0].reference,'previous')


class References(Fixture):
 def setUp(self):
  super().setUp()
  self.clients=[{'address':'0x1','stableId':10,'class':'teams','initialClass':'teams','workspace':{'id':1},'title':'Teams'},
                {'address':'0x2','stableId':20,'class':'files','initialClass':'files','workspace':{'id':2},'title':'Files'}]
  self.hypr=Mock(); self.hypr.query.side_effect=lambda q:copy.deepcopy(self.clients if q=='clients' else self.clients[1])
  from plan import Planner
  self.planner=Planner(self.catalog,self.cfg,self.hypr)
 def test_shorthand_opens_a_missing_app_on_the_requested_workspace(self):
  self.clients.clear()
  self.hypr.query.side_effect=lambda q: [] if q=='clients' else {}
  plan=self.planner.prepare(self.router.local('files workspace 2').commands)
  self.assertEqual(plan.steps[0].command.action,'open')
  self.assertEqual(plan.steps[0].command.workspace,2)
  self.assertIsNone(plan.steps[0].target)
 def test_shorthand_reuses_a_window_elsewhere_or_already_at_destination(self):
  for workspace in (1,2):
   self.clients[1]['workspace']['id']=workspace
   plan=self.planner.prepare(self.router.local('files workspace 2').commands)
   self.assertEqual(plan.steps[0].command.action,'focus')
   self.assertEqual(plan.steps[0].command.workspace,2)
   self.assertEqual(plan.steps[0].target,WindowTarget('0x2',20))
 def test_missing_named_app_placement_launches_and_binds_later_steps(self):
  from desktop_core.commands import Command
  plan=self.planner.prepare([Command('move',app='outlook',workspace=5,focus=False),
                             Command('fullscreen',reference='previous')])
  self.assertEqual(plan.steps[0].command.action,'open')
  self.assertEqual(plan.steps[0].command.workspace,5)
  self.assertFalse(plan.steps[0].command.focus)
  self.assertIsNone(plan.steps[0].target)
  self.assertEqual(plan.steps[-1].target,ResultRef(0))
 def test_missing_current_window_never_launches_a_guessed_app(self):
  from desktop_core.commands import Command
  self.hypr.query.side_effect=lambda q: [] if q=='clients' else {}
  with self.assertRaisesRegex(ValueError,'no current window'):
   self.planner.prepare([Command('move',workspace=2)])
 def test_it_binds_previous_and_this_stays_original(self):
  p=self.planner.prepare(self.router.local('focus teams then make it full screen then move this app to three').commands)
  self.assertEqual(p.steps[-2].target,WindowTarget('0x1',10))
  self.assertEqual(p.steps[-1].target,WindowTarget('0x2',20))

 def test_new_app_result_is_used_not_whichever_is_focused(self):
  p=self.planner.prepare(self.router.local('open outlook then move it to workspace five then make it full screen').commands)
  self.assertIsInstance(p.steps[1].target,ResultRef)
  results=[{'ok':True,'window':'0x9','stable_id':99,'message':'Opened'},
           {'ok':True,'window':'0x9','stable_id':99,'message':'Moved'},
           {'ok':True,'window':'0x9','stable_id':99,'message':'Focused'},
           {'ok':True,'window':'0x9','stable_id':99,'message':'Fullscreen'}]
  self.planner.desktop.execute=Mock(side_effect=results)
  self.assertTrue(self.planner.execute(p)['ok'])
  for call in self.planner.desktop.execute.call_args_list[1:]:
   self.assertEqual(call.args[1],WindowTarget('0x9',99))

 def test_later_named_reference_uses_planned_new_window(self):
  with patch('desktop_core.desktop.launch_identifier',return_value='teams.desktop:new'):
   p=self.planner.prepare([Command('open',app='teams',new=True),Command('move',app='teams',workspace=5)])
  self.assertEqual(p.steps[1].target,ResultRef(0))

 def test_closed_reference_rejected_before_execution(self):
  for phrase in ['close teams then make it full screen','close teams then move teams to five']:
   with self.assertRaises(ValueError): self.planner.prepare(self.router.local(phrase).commands)
  self.hypr.dispatch.assert_not_called()

 def test_failed_launch_never_runs_later_steps(self):
  p=self.planner.prepare(self.router.local('open outlook then close files').commands)
  self.planner.desktop.execute=Mock(side_effect=RuntimeError('No window'))
  self.assertFalse(self.planner.execute(p)['ok']); self.planner.desktop.execute.assert_called_once()

 def test_missing_workspace_question_is_bound_and_one_shot(self):
  now=[0.]; feedback=Mock()
  e=Engine(self.router,self.planner,True,feedback,clock=lambda:now[0])
  self.planner.execute=Mock(return_value={'ok':True,'message':'Moved'})
  with redirect_stdout(StringIO()):
   self.assertEqual(e.process('computer move files')['verdict'],'clarify')
   self.planner.execute.assert_not_called()
   # The user changes focus while answering. The old target must remain.
   self.clients.reverse()
   e.process('computer workspace five')
   e.process('computer five')
  self.planner.execute.assert_called_once()
  step=self.planner.execute.call_args.args[0].steps[0]
  self.assertEqual(step.target,WindowTarget('0x2',20)); self.assertEqual(step.command.workspace,5)

 def test_layout_question_expiry_cancel_and_fresh_request(self):
  now=[0.]; e=Engine(self.router,self.planner,True,Mock(),clock=lambda:now[0])
  self.planner.execute=Mock(return_value={'ok':True,'message':'Done'})
  with redirect_stdout(StringIO()):
   e.process('computer make files'); self.planner.execute.assert_not_called()
   e.process('computer floating')
   self.assertEqual(self.planner.execute.call_args.args[0].steps[-1].command.action,'float')
   self.planner.execute.reset_mock()
   e.process('computer move'); e.process('computer cancel'); e.process('computer five')
   self.planner.execute.assert_not_called()
   e.process('computer move'); now[0]=9; e.process('computer five')
   self.planner.execute.assert_not_called()
   e.process('computer move'); e.process('computer close teams')
   self.planner.execute.assert_called_once(); self.assertIsNone(e.question)


class Panels(unittest.TestCase):
 def test_enabled_clone_is_selected_not_disabled_stock(self):
  plugins=[{'id':'omarchy.agents','enabled':False,'kinds':['bar-widget']},
           {'id':'example.agents','clonedFrom':'omarchy.agents','enabled':True,'kinds':['bar-widget']}]
  self.assertEqual(resolve_panel('panel.usage.show',plugins),'example.agents')
 def test_notification_daemon_is_not_history_panel(self):
  with self.assertRaises(ValueError): resolve_panel('panel.notifications.show',[
   {'id':'omarchy.notifications','enabled':True,'kinds':['service']}])
 def test_unavailable_panel_is_not_reported_successful(self):
  with patch('panels.run',return_value='unknown'):
   with self.assertRaises(RuntimeError): execute_panel('panel.notes.show','io.github.agata.omanano')


class Pauses(unittest.TestCase):
 def test_four_hundred_ms_pause_keeps_one_utterance(self):
  gate=Segmenter(); loud=np.full(320,1000,dtype=np.int16); quiet=np.zeros(320,dtype=np.int16)
  frames=[loud]*15 + [quiet]*20 + [loud]*15 + [quiet]*28
  ended=[]; starts=0
  for i,frame in enumerate(frames):
   began,segment=gate.feed(frame,i*.02); starts+=began
   if segment: ended.append(segment)
  self.assertEqual(starts,1); self.assertEqual(len(ended),1)
  self.assertEqual(len(ended[0][0]),len(frames)*320)
 def test_separate_commands_stay_separate(self):
  gate=Segmenter(); loud=np.full(320,1000,dtype=np.int16); quiet=np.zeros(320,dtype=np.int16)
  ended=[]
  for i,frame in enumerate(([loud]*15+[quiet]*40)*2):
   _,segment=gate.feed(frame,i*.02)
   if segment: ended.append(segment)
  self.assertEqual(len(ended),2)

if __name__=='__main__': unittest.main()
