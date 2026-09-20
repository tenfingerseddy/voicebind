"""Resolve named dictation destinations and focus explicit input fields."""
from dataclasses import dataclass
import re
import shutil

from desktop_core.commands import Command
from desktop_core.desktop import WindowTarget, capture_window
from dictation import is_start_phrase, normalize_start, input_target, session_locked, display_asleep, focus_field
from language import app_name


@dataclass(frozen=True)
class Destination:
    app: str | None = None
    field: str = 'current'


def same_app(catalog, app, alias):
    try: return catalog.resolve(alias).id == app.id
    except ValueError: return False


def parse_start(text, catalog):
    phrase = normalize_start(text)
    if is_start_phrase(phrase): return Destination()
    match = re.fullmatch(r'(.+?) (?:in|into|to) (.+)',phrase)
    if match and (is_start_phrase(match[1]) or match[1] in {'dictated','dictating'}):
        destination = match[2]
    else:
        # Measured Whisper contractions: "dictate in Teams" -> "dictated
        # Teams", and "dictate in browser ..." -> "dictating browser ...".
        # Resolve the entire remainder against the installed catalogue; no
        # fuzzy app matching or changes to ordinary dictated prose.
        short = re.fullmatch(r'(?:dictate|dictated|dictating|dictation|dictite) (.+)',phrase)
        if not short: return None
        destination = short[1]
    address = re.fullmatch(r'(?:(?:the|my) )?(?:(.+?)(?:\'s)? )?(?:address|url) ?bar',destination)
    alias = (address[1] or 'browser') if address else destination
    app = catalog.resolve(app_name(alias,catalog))
    if address:
        browsers = ('browser','chromium','google chrome','firefox','brave browser','zen browser')
        if not any(same_app(catalog,app,name) for name in browsers) or '--app-id=' in app.exec or '--app=' in app.exec:
            raise ValueError('An address bar requires a browser window')
        return Destination(app.id,'address')
    return Destination(app.id,'compose' if same_app(catalog,app,'teams') else 'current')


def prepare_destination(destination, planner, original, valid=lambda:True):
    """Focus/launch one resolved app, then use its explicit field shortcut.

    No clipboard, text entry or Enter. The caller starts capture only after
    this returns the verified window identity. Cancellation skips later steps.
    """
    desktop = planner.desktop; hypr = desktop.hypr
    if not valid(): return None
    if session_locked(hypr) or display_asleep(hypr):
        raise ValueError('Unlock and wake the display before dictating')
    if not shutil.which('wtype'): raise ValueError('Install wtype for text insertion')
    if original and capture_window(hypr.query('activewindow')) != original:
        raise ValueError('Focus changed before dictation started; say the command again')
    if not valid(): return None
    result = desktop.execute(Command('open',app=destination.app,focus=True),original)
    if not result.get('ok') or not result.get('window'):
        raise ValueError(result.get('message','Could not focus the dictation app'))
    target = WindowTarget(result['window'],result['stable_id']) if result.get('stable_id') is not None else result['window']
    if not valid(): return None
    # An explicit dictation destination needs foreground focus even when the
    # user's ordinary app-launch preference is to remain on the current app.
    if capture_window(hypr.query('activewindow')) != target:
        client = next((c for c in hypr.query('clients') if capture_window(c)==target),None)
        if client is None: raise ValueError('The dictation app closed before it could receive focus')
        desktop.place(client,None,True)

    def check_focus():
        if not valid(): return False
        window,panels = input_target(hypr)
        if session_locked(hypr) or display_asleep(hypr) or window != target:
            raise ValueError('Focus changed before dictation started; say the command again')
        if panels:
            raise ValueError('Close the open keyboard panel before dictating into a named app')
        return True

    if not check_focus(): return None
    if destination.field != 'current':
        focus_field(destination.field)
        if not check_focus(): return None
    return target
