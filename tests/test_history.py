from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from daemon import Engine
from desktop_core.commands import Command
from history import entry, recent, MAX_ENTRIES
from router import Proposal


class History(unittest.TestCase):
    def test_command_outcome_and_timings_without_model_payload(self):
        record = {'t': 10, 'text': 'computer open files', 'verdict': 'act', 'route': 'jev',
                  'activation': 'wake', 'plan': 'Open files', 'result': {'ok': True, 'message': 'Window opened'},
                  'ms': {'whisper': 220, 'jev': 400, 'end_of_speech_to_result': 982, 'secret': 'private-secret'},
                  'answers': {'private-secret': 'not-for-ui'}, 'api_key': 'private-secret'}
        row = entry(record)
        self.assertEqual(row['outcome'], 'Completed')
        self.assertEqual(row['heard'], record['text'])
        self.assertEqual(row['ms']['end_of_speech_to_result'], 982)
        self.assertNotIn('private-secret', json.dumps(row))
        record['result'] = {'ok': False, 'message': 'Step 2 failed; window disappeared'}
        self.assertTrue(entry(record)['issue'])
        self.assertEqual(entry(record)['outcome'], 'Error')

    def test_dictation_displays_only_outcome_and_count(self):
        row = entry({'t': 20, 'event': 'dictation_finished', 'characters': 91, 'whisper_ms': 320,
                     'text': 'private dictated prose', 'raw': 'private dictated prose'})
        self.assertEqual(row['message'], '91 characters inserted.')
        self.assertNotIn('private dictated prose', json.dumps(row))
        self.assertIsNone(entry({'t': 21, 'event': 'dictation_probe', 'text': 'private dictated prose'}))
        self.assertEqual(entry({'t':22,'event':'dictation_error','reason':'Focus changed'})['message'], 'Focus changed')

    def test_multiple_sessions_are_sorted_by_event_time_and_partial_writes_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A long-running listener can have more recent events than a newly
            # started one-off process. Session filenames aren't event times.
            (root/'older.jsonl').write_text(json.dumps({'t':99,'event':'ready'})+'\n')
            (root/'newer.jsonl').write_bytes((json.dumps({'t':80,'event':'recognition_error','reason':'Offline'})+'\n').encode()+b'broken\n{"t":100')
            rows = recent(root)
            self.assertEqual([r['t'] for r in rows], [99,80])
            self.assertEqual(rows[-1]['message'], 'Offline')

    def test_history_is_bounded_and_ignores_capture_noise(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            records = [{'t':n,'event':'wake_armed'} for n in range(150)]
            records += [{'t':151,'event':'audio_timing','whisper_ms':200}, {'t':152,'event':'wake_detected'}]
            (root/'session.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            rows=recent(root)
            self.assertEqual(len(rows), MAX_ENTRIES)
            self.assertEqual((rows[0]['t'],rows[-1]['t']), (149,50))
            self.assertEqual(recent(root/'missing'), [])

    def test_dry_run_and_confirmation_are_not_reported_as_executed(self):
        row=entry({'t':1,'verdict':'act','result':{'ok':True,'dry_run':True,'message':'Dry run'}})
        self.assertEqual(row['outcome'],'Dry run')
        row=entry({'t':2,'verdict':'confirm','plan':'Restart','prompt':'Say computer yes'})
        self.assertEqual(row['outcome'],'Confirmation requested')
        self.assertEqual(row['message'],'Say computer yes')
        self.assertNotEqual(entry({'t':3,'verdict':'act'})['outcome'],'Completed')

    def test_history_request_does_not_refresh_settings_or_expose_credentials(self):
        from panel_backend import handle
        with patch('history.recent', return_value=[{'title':'Example'}]), patch('panel_backend.snapshot') as snapshot:
            result=handle({'action':'history'})
        self.assertEqual(result, {'ok':True,'history':[{'title':'Example'}]})
        snapshot.assert_not_called()

    def test_normal_error_and_confirmation_feedback_never_spawns_notifications(self):
        router=Mock(config={}); planner=Mock(); bookmarks=Mock()
        bookmarks.prepare.return_value=None
        router.decide.return_value=Proposal(commands=[Command('close')])
        planner.prepare.return_value=Mock(label='Close the window')
        planner.execute.return_value={'ok':True,'message':'Closed'}
        log=Mock()
        engine=Engine(router,planner,True,log=log,bookmarks=bookmarks)
        with patch('daemon.subprocess.Popen') as popen, redirect_stdout(StringIO()):
            self.assertEqual(engine.process('computer close this')['result']['message'],'Closed')
            router.decide.side_effect=RuntimeError('Offline')
            self.assertEqual(engine.process('computer open files')['reason'],'Offline')
            router.decide.side_effect=None
            router.decide.return_value=Proposal(commands=[Command('close_all')],verdict='confirm')
            result=engine.process('computer close all windows')
            self.assertIn('computer yes',result['prompt'])
        popen.assert_not_called()
        self.assertEqual(log.call_count,3)


if __name__=='__main__': unittest.main()
