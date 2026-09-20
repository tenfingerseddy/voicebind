"""Explicit whole-utterance aliases shared by commands and dictation starts."""
import re

CONTROLS = {'yes', 'do it', 'confirm', 'go ahead', 'yes do it', 'no', 'cancel',
            'never mind', 'nevermind', 'stop', 'finish', 'end', 'discard'}
SETTINGS_PHRASES = {'voice settings', 'open voice settings', 'jev voice settings',
                    'open jev voice settings', 'configure voice', 'customize voice',
                    'customise voice', 'jev settings', 'open jev settings', 'open jab settings',
                    'voicebind settings', 'open voicebind settings', 'voice bind settings',
                    'open voice bind settings', 'configure voicebind'}


def phrase_key(text):
    return re.sub(r'\s+', ' ', text.casefold().strip().strip('.!?,')).strip()


def validate_phrases(phrases):
    if not isinstance(phrases, dict) or len(phrases) > 100:
        raise ValueError('Use at most 100 custom phrases')
    seen = set()
    for spoken, command in phrases.items():
        key = phrase_key(spoken)
        if not key or len(key) > 120 or key in seen:
            raise ValueError('Custom phrases must be unique and under 120 characters')
        if key in CONTROLS | SETTINGS_PHRASES or re.match(r'^(?:(?:save|update|replace|restore|load|delete|remove|forget|list|show|open|activate|switch to)\b.*\bbookmarks?\b|bookmarks?\b)', key):
            raise ValueError(f'“{spoken}” is reserved; choose another custom phrase')
        if not isinstance(command, str) or not command.strip() or len(command) > 400:
            raise ValueError('Give each phrase a command of at most 400 characters')
        if phrase_key(command) in CONTROLS:
            raise ValueError('Custom phrases cannot confirm or cancel pending commands')
        if any(ord(c) < 32 for c in spoken + command):
            raise ValueError('Phrases must be single lines')
        seen.add(key)
    if any(phrase_key(v) in seen for v in phrases.values()):
        raise ValueError('Map phrases directly to commands, not to other aliases')


def expand_phrase(text, config):
    phrases = config.get('phrases', {}) if isinstance(config, dict) else {}
    key = phrase_key(text)
    # Exactly one substitution. Never rewrite prose or fuzzy-match a wake word.
    return next((v for k, v in phrases.items() if phrase_key(k) == key), text)
