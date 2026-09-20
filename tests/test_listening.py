from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import queue
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
from configuration import DEFAULTS, load_config, load_key, save_key
from control import Control, request
from daemon import Engine, Segmenter
from listening import Listener, AudioJob
from router import Proposal
from desktop_core.commands import Command
from plan import Plan, Step


class Capture(unittest.TestCase):
 def setUp(self):
  self.now=[0.]; self.engine=Mock(); self.engine.lock=threading.RLock()
  self.engine.process.return_value={'verdict':'act','result':{'ok':True}}
  self.planner=Mock(); self.planner.desktop.hypr.query.return_value={'address':'0x1','stableId':1}
  self.control=Control('/tmp/not-started-jev-test.sock')
  self.decode=Mock(return_value=(200,'move this to workspace five'))
  self.listener=Listener(self.engine,self.planner,Segmenter(),DEFAULTS,Mock(),threading.Event(),self.control,self.decode,lambda:self.now[0])
  self.loud=np.full(320,1200,dtype=np.int16); self.quiet=np.zeros(320,dtype=np.int16)
 def feed(self, frame, n):
  for _ in range(n):
   self.now[0]+=.02; self.listener.frame(frame,self.now[0])
 def test_press_is_immediate_and_release_requires_no_wake(self):
  self.listener.command('press'); self.assertEqual(self.control.snapshot()['phase'],'listening')
  self.feed(self.loud,20); self.listener.command('release')
  self.assertEqual(self.control.snapshot()['phase'],'processing')
  self.listener.process_job(self.listener.work.get_nowait())
  self.assertEqual(self.engine.process.call_args.kwargs['activation'],'ptt')
  self.assertEqual(self.control.snapshot()['phase'],'idle')
 def test_custom_phrase_can_start_dictation_without_rewriting_body(self):
  self.engine.router.config={'phrases':{'writing time':'dictate'}}
  self.engine.live=False
  self.decode.return_value=(100,'computer writing time')
  self.listener.command('press'); self.feed(self.loud,10); self.listener.command('release')
  self.listener.process_job(self.listener.work.get_nowait())
  self.engine.feedback.assert_called_with('Dry run: start dictation')
  self.engine.process.assert_not_called()
 def test_capture_resets_keep_learned_microphone_noise(self):
  self.listener.gate.noise=560
  for action in ('press','release','cancel','toggle-wake'):
   self.listener.command(action)
   self.assertEqual(self.listener.gate.noise,560)
 def test_idle_audio_health_is_numeric_and_does_not_broadcast(self):
  self.control.changed.clear(); self.feed(self.quiet,15)
  state=self.control.snapshot()
  self.assertIn('audio_rms',state); self.assertIn('speech_threshold',state)
  self.assertFalse(self.control.changed.is_set())
 def test_quiet_tap_never_decodes(self):
  self.listener.command('press'); self.feed(self.quiet,20); self.listener.command('release')
  self.assertTrue(self.listener.work.empty()); self.decode.assert_not_called()
 def test_confirmation_prompt_is_available_in_bar_and_cleared_on_new_recording(self):
  self.engine.process.return_value={'verdict':'confirm','prompt':'Close all windows? Say computer yes.'}
  self.listener.command('press'); self.feed(self.loud,10); self.listener.command('release')
  self.listener.process_job(self.listener.work.get_nowait())
  self.assertEqual(self.control.snapshot()['phase'],'waiting')
  self.assertIn('computer yes',self.control.snapshot()['prompt'])
  self.listener.command('press')
  self.assertEqual(self.control.snapshot()['prompt'],'')
 def test_ptt_holds_across_pauses(self):
  self.listener.command('press'); self.feed(self.loud,10); self.feed(self.quiet,60); self.feed(self.loud,10)
  self.assertTrue(self.listener.work.empty())
  self.listener.command('release'); self.assertEqual(self.listener.work.qsize(),1)
 def test_cancel_invalidates_already_queued_audio(self):
  self.listener.command('press'); self.feed(self.loud,10); self.listener.command('release')
  job=self.listener.work.get_nowait(); self.listener.command('cancel'); self.listener.process_job(job)
  self.decode.assert_not_called(); self.engine.process.assert_not_called()
 def test_cancel_during_decode_does_not_execute(self):
  self.listener.command('press'); self.feed(self.loud,10); self.listener.command('release')
  def cancel(_,**kwargs): self.listener.command('cancel'); return 200,'close files'
  self.listener.decode=cancel; self.listener.process_job(self.listener.work.get_nowait())
  self.engine.process.assert_not_called()
 def test_overlong_ptt_is_discarded(self):
  self.listener.command('press'); self.feed(self.loud,1001); self.listener.command('release')
  self.assertFalse(self.listener.ptt); self.assertTrue(self.listener.work.empty())
 def test_wake_preview_only_updates_ui_never_executes(self):
  self.decode.return_value=(200,'Computer move')
  self.feed(self.loud,42); job=self.listener.previews.get_nowait()
  self.listener.process_job(job)
  self.assertEqual(self.control.snapshot()['phase'],'listening'); self.engine.process.assert_not_called()
 def test_unaddressed_preview_and_full_audio_stay_private(self):
  self.decode.return_value=(200,'the meeting is tomorrow')
  self.feed(self.loud,42); self.listener.process_job(self.listener.previews.get_nowait())
  self.feed(self.quiet,28); self.listener.process_job(self.listener.work.get_nowait())
  self.engine.process.assert_not_called(); self.assertEqual(self.control.snapshot()['phase'],'idle')
 def test_wake_only_arms_one_followup_then_expires(self):
  self.decode.return_value=(200,'computer')
  self.feed(self.loud,15); self.feed(self.quiet,28); self.listener.process_job(self.listener.work.get_nowait())
  self.assertGreater(self.listener.armed_until,self.now[0])
  self.decode.return_value=(200,'open files')
  self.feed(self.loud,15); self.feed(self.quiet,28)
  job=self.listener.work.get_nowait(); self.assertEqual(job.activation,'wake-followup')
  self.listener.process_job(job); self.engine.process.assert_called_once()
  self.listener.armed_until=self.now[0]+5; self.listener.show('listening',seconds=5)
  self.now[0]+=6; self.feed(self.quiet,1)
  self.assertEqual(self.listener.armed_until,0); self.assertEqual(self.control.snapshot()['phase'],'idle')
 def test_wake_disabled_still_accepts_ptt(self):
  self.listener.command('toggle-wake'); self.feed(self.loud,60); self.feed(self.quiet,30)
  self.assertTrue(self.listener.work.empty()); self.assertTrue(self.listener.previews.empty())
  self.listener.command('press'); self.feed(self.loud,10); self.listener.command('release')
  self.assertEqual(self.listener.work.qsize(),1)
 def test_old_job_cannot_hide_new_recording(self):
  self.listener.token=4; self.listener.show('listening')
  self.listener.show('idle',token=3); self.assertEqual(self.control.snapshot()['phase'],'listening')
 def test_repeated_key_press_is_not_a_new_recording(self):
  self.listener.command('press'); self.feed(self.loud,10)
  epoch=self.listener.epoch; self.listener.command('press')
  self.assertEqual(self.listener.epoch,epoch); self.assertEqual(len(self.listener.frames),10)
 def test_dictation_waveform_uses_audio_peaks_and_settles_during_silence(self):
  self.listener.dictation=Mock(active=True,phase='recording')
  self.listener.show('dictating')
  audio=np.tile(np.array([-32768,32767],dtype=np.int16),160)
  self.feed(audio,10)
  status=self.control.snapshot()
  self.assertEqual(min(status['waveform']),-100); self.assertEqual(max(status['waveform']),100)
  self.assertTrue(status['dictation'])
  self.feed(self.quiet,100)
  status=self.control.snapshot()
  self.assertLessEqual(len(status['waveform']),160)
  self.assertEqual(set(status['waveform']),{0})
  self.engine.process.assert_not_called()
 def test_waveform_is_limited_to_dictation_and_cleared_when_done(self):
  self.listener.command('press'); self.feed(self.loud,10)
  self.assertEqual(self.control.snapshot()['waveform'],[])
  self.listener.command('cancel')
  self.listener.dictation=Mock(active=True,phase='recording')
  self.listener.show('dictating'); self.feed(self.loud,10)
  samples=self.control.snapshot()['waveform']; self.assertTrue(samples)
  self.listener.dictation.phase='processing'; self.listener.show('processing')
  self.assertEqual(self.control.snapshot()['waveform'],samples)
  self.assertTrue(self.control.snapshot()['dictation'])
  self.listener.dictation.active=False; self.listener.show('idle')
  self.assertEqual(self.control.snapshot()['waveform'],[])
  self.assertFalse(self.control.snapshot()['dictation'])


