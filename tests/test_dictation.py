import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from configuration import DEFAULTS
from control import Control
from daemon import Segmenter
from desktop_core.desktop import capture_window
from dictation import Dictation, clean_transcript, control_suffix, is_start_phrase, keyboard_panel_open
from listening import AudioJob, Listener


class Cues(unittest.TestCase):
    def test_natural_start_requests(self):
        for phrase in ['open dictation', 'dictate this', 'begin dictation',
                       'start dictating', 'turn on dictation mode', 'enable dictation',
                       'Please, open dictation!', 'Could you please dictate this?',
                       'dictate this please', 'dictite this']:
            with self.subTest(phrase=phrase): self.assertTrue(is_start_phrase(phrase))

    def test_start_requires_a_whole_request(self):
        for phrase in ['do not open dictation', "don't dictate this", 'never start dictation',
                       'open dictation and close files', 'dictate this is my paragraph',
                       'explain how to start dictation', 'open files', 'computer dictate this']:
            with self.subTest(phrase=phrase): self.assertFalse(is_start_phrase(phrase))

    def test_short_finish_cues_are_removed(self):
        for cue in ['Computer stop.', 'computer finish!', 'computer end',
                    'Hey computer, stop.', 'computer please finish',
                    'computer finish please', 'computer stop now']:
            with self.subTest(cue=cue):
                self.assertEqual(control_suffix(cue),'finish')
                self.assertEqual(clean_transcript('Keep these words. '+cue,True),'Keep these words.')

    def test_short_cues_do_not_match_unaddressed_or_continued_prose(self):
        for phrase in ['stop', 'finish', 'Please stop.', 'The computer stopped.',
                       'computer stop the music', 'computer finish the report',
                       'We say computer stop when we are done.', 'commuter stop',
                       'computer do not stop', 'computer finish dedication']:
            with self.subTest(phrase=phrase):
                self.assertIsNone(control_suffix(phrase))
                self.assertEqual(clean_transcript(phrase),phrase)

    def test_finish_cue_removed_with_punctuation(self):
        for cue in ['Computer finish dictation.', 'computer, stop dictation!', 'COMPUTER END DICTATING']:
            self.assertEqual(control_suffix(cue), 'finish')
            self.assertEqual(clean_transcript('Hello there. '+cue, True), 'Hello there.')

    def test_cancel_and_discard(self):
        for phrase in ['computer cancel dictation','computer discard dictation',
                       'computer cancel','computer discard','computer cancel please']:
            self.assertEqual(control_suffix(phrase),'cancel')

    def test_ordinary_mentions_preserved(self):
        for text in ['Please stop dictation tomorrow.', 'My computer finished dictation.',
                     'Say computer finish dictation to end the session.', 'Computer close files.']:
            self.assertIsNone(control_suffix(text))
            self.assertEqual(clean_transcript(text),text)

    def test_uncertain_voice_ending_does_not_guess(self):
        with self.assertRaises(ValueError): clean_transcript('Hello. Computer finished dedication.',True)

    def test_no_control_keys_or_newlines_in_inserted_prose(self):
        self.assertEqual(clean_transcript('Hello\r\nworld\tthere\x1b\x00.'),'Hello world there .')


