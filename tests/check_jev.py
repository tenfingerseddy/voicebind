"""Opt-in Jev evaluation: synthetic apps, real API, no desktop execution.

Run from the project root: .venv/bin/python tests/check_jev.py
Uses the configured Jev credential and incurs normal provider usage.
"""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from configuration import load_key
from desktop_core.apps import AppCatalog
from plan import Planner
from router import Router
from vt import Jev

CASES = [
    ('files workspace 2', 'open', 'files.desktop', 2),
    ('files two', 'open', 'files.desktop', 2),
    ('workspace two for files', 'open', 'files.desktop', 2),
    ('could I have files over on two', 'open', 'files.desktop', 2),
    ("I'd like my browser over on the second workspace", 'open', 'browser.desktop', 2),
    ('teams over on five please', 'open', 'teams.desktop', 5),
    ('a browser would be handy on desktop three', 'open', 'browser.desktop', 3),
    ('too loud', 'volume', None, None),
    ('screen is a bit dim', 'brightness.up', None, None),
    ('files workspace two then make it full screen', 'open', 'files.desktop', 2),
    ('open files and teams on workspace two', 'open', 'files.desktop', 2),
    ('do not put files on workspace two', None, None, None),
    ('files workspace two if it is open', None, None, None),
    ('files workspace 200', None, None, None),
    ('imaginary app workspace two', None, None, None),
]


def main():
    key = load_key()
    if not key:
        sys.exit('Configure your own Jev key before running this optional check.')
    failed = 0
    with tempfile.TemporaryDirectory(prefix='voicebind-jev-check-') as directory:
        root = Path(directory)
        for name in ('files', 'browser', 'teams'):
            (root/(name+'.desktop')).write_text(
                f'[Desktop Entry]\nType=Application\nName={name}\nExec={name}\nStartupWMClass={name}\n')
        catalog = AppCatalog([root], default_browser='browser.desktop', default_file_manager='files.desktop')
        hypr = Mock()
        hypr.query.side_effect = lambda name: [] if name == 'clients' else {}
        planner = Planner(catalog, {}, hypr)
        client = Jev(key)
        router = Router(catalog, {}, client)
        try:
            for phrase, action, app, workspace in CASES:
                try:
                    router.interpretation['reject_low_confidence'] = action is None
                    proposal = router.decide(phrase, force_jev=True)
                    got = None
                    if action is None:
                        ok = proposal.verdict == 'drop'
                    else:
                        plan = planner.prepare(proposal.commands)
                        command = plan.steps[0].command
                        got = (command.action, command.app, command.workspace)
                        ok = proposal.verdict == 'act' and got == (action, app, workspace)
                        if phrase == 'too loud':
                            ok = ok and command.value < 0 and command.relative
                        if 'then' in phrase:
                            ok = ok and plan.steps[-1].command.action == 'fullscreen'
                        if 'files and teams' in phrase:
                            ok = ok and len(plan.steps) == 2 and plan.steps[1].command.app == 'teams.desktop'
                    failed += not ok
                    print(json.dumps({'phrase': phrase, 'ok': bool(ok), 'planned': got,
                                      'verdict': proposal.verdict, 'ms': proposal.ms}), flush=True)
                except Exception as error:
                    failed += 1
                    print(json.dumps({'phrase': phrase, 'ok': False, 'error': str(error)}), flush=True)
        finally:
            client.close()
        hypr.dispatch.assert_not_called()
    print(f'{len(CASES)-failed}/{len(CASES)} semantic cases passed. No desktop actions executed.')
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
