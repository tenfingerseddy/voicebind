"""Jev priority, opt-in rejection and configuration persistence. No desktop actions."""
from unittest.mock import Mock, patch
import unittest

from test_pipeline import Fixture
import test_panel_backend
from configuration import load_config
import panel_backend as panel
from router import probability


class ConfidenceRouting(Fixture):
    def replies(self, action='fullscreen', score=.3, addressed=.3, complete=.3):
        values = {'action0': action, 'target0': 'current', 'workspace0': 'none',
                  'focus0': 'follow', 'new0': 'existing', 'folder0': 'none'}
        answers = {key: {'choice': value, 'probabilities': {value: score}}
                   for key, value in values.items()}
        answers.update(addressed={'noul': addressed}, complete0={'noul': complete})
        self.router.jev = Mock()
        self.router.jev.ask.return_value = (25, {'answers': answers})
        return answers

    def test_jev_runs_before_a_known_local_command(self):
        self.replies()
        with patch.object(self.router, 'local', side_effect=AssertionError('local ran first')):
            proposal = self.router.decide('make this app full screen')
        self.assertEqual(proposal.route, 'jev')
        self.assertEqual(proposal.verdict, 'act')
        self.assertEqual(proposal.commands[0].action, 'fullscreen')
        self.router.jev.ask.assert_called_once()

    def test_low_intent_completeness_and_action_scores_are_optional(self):
        self.replies(score=.12, addressed=.2, complete=.3)
        proposal = self.router.decide('could it fill the screen')
        self.assertEqual((proposal.verdict, proposal.confidence), ('act', .12))
        self.assertEqual(proposal.commands[0].reference, 'previous')
        self.assertNotIn('none', self.router.jev.ask.call_args.kwargs['questions']['action0']['criteria'])

    def test_rejection_is_configurable_at_the_boundary(self):
        self.replies(score=.45, addressed=.9, complete=.9)
        self.router.interpretation.update(reject_low_confidence=True, minimum_confidence=60)
        proposal = self.router.decide('make this app full screen')
        self.assertEqual(proposal.verdict, 'drop')
        self.assertIn('45%', proposal.reason)
        self.assertIn('60%', proposal.reason)
        self.router.interpretation['minimum_confidence'] = 45
        self.assertEqual(self.router.decide('make this app full screen').verdict, 'act')
        self.router.interpretation['minimum_confidence'] = 46
        self.assertEqual(self.router.decide('make this app full screen').verdict, 'drop')

    def test_each_judgement_respects_the_same_threshold(self):
        self.router.interpretation.update(reject_low_confidence=True, minimum_confidence=40)
        for low in ('score', 'addressed', 'complete'):
            scores = dict(score=.9, addressed=.9, complete=.9)
            scores[low] = .39
            self.replies(**scores)
            with self.subTest(low=low):
                result = self.router.decide('make this app full screen')
                self.assertEqual((result.verdict, result.confidence), ('drop', .39))

    def test_best_supported_probability_wins_even_if_none_was_ranked_first(self):
        answers = self.replies(score=.9, addressed=.9, complete=.9)
        answers['action0'] = {'choice': 'none', 'probabilities': {'none': .7, 'fullscreen': .2, 'close': .1}}
        self.assertEqual(self.router.decide('could it fill the screen').commands[0].action, 'fullscreen')
        self.router.interpretation['reject_low_confidence'] = True
        self.assertEqual(self.router.decide('could it fill the screen').verdict, 'drop')

    def test_highest_probability_is_not_a_rounded_choice_field(self):
        self.assertEqual(probability({'choice': 'close', 'probabilities': {'close': .3, 'fullscreen': .4}},
                                     {'close', 'fullscreen'}), ('fullscreen', .4))
        for bad in (float('nan'), float('inf'), True, -1, 1.1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probability({'choice': 'close', 'probabilities': {'close': bad}}, {'close'})

    def test_outage_uses_local_command_after_jev_attempt(self):
        self.router.jev = Mock()
        self.router.jev.ask.side_effect = RuntimeError('Jev HTTP 503')
        result = self.router.decide('open teams on workspace 5')
        self.router.jev.ask.assert_called_once()
        self.assertEqual((result.route, result.verdict, result.commands[0].workspace), ('local', 'act', 5))
        self.assertIn('local interpretation', result.reason)

    def test_jev_first_keeps_both_apps_in_a_shared_verb_request(self):
        answers = self.replies(action='open', score=.9, addressed=.9, complete=.9)
        for index, app in enumerate(('files', 'teams')):
            for name, value in {'action': 'open', 'target': app, 'workspace': '2',
                                'focus': 'follow', 'new': 'existing'}.items():
                answers[name + str(index)] = {'choice': value, 'probabilities': {value: .9}}
            answers['complete' + str(index)] = {'noul': .9}
        result = self.router.decide('open files and teams on workspace two')
        self.assertEqual(result.route, 'jev')
        self.assertEqual([(c.app, c.workspace) for c in result.commands],
                         [('files.desktop', 2), ('teams.desktop', 2)])
        self.router.jev.ask.assert_called_once()
        self.assertEqual(self.router.jev.ask.call_args.args[1]['clauses'],
                         ['open files on workspace 2', 'open teams on workspace 2'])

    def test_explicit_cancellation_does_not_turn_into_an_action(self):
        self.replies()
        self.assertEqual(self.router.decide('cancel').reason, 'Cancelled')
        self.router.jev.ask.assert_not_called()


class ConfidenceSettings(unittest.TestCase):
    setUp = test_panel_backend.PanelSettings.setUp

    def test_save_and_reload_rejection_settings(self):
        for enabled, threshold in [(True, 35), (False, 35), (True, 0), (True, 100)]:
            panel.save({'config': {'interpretation': {'reject_low_confidence': enabled,
                                                     'minimum_confidence': threshold}},
                        'revision': panel.revision(panel.raw_config())})
            self.assertEqual(load_config()['interpretation'], {
                'reject_low_confidence': enabled, 'minimum_confidence': threshold})

    def test_invalid_threshold_does_not_overwrite_saved_settings(self):
        original = panel.raw_config()
        for value in (-1, 101, 50.5, True, '60'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                panel.save({'config': {'interpretation': {'minimum_confidence': value}},
                            'revision': panel.revision(original)})
            self.assertEqual(panel.raw_config(), original)
        with self.assertRaises(ValueError):
            panel.save({'config': {'interpretation': {'reject_low_confidence': 'false'}},
                        'revision': panel.revision(original)})
        self.assertEqual(panel.raw_config(), original)
