"""A bounded, read-only view of existing local diagnostics for the bar popup."""
import json
import math
from pathlib import Path
from runtime_paths import state_home

SESSIONS = state_home()/'sessions'
MAX_FILES = 24
MAX_BYTES = 256 * 1024
MAX_ENTRIES = 100

EVENTS = {
    'ready': ('Listener ready', 'Ready', 'Voicebind is listening.', False),
    'stopped': ('Listener stopped', 'Stopped', '', False),
    'wake_armed': ('Wake phrase heard', 'Ready', 'Waiting for the next spoken command.', False),
    'toggle-wake': ('Wake listening changed', 'Changed', 'F10 still works.', False),
    'cancel': ('Voice request cancelled', 'Cancelled', '', False),
    'dictation_started': ('Dictation started', 'Recording', '', False),
    'dictation_finished': ('Dictation finished', 'Completed', '', False),
    'dictation_cancelled': ('Dictation cancelled', 'Cancelled', 'Nothing inserted.', False),
    'dictation_error': ('Dictation failed', 'Error', '', True),
    'recognition_error': ('Speech recognition failed', 'Error', '', True),
    'confirmation_expired': ('Confirmation expired', 'Not run', 'Nothing ran.', True),
    'question_expired': ('Question expired', 'Not run', 'Nothing ran.', True),
    'busy_audio_discarded': ('Request discarded', 'Not run', 'The listener was busy.', True),
    'stale_audio_discarded': ('Request discarded', 'Not run', 'The recording was too old to act on.', True),
    'overlong_audio_discarded': ('Recording discarded', 'Not run', 'Speech or noise continued too long.', True),
    'recording_limit': ('Recording cancelled', 'Not run', 'Reached the 20-second command limit.', True),
}


def text(value, limit=1000):
    return value[:limit] if isinstance(value, str) else ''


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def entry(record):
    """Only display allowed fields, never raw model payloads or dictation prose."""
    if not isinstance(record, dict) or not number(record.get('t')): return None
    row = {'t': record['t'], 'heard': '', 'plan': '', 'message': '', 'route': '',
           'activation': '', 'ms': {}, 'issue': False}
    event = record.get('event')
    if event:
        if event not in EVENTS: return None
        title, outcome, message, issue = EVENTS[event]
        row.update(title=title, outcome=outcome, message=text(record.get('reason')) or message, issue=issue)
        if event == 'dictation_finished':
            count = record.get('characters')
            if number(count): row['message'] = f'{int(count)} characters inserted.' if count else 'No words to insert.'
            if number(record.get('whisper_ms')): row['ms']['whisper'] = record['whisper_ms']
        elif event == 'dictation_started':
            row['message'] = 'Dictation text is not stored in History.'
        elif event == 'toggle-wake' and isinstance(record.get('wake_enabled'), bool):
            row['title'] = 'Wake listening '+('on' if record['wake_enabled'] else 'paused')
    else:
        verdict = record.get('verdict')
        if verdict not in {'act', 'confirmed', 'confirm', 'clarify', 'drop', 'error', 'cancelled'}: return None
        result = record.get('result') if isinstance(record.get('result'), dict) else {}
        row.update(title='Voice command', heard=text(record.get('text'), 800), plan=text(record.get('plan')),
                   message=text(result.get('message')) or text(record.get('prompt')) or text(record.get('reason')),
                   route=text(record.get('route'), 40), activation=text(record.get('activation'), 40))
        if result.get('ok') is False or verdict == 'error': row.update(outcome='Error', issue=True)
        elif result.get('dry_run'): row['outcome'] = 'Dry run'
        elif verdict == 'drop': row.update(outcome='Not run', issue=True)
        elif verdict == 'cancelled': row['outcome'] = 'Cancelled'
        elif verdict in {'confirm','clarify'}:
            row.update(outcome='Confirmation requested' if verdict=='confirm' else 'More detail requested', issue=True)
        elif result.get('ok') is True: row['outcome'] = 'Completed'
        else: row.update(outcome='No result recorded', issue=True)
        timings = record.get('ms') if isinstance(record.get('ms'), dict) else {}
        row['ms'] = {k: v for k, v in timings.items() if k in {'whisper', 'jev', 'decision_and_action',
                     'release_to_result', 'end_of_speech_to_result'} and number(v)}
    return row


def recent(directory=None):
    root = Path(directory) if directory is not None else SESSIONS
    rows = []
    paths = []
    for path in root.glob('*.jsonl'):
        try: paths.append((path.stat().st_mtime, path))
        except OSError: continue
    paths = [path for _, path in sorted(paths, reverse=True)[:MAX_FILES]]
    for path in paths:
        try:
            with path.open('rb') as stream:
                size = stream.seek(0, 2)
                start = max(0, size-MAX_BYTES)
                stream.seek(start)
                if start: stream.readline()  # Skip a cut-off first record.
                lines = stream.read(MAX_BYTES).splitlines(keepends=True)
        except OSError:
            continue
        count = 0
        for line in reversed(lines):
            # The listener might currently be writing the last JSON record.
            if not line.endswith(b'\n'): continue
            try: row = entry(json.loads(line))
            except (ValueError, UnicodeError, TypeError): continue
            if row:
                rows.append(row); count += 1
                if count >= MAX_ENTRIES: break
    return sorted(rows, key=lambda row: row['t'], reverse=True)[:MAX_ENTRIES]