class Handoff(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        env=patch.dict(os.environ,{'XDG_STATE_HOME':str(self.root/'state')})
        env.start(); self.addCleanup(env.stop)
        self.client={'address':'0x1','stableId':1}; self.hypr=Mock()
        self.layers={}; self.monitors=[{'name':'DP-1','focused':True,'dpmsStatus':True}]
        self.hypr.query.side_effect=lambda cmd: self.client if cmd=='activewindow' else self.layers if cmd=='layers' else self.monitors
        self.hypr.request.return_value='false'
        self.now=[0.]; self.feedback=Mock(); self.events=Mock()
        self.d=Dictation(self.hypr,self.feedback,self.events,clock=lambda:self.now[0])
        which=patch('dictation.shutil.which',return_value='/test/wtype')
        which.start(); self.addCleanup(which.stop)
        output=patch('dictation.subprocess.run',return_value=Mock(returncode=0))
        self.run=output.start(); self.addCleanup(output.stop)
        self.frame=np.full(320,500,dtype=np.int16)

    def start(self): self.d.start(capture_window(self.client))
    def feed(self,n=10):
        for _ in range(n): self.d.feed(self.frame,500)
    def completed(self,text,voice=True):
        self.start(); self.feed(); self.d.finish(voice=voice); self.d.complete(text,200)
    def draft(self): return (self.root/'state/jev-voice/dictation-draft.txt').read_text()
    def panel(self,address='0xa',monitor='DP-1'):
        return {monitor:{'levels':{'3':[{'namespace':'omarchy-keyboard-panel','address':address}]}}}

    def test_start_reuses_capture_without_external_recorder(self):
        self.start(); self.run.assert_not_called()
        self.events.assert_called_with('dictation_started',capture='shared-microphone',target_kind='window')

    def test_recorded_pcm_comes_from_shared_frames(self):
        self.start(); self.feed(); pcm=self.d.finish()
        np.testing.assert_array_equal(pcm[:3200],np.full(3200,500,dtype=np.int16))
        self.assertEqual(self.d.voiced,10); self.assertEqual(self.d.frames,[])

    def test_only_clean_text_goes_to_stdin_and_never_return(self):
        self.completed('Hello there. Computer finish dictation.')
        self.assertEqual(self.run.call_args.args[0],['wtype','-d','1','-'])
        self.assertEqual(self.run.call_args.kwargs['input'],'Hello there.')
        self.assertFalse(self.d.active); self.assertNotIn('Hello',str(self.events.call_args_list))

    def test_short_ending_is_removed_before_typing(self):
        for cue in ['Computer stop.', 'Computer finish.']:
            self.completed('Hello there. '+cue)
            self.assertEqual(self.run.call_args.kwargs['input'],'Hello there.')

    def test_cancel_in_final_transcript_discards_instead_of_inserting(self):
        self.completed('Do not insert these words. Computer cancel.',voice=False)
        self.run.assert_not_called()
        self.assertIn('dictation_cancelled',str(self.events.call_args_list))
        self.assertFalse(self.d.active)

    def test_explicit_field_is_refocused_before_insertion(self):
        for field,shortcut in [('address',['wtype','-M','ctrl','-k','l','-m','ctrl']),
                               ('compose',['wtype','-M','alt','-M','shift','-k','r','-m','shift','-m','alt'])]:
            self.run.reset_mock()
            self.d.start(capture_window(self.client),field=field)
            self.feed(); self.d.finish(); self.d.complete('Words to insert.')
            self.assertEqual(self.run.call_args_list[0].args[0],shortcut)
            self.assertEqual(self.run.call_args_list[1].kwargs['input'],'Words to insert.')
            self.assertEqual(self.d.field,'current')

    def test_field_focus_failure_saves_the_recognised_text(self):
        self.d.start(capture_window(self.client),field='address')
        self.feed(); self.d.finish()
        self.run.side_effect=subprocess.CalledProcessError(1,['wtype'])
        self.d.complete('Keep these words.')
        self.assertEqual(self.draft(),'Keep these words.')
        self.assertEqual(self.run.call_count,1)

    def test_focus_change_after_field_shortcut_prevents_text_insertion(self):
        self.d.start(capture_window(self.client),field='address')
        self.feed(); self.d.finish()
        self.run.side_effect=lambda *args,**kwargs:setattr(self,'client',{'address':'0x2','stableId':2})
        self.d.complete('Keep these words.')
        self.assertEqual(self.draft(),'Keep these words.'); self.assertEqual(self.run.call_count,1)

    def test_uncertain_end_is_private_draft_without_typing(self):
        self.completed('Hello. Computer finish dedication.')
        self.run.assert_not_called(); self.assertIn('Hello',self.draft())
        self.assertEqual((self.root/'state/jev-voice/dictation-draft.txt').stat().st_mode&0o777,0o600)

    def test_focus_changed_or_address_reused_never_types(self):
        for client in [{'address':'0x2','stableId':2},{'address':'0x1','stableId':2}]:
            self.client={'address':'0x1','stableId':1}; self.start(); self.feed(); self.d.finish(voice=True)
            self.client=client; self.d.complete('Hello. Computer finish dictation.')
            self.run.assert_not_called(); self.assertEqual(self.draft(),'Hello.')

    def test_changed_focus_before_start_is_rejected(self):
        with self.assertRaises(ValueError): self.d.start('another-window')
        self.assertFalse(self.d.active)

    def test_key_finish_needs_no_spoken_cue(self):
        self.completed('A normal paragraph.',voice=False)
        self.assertEqual(self.run.call_args.kwargs['input'],'A normal paragraph.')

    def test_cancel_discards_frames_without_typing(self):
        self.start(); self.feed(); self.d.cancel()
        self.run.assert_not_called(); self.assertFalse(self.d.active); self.assertEqual(self.d.frames,[])

    def test_recording_memory_and_duration_are_bounded(self):
        self.start(); self.feed(3000); self.assertEqual(len(self.d.frames),2750)
        self.now[0]=56; self.assertTrue(self.d.due)
        self.assertLessEqual(len(self.d.finish()),55*16000+2000)

    def test_quiet_audio_is_not_sent_to_asr(self):
        self.start()
        for _ in range(10): self.d.feed(np.zeros(320,dtype=np.int16),0)
        self.assertEqual(len(self.d.finish()),0)

    def test_empty_result_reports_error_instead_of_success(self):
        self.completed('',voice=False)
        self.run.assert_not_called(); self.assertFalse(self.d.active)
        self.assertIn('dictation_empty',str(self.events.call_args_list))
        self.assertNotIn('dictation_finished',str(self.events.call_args_list))

    def test_finish_cue_alone_inserts_nothing(self):
        self.completed('Computer finish dictation.')
        self.assertFalse(self.d.active); self.run.assert_not_called()

    def test_focused_bar_panel_accepts_dictation(self):
        self.layers=self.panel(); self.completed('Hello. Computer finish dictation.')
        self.assertEqual(self.run.call_args.kwargs['input'],'Hello.')

    def test_panel_replaced_during_dictation_preserves_draft(self):
        self.layers=self.panel(); self.start(); self.feed(); self.d.finish(voice=True)
        self.layers=self.panel('0xb'); self.d.complete('Hello. Computer finish dictation.')
        self.run.assert_not_called(); self.assertEqual(self.draft(),'Hello.')

    def test_panel_closed_during_dictation_preserves_draft(self):
        self.layers=self.panel(); self.start(); self.feed(); self.d.finish(voice=True)
        self.layers={}; self.d.complete('Hello. Computer finish dictation.')
        self.run.assert_not_called(); self.assertEqual(self.draft(),'Hello.')

    def test_panel_on_another_monitor_does_not_block_app(self):
        self.layers=self.panel(monitor='DP-2'); self.assertFalse(keyboard_panel_open(self.hypr))
        self.completed('Hello. Computer finish dictation.')
        self.assertEqual(self.run.call_args.kwargs['input'],'Hello.')

    def test_sleeping_display_blocks_start(self):
        self.monitors[0]['dpmsStatus']=False
        with self.assertRaises(ValueError): self.start()
        self.assertFalse(self.d.active)

    def test_locked_desktop_blocks_start(self):
        self.hypr.request.return_value='true'
        with self.assertRaises(ValueError): self.start()
        self.assertFalse(self.d.active)

    def test_desktop_locked_during_recording_never_types(self):
        self.start(); self.feed(); self.d.finish(voice=True); self.hypr.request.return_value='true'
        self.d.complete('Hello. Computer finish dictation.')
        self.run.assert_not_called(); self.assertEqual(self.draft(),'Hello.')

    def test_panel_opened_over_app_preserves_draft(self):
        self.start(); self.feed(); self.d.finish(voice=True); self.layers=self.panel()
        self.d.complete('Hello. Computer finish dictation.')
        self.run.assert_not_called(); self.assertEqual(self.draft(),'Hello.')

    def test_insertion_failure_preserves_draft(self):
        self.run.side_effect=subprocess.TimeoutExpired('wtype',8)
        self.completed('Keep these words. Computer finish dictation.')
        self.assertEqual(self.draft(),'Keep these words.'); self.assertFalse(self.d.active)


class Routing(unittest.TestCase):
    def setUp(self):
        self.engine=Mock(); self.engine.live=True
        self.planner=Mock(); self.decode=Mock(return_value=(1,'computer dictate'))
        self.log=Mock(); self.control=Control('/tmp/not-started-dictation-test.sock')
        self.l=Listener(self.engine,self.planner,Segmenter(),DEFAULTS,self.log,
                        threading.Event(),self.control,self.decode,lambda:0)
        self.d=Mock(); self.d.active=False; self.d.phase='idle'; self.l.dictation=self.d
    def job(self,activation=None):
        return AudioJob(np.zeros(320,dtype=np.int16),0,'captured-target',0,self.l.epoch,activation)

    def test_start_is_local_and_captures_original_target(self):
        self.l.process_job(self.job())
        self.d.start.assert_called_once_with('captured-target')
        self.engine.process.assert_not_called(); self.assertTrue(self.l.clear_pending)
        self.assertEqual(self.control.snapshot()['phase'],'dictating')

    def test_observed_dictite_mishearing_stays_local(self):
        self.decode.return_value=(1,'Computer Dictite.')
        self.l.process_job(self.job())
        self.d.start.assert_called_once_with('captured-target')
        self.engine.process.assert_not_called()

    def test_natural_start_requests_use_local_path(self):
        for phrase in ['Computer open dictation.', 'Computer dictate this.',
                       'Hey computer, please begin dictation.', 'Computer turn on dictation mode.']:
            self.d.start.reset_mock(); self.decode.return_value=(1,phrase)
            self.l.process_job(self.job())
            self.d.start.assert_called_once_with('captured-target')
        self.engine.process.assert_not_called()

    def test_short_finish_controls_only_active_dictation(self):
        self.engine.process.return_value=None
        self.decode.return_value=(1,'Computer finish.')
        self.l.process_job(self.job()); self.d.finish.assert_not_called()
        self.engine.process.assert_called_once()
        self.d.active=True; self.d.phase='recording'
        self.l.process_job(self.job('dictation'))
        self.d.finish.assert_called_once_with(voice=True)
        self.engine.process.assert_called_once()

    def test_target_preparation_is_bound_before_recording(self):
        from dictation_target import Destination
        self.decode.return_value=(1,'Computer dictate in Teams.')
        with patch('listening.parse_start',return_value=Destination('teams.desktop','compose')), \
             patch('listening.prepare_destination',return_value='resolved-teams') as prepare:
            self.l.process_job(self.job())
        prepare.assert_called_once()
        self.d.start.assert_called_once_with('resolved-teams',field='compose')
        self.engine.process.assert_not_called()

    def test_cancelled_destination_preparation_does_not_start_recording(self):
        from dictation_target import Destination
        self.decode.return_value=(1,'Computer dictate in Teams.')
        with patch('listening.parse_start',return_value=Destination('teams.desktop','compose')), \
             patch('listening.prepare_destination',return_value=None):
            self.l.process_job(self.job())
        self.d.start.assert_not_called(); self.engine.process.assert_not_called()

    def test_dry_run_does_not_start_dictation(self):
        self.engine.live=False; self.l.process_job(self.job())
        self.d.start.assert_not_called(); self.engine.process.assert_not_called()

    def test_wakeless_start_ignored_except_explicit_ptt(self):
        self.decode.return_value=(1,'dictate'); self.l.process_job(self.job())
        self.d.start.assert_not_called()
        self.l.process_job(self.job('ptt')); self.d.start.assert_called_once()

    def test_desktop_commands_are_only_prose_during_dictation(self):
        self.d.active=True; self.d.phase='recording'
        self.decode.return_value=(1,'computer close files')
        self.l.process_job(self.job('dictation'))
        self.d.finish.assert_not_called(); self.engine.process.assert_not_called(); self.log.assert_not_called()

    def test_spoken_finish_never_routes_or_logs_body(self):
        self.d.active=True; self.d.phase='recording'
        self.decode.return_value=(1,'These are private words. Computer finish dictation.')
        self.l.process_job(self.job('dictation'))
        self.d.finish.assert_called_once_with(voice=True)
        self.engine.process.assert_not_called(); self.log.assert_not_called()

    def test_f10_finishes_instead_of_starting_command_capture(self):
        self.d.active=True; self.d.phase='recording'; self.l.command('press'); self.l.command('release')
        self.d.finish.assert_called_once_with(voice=False); self.assertFalse(self.l.ptt)

    def test_spoken_cancel_and_control_key_discard(self):
        self.d.active=True; self.d.phase='recording'
        self.decode.return_value=(1,'Computer cancel dictation.')
        self.l.process_job(self.job('dictation')); self.d.cancel.assert_called_once()
        self.assertEqual(self.control.snapshot()['phase'],'idle'); self.engine.process.assert_not_called()

    def test_ending_cue_remains_available_after_long_continuous_prose(self):
        self.d.active=True; self.d.phase='recording'
        for i in range(1200): self.l.frame(np.full(320,1000,dtype=np.int16),i*.02)
        for i in range(30): self.l.frame(np.zeros(320,dtype=np.int16),24+i*.02)
        job=self.l.work.get_nowait()
        self.assertEqual(job.activation,'dictation'); self.assertLessEqual(len(job.pcm),6*16000)
        self.decode.return_value=(1,'Computer finish dictation.')
        self.l.process_job(job); self.d.finish.assert_called_once_with(voice=True)

    def test_cancellation_during_final_decode_prevents_insertion(self):
        self.d.active=True; self.d.phase='processing'
        def decode(*args,**kwargs):
            self.l.command('cancel'); return 200,'Do not insert these words'
        self.l.decode=decode; self.l.process_job(self.job('dictation-final'))
        self.d.complete.assert_not_called(); self.d.cancel.assert_called_once()

    def test_final_decode_uses_the_existing_decoder_and_never_jev(self):
        self.d.active=True; self.d.phase='processing'
        self.decode.return_value=(120,'Words. Computer finish dictation.')
        self.l.process_job(self.job('dictation-final'))
        self.d.complete.assert_called_once_with('Words. Computer finish dictation.',120)
        self.engine.process.assert_not_called(); self.assertEqual(self.control.snapshot()['phase'],'idle')

    def test_capture_feeds_same_frame_to_dictation(self):
        self.d.active=True; self.d.phase='recording'
        frame=np.full(320,500,dtype=np.int16); self.l.frame(frame,0)
        self.assertIs(self.d.feed.call_args.args[0],frame)
        self.assertEqual(self.d.feed.call_args.args[1],500)


if __name__=='__main__': unittest.main()