class Boundaries(unittest.TestCase):
 def test_hold_to_talk_can_answer_existing_confirmation(self):
  router=Mock(); planner=Mock(); stop=threading.Event()
  planner.desktop.hypr.query.return_value={'address':'0x1','stableId':1}
  plan=Plan([Step(Command('close'),label='Close captured window')])
  planner.execute.return_value={'ok':True,'message':'Closed'}
  e=Engine(router,planner,True,Mock(),log=lambda result:stop.set())
  e.confirmation.set(plan)
  listener=Listener(e,planner,Segmenter(),DEFAULTS,Mock(),stop,Control('/tmp/not-started-jev-confirm.sock'),Mock(return_value=(1,'yes')))
  listener.command('press')
  import time
  for _ in range(5): listener.frame(np.full(320,1000,dtype=np.int16),time.monotonic())
  listener.command('release')
  with redirect_stdout(StringIO()): listener.worker()
  planner.execute.assert_called_once_with(plan); router.decide.assert_not_called()
 def test_ptt_is_explicit_no_wake_bypass_for_other_modes(self):
  router=Mock(); planner=Mock(); feedback=Mock()
  router.decide.return_value=Proposal(commands=[Command('close')])
  planner.prepare.return_value=Plan([Step(Command('close'),label='Close')])
  planner.execute.return_value={'ok':True,'message':'Closed'}
  e=Engine(router,planner,True,feedback)
  with redirect_stdout(StringIO()):
   self.assertIsNone(e.process('close files')); planner.execute.assert_not_called()
   e.process('close files',activation='ptt')
  planner.execute.assert_called_once()
 def test_cancel_during_preflight_does_not_dispatch(self):
  router=Mock(); planner=Mock(); router.decide.return_value=Proposal(commands=[Command('close')]); valid=[True]
  def prepare(*args): valid[0]=False; return Plan([Step(Command('close'),label='Close')])
  planner.prepare.side_effect=prepare
  with redirect_stdout(StringIO()):
   result=Engine(router,planner,True,Mock()).process('computer close files',valid=lambda:valid[0])
  self.assertEqual(result['verdict'],'cancelled'); planner.execute.assert_not_called()


