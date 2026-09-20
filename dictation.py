"""Buffered dictation from the existing Jev microphone and local recognizer."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata

from desktop_core.desktop import capture_window
from vt import WAKE, RATE
import numpy as np

START_PHRASES = {
    'dictate', 'dictate this', 'dictate for me', 'dictite', 'dictite this',
    'dictation', 'dictation mode', 'take dictation',
    'start dictating', 'begin dictating',
} | {f'{verb} {noun}'
     for verb in ('start','begin','open','enable','activate','turn on')
     for noun in ('dictation','dictation mode')}
RUNTIME_ACTIONS = {
    'dictation.start': 'Start dictation: computer dictate, open dictation, or dictate this',
    'dictation.finish': 'Finish and insert dictation: computer stop or computer finish',
    'dictation.cancel': 'Discard the current dictation without inserting it',
}
FIELD_KEYS = {
    'current': [],
    'address': ['-M','ctrl','-k','l','-m','ctrl'],
    # Teams web/PWA compose box. Ctrl+R would reload its browser window.
    'compose': ['-M','alt','-M','shift','-k','r','-m','shift','-m','alt'],
}


def focus_field(field):
    if field not in FIELD_KEYS: raise ValueError('Unknown dictation input field')
    if FIELD_KEYS[field]:
        subprocess.run(['wtype',*FIELD_KEYS[field]],check=True,capture_output=True,timeout=2)
        time.sleep(.12)


def normalize_start(text):
    phrase = re.sub(r'[\s,.:;!?-]+',' ',text.casefold()).strip()
    phrase = re.sub(r'^(?:please|(?:can|could|would) you(?: please)?) ','',phrase)
    return re.sub(r' please$','',phrase)


def is_start_phrase(text):
    # Match a complete request, preserving negation and trailing prose/clauses.
    return normalize_start(text) in START_PHRASES


def cue_pattern(wake=WAKE):
    # Stop is reserved only at the end of a dictation segment, not in the
    # middle of ordinary prose. Punctuation between recognised words is fine.
    gap = r'[\s,.:;!?-]+'
    return re.compile(r'(?<!\w)(?:(?:hey|ok|okay)' + gap + r')?' + re.escape(wake) + gap +
                      r'(?:please' + gap + r')?(?P<verb>finish|stop|end|cancel|discard)' +
                      r'(?:' + gap + r'(?:dictation|dictating|dictate))?' +
                      r'(?:' + gap + r'(?:please|now))?[\s.!?,;:]*$', re.I)


def control_suffix(text):
    match = cue_pattern().search(text)
    if not match: return None
    return 'cancel' if match['verb'].lower() in {'cancel','discard'} else 'finish'


def clean_transcript(text, require_cue=False):
    text = text.strip()
    match = cue_pattern().search(text)
    if require_cue and not match:
        raise ValueError('The stop phrase could not be separated reliably')
    if match:
        text = text[:match.start()].rstrip(' ,;:')
    # Whisper line wrapping is not an instruction to press Enter. This mode
    # inserts prose only; it never sends Return or an auto-submit command.
    text = ''.join(' ' if unicodedata.category(c) == 'Cc' else c for c in text)
    return re.sub(r'\s+', ' ', text).strip()


def keyboard_panel_open(hypr, focused_only=True):
    # Hyprland's activewindow retains the underlying window when a shell
    # keyboard panel owns the seat. Never mistake that for a focused editor.
    layers = hypr.query('layers')
    if focused_only:
        monitors = hypr.query('monitors')
        if isinstance(monitors,list):
            focused = {m['name'] for m in monitors if m.get('focused')}
            layers = {name: value for name,value in layers.items() if name in focused}
    return any(layer.get('namespace') == 'omarchy-keyboard-panel'
               for monitor in layers.values() if isinstance(monitor,dict)
               for level in monitor.get('levels',{}).values()
               for layer in level)


def display_asleep(hypr):
    monitors = hypr.query('monitors')
    return isinstance(monitors,list) and not any(m.get('focused') and m.get('dpmsStatus',True) for m in monitors)


def session_locked(hypr):
    result = hypr.request('locked').strip()
    if result not in {'true','false'}:
        raise RuntimeError('Cannot check the desktop lock state')
    return result == 'true'


def input_target(hypr):
    """Bind the current app or keyboard panel without moving keyboard focus."""
    monitors = hypr.query('monitors')
    focused = {m['name'] for m in monitors if m.get('focused')}
    layers = hypr.query('layers')
    panels = tuple(sorted((name,layer['address'])
                   for name,monitor in layers.items() if name in focused
                   for level in monitor.get('levels',{}).values()
                   for layer in level if layer.get('namespace')=='omarchy-keyboard-panel'))
    return capture_window(hypr.query('activewindow')), panels


class Dictation:
    def __init__(self, hypr, feedback, event, clock=time.monotonic):
        self.hypr, self.feedback, self.event, self.clock = hypr, feedback, event, clock
        self.phase = 'idle'; self.target = None
        self.field = 'current'
        self.frames = []; self.voiced = 0; self.first_voice = self.last_voice = None
        self.deadline = 0; self.voice_finish = False; self.audio_ms = 0

    @property
    def active(self): return self.phase != 'idle'

    @property
    def due(self): return self.phase == 'recording' and self.clock() >= self.deadline

    def start(self, target, field='current'):
        if self.active: return
        if field not in FIELD_KEYS: raise ValueError('Unknown dictation input field')
        if not shutil.which('wtype'):
            raise ValueError('Install wtype for text insertion')
        if session_locked(self.hypr):
            raise ValueError('Unlock the desktop and focus a text field before dictating')
        if display_asleep(self.hypr):
            raise ValueError('Wake the display and focus a text field before dictating')
        surface = input_target(self.hypr)
        if not (surface[0] or surface[1]):
            raise ValueError('Focus a text field before dictating')
        if target and target != surface[0]:
            raise ValueError('Focus changed before dictation started; say the command again')
        self.target = surface
        self.field = field
        self.frames = []; self.voiced = 0; self.first_voice = self.last_voice = None
        self.phase = 'recording'; self.deadline = self.clock()+55
        self.event('dictation_started',capture='shared-microphone',target_kind='panel' if surface[1] else 'window')
        self.feedback(f'Dictation on. Say "{WAKE} stop" or "{WAKE} finish", or tap F10. Ctrl+F10 cancels.')

    def feed(self, frame, rms):
        if self.phase != 'recording' or len(self.frames) >= 2750: return
        self.frames.append(frame)
        if rms > 180:
            self.voiced += 1
            if self.first_voice is None: self.first_voice = len(self.frames)-1
            self.last_voice = len(self.frames)-1

    def finish(self, voice=False):
        if self.phase != 'recording': return None
        self.voice_finish = voice; self.phase = 'processing'
        self.audio_ms = len(self.frames)*20
        self.event('dictation_finishing',control='voice' if voice else 'key-or-time-limit',
                   audio_ms=self.audio_ms,voiced_ms=self.voiced*20)
        if self.voiced < 4:
            pcm = np.empty(0,dtype=np.int16)
        else:
            frames = self.frames[max(0,self.first_voice-12):self.last_voice+29]
            pcm = np.concatenate([*frames,np.zeros(RATE//8,dtype=np.int16)])
        self.frames = []
        return pcm

    def cancel(self):
        if not self.active: return
        self.cleanup()
        self.event('dictation_cancelled'); self.feedback('Dictation discarded')

    def save_draft(self, text):
        directory = Path(os.environ.get('XDG_STATE_HOME',str(Path.home()/'.local/state')))/'jev-voice'
        directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        path = directory/'dictation-draft.txt'
        fd, temporary = tempfile.mkstemp(prefix='.dictation-',dir=directory)
        try:
            with os.fdopen(fd,'w') as f: f.write(text)
            os.replace(temporary,path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path

    def complete(self, raw, asr_ms=0):
        if self.phase != 'processing': return
        try:
            if not raw.strip():
                self.event('dictation_empty',audio_ms=self.audio_ms,voiced_ms=self.voiced*20)
                raise ValueError('No speech was recognised. Nothing typed; try dictating again.')
            if control_suffix(raw) == 'cancel':
                self.cancel(); return
            try: text = clean_transcript(raw,self.voice_finish)
            except ValueError:
                self.save_draft(raw)
                raise ValueError('Stop phrase unclear. Nothing typed; the private draft is available with jev-voice dictation-draft') from None
            if not text:
                self.feedback('Dictation finished; no words before the finish phrase')
                self.event('dictation_finished',characters=0); return
            if session_locked(self.hypr) or display_asleep(self.hypr) or input_target(self.hypr) != self.target:
                self.save_draft(text)
                raise ValueError('Focus or display changed. Nothing typed; the private draft is available with jev-voice dictation-draft')
            if self.field != 'current':
                try: focus_field(self.field)
                except (OSError,subprocess.SubprocessError):
                    self.save_draft(text)
                    raise RuntimeError('Could not focus the requested input field; a private dictation draft was saved') from None
                if session_locked(self.hypr) or display_asleep(self.hypr) or input_target(self.hypr) != self.target:
                    self.save_draft(text)
                    raise ValueError('Focus changed before insertion; a private dictation draft was saved')
            # stdin avoids putting prose in process arguments. No Return or
            # clipboard change; 1 ms is the smallest accepted wtype delay here.
            try:
                result = subprocess.run(['wtype','-d','1','-'],input=text,text=True,capture_output=True,timeout=8)
            except (OSError,subprocess.SubprocessError):
                self.save_draft(text)
                raise RuntimeError('Text insertion interrupted; a private dictation draft was saved') from None
            if result.returncode:
                self.save_draft(text)
                raise RuntimeError('Text insertion failed; a private dictation draft was saved')
            self.event('dictation_finished',characters=len(text),whisper_ms=round(asr_ms,1))
            self.feedback('Dictation inserted')
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
            self.fail(error)
        finally:
            self.cleanup()

    def fail(self, error):
        self.event('dictation_error',reason=str(error)); self.feedback(str(error),True)
        self.cleanup()

    def cleanup(self):
        self.frames = []; self.target = None; self.phase = 'idle'; self.field = 'current'