class LocalControls(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
  self.path=Path(self.temp.name)/'control.sock'; self.control=Control(self.path); self.control.start()
  self.addCleanup(self.control.close)
 def test_private_permissions_and_whitelist(self):
  self.assertEqual(self.path.stat().st_mode&0o777,0o600)
  self.assertFalse(request('execute shell',self.path)['ok'])
  self.assertTrue(request('press',self.path)['ok']); self.assertEqual(self.control.events.get(timeout=1),'press')
 def test_subscription_reports_states_without_polling(self):
  with socket.socket(socket.AF_UNIX) as s:
   s.settimeout(1); s.connect(str(self.path)); s.sendall(b'{"action":"subscribe"}\n')
   with s.makefile('r') as stream:
    self.assertEqual(json.loads(stream.readline())['phase'],'idle')
    self.control.update(phase='listening',level=.5)
    value=json.loads(stream.readline()); self.assertEqual(value['phase'],'listening')
    self.assertNotIn('key',value); self.assertNotIn('text',value)
 def test_malformed_action_does_not_kill_server(self):
  with socket.socket(socket.AF_UNIX) as s:
   s.settimeout(1); s.connect(str(self.path)); s.sendall(b'{"action":{}}\n'); self.assertIn(b'false',s.recv(1024))
  self.assertEqual(request('status',self.path)['phase'],'idle')


class Configuration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name)
  self.patch=patch.dict(os.environ,{'XDG_CONFIG_HOME':str(self.root),'JEV_VOICE_CONFIG':str(self.root/'settings.toml')},clear=True)
  self.patch.start(); self.addCleanup(self.patch.stop)
  self.home=patch('pathlib.Path.home',return_value=self.root); self.home.start(); self.addCleanup(self.home.stop)
 def test_private_key_and_environment_override(self):
  save_key('not-a-real-secret'); self.assertEqual(load_key(),'not-a-real-secret')
  self.assertEqual((self.root/'jev-voice/api-key').stat().st_mode&0o777,0o600)
  with patch.dict(os.environ,{'JEV_API_KEY':'environment-key'}): self.assertEqual(load_key(),'environment-key')
 def test_no_key_and_defaults_work_on_clean_machine(self):
  self.assertIsNone(load_key()); self.assertEqual(load_config(),DEFAULTS)
 def test_bad_settings_fail_clearly(self):
  for text in ['[indicator]\nsize=1000','[indicator]\ndictation_width=90','[voice]\nwake_phrase="computer; exit"','[recognition]\nend_silence_ms=1']:
   (self.root/'settings.toml').write_text(text)
   with self.assertRaises(ValueError): load_config()
 def test_settings_override_only_given_fields(self):
  (self.root/'settings.toml').write_text('[indicator]\nsize=40\n[voice]\nwake_enabled=false\n')
  c=load_config(); self.assertEqual(c['indicator']['size'],40); self.assertEqual(c['indicator']['top'],54)
  self.assertFalse(c['voice']['wake_enabled'])
 def test_reject_multiline_key_without_overwriting_existing(self):
  save_key('old-key')
  with self.assertRaises(ValueError): save_key('one\ntwo')
  self.assertEqual(load_key(),'old-key')

if __name__=='__main__': unittest.main()
