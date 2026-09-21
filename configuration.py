"""Portable user configuration and private Jev credential storage."""
from copy import deepcopy
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

DEFAULTS = {
    'voice': {'wake_phrase': 'computer', 'wake_enabled': True},
    'recognition': {'end_silence_ms': 360, 'wake_preview_ms': 800},
    'interpretation': {'reject_low_confidence': False, 'minimum_confidence': 60},
    'phrases': {},
    'indicator': {'enabled': True, 'size': 48, 'top': 54, 'dictation_width': 216},
    'desktop': {'focus_by_default': True, 'launch_timeout_seconds': 8, 'close_timeout_seconds': 1.5},
}


def config_dir():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))) / 'jev-voice'


def load_config():
    cfg = deepcopy(DEFAULTS)
    path = Path(os.environ.get('JEV_VOICE_CONFIG', str(config_dir()/'config.toml')))
    if not path.exists() and 'JEV_VOICE_CONFIG' not in os.environ:
        legacy = Path.home()/'.config/omarchy-voice/config.toml'
        if legacy.exists():
            # Only reuse app/workspace mappings; new recording/UI settings have
            # their own defaults instead of inheriting Voicebind's recognizer.
            old = tomllib.loads(legacy.read_text())
            for key in ('apps', 'workspaces', 'folders', 'shortcuts', 'window_classes', 'window_matches'):
                if key in old:
                    cfg[key] = old[key]
    elif path.exists():
        for section, value in tomllib.loads(path.read_text()).items():
            if isinstance(value, dict) and isinstance(cfg.get(section), dict):
                cfg[section].update(value)
            else:
                cfg[section] = value
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    from personalization import validate_phrases
    validate_phrases(cfg.get('phrases', {}))
    wake = cfg['voice']['wake_phrase']
    if not isinstance(wake, str) or not re.fullmatch(r'[a-zA-Z]+(?: [a-zA-Z]+){0,2}', wake):
        raise ValueError('wake_phrase must contain one to three words')
    for section, key, low, high in [('recognition','end_silence_ms',200,1500),
                                  ('recognition','wake_preview_ms',600,1600),
                                  ('indicator','size',28,96), ('indicator','top',0,240),
                                  ('indicator','dictation_width',120,480)]:
        v = cfg[section][key]
        if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
            raise ValueError(f'{section}.{key} must be an integer from {low} to {high}')
    interpretation = cfg.get('interpretation', DEFAULTS['interpretation'])
    if not isinstance(interpretation, dict):
        raise ValueError('interpretation must be a settings table')
    if not isinstance(interpretation.get('reject_low_confidence', False), bool):
        raise ValueError('interpretation.reject_low_confidence must be true or false')
    threshold = interpretation.get('minimum_confidence', 60)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise ValueError('interpretation.minimum_confidence must be an integer from 0 to 100')
    source = cfg['recognition'].get('source', '')
    if not isinstance(source, str) or len(source) > 256 or any(ord(c) < 32 for c in source):
        raise ValueError('Choose a valid microphone source')
    for section,key in [('voice','wake_enabled'),('indicator','enabled'),('desktop','focus_by_default')]:
        if not isinstance(cfg[section][key], bool):
            raise ValueError(f'{section}.{key} must be true or false')
    return cfg


def config_path():
    return Path(os.environ.get('JEV_VOICE_CONFIG', str(config_dir()/'config.toml')))


def toml_value(value):
    if isinstance(value, bool): return str(value).lower()
    if isinstance(value, (int, float)): return repr(value)
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list): return '[' + ', '.join(toml_value(v) for v in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + toml_value(v) for k,v in value.items()) + '}'
    raise ValueError('Unsupported configuration value')


def save_config(cfg, expected=None):
    validate_config(cfg)
    path = config_path()
    current = path.read_bytes() if path.exists() else None
    if expected is not None and current != expected[0]:
        raise ValueError('Settings changed elsewhere. Reopen settings before saving.')
    lines = ['# Voicebind — editable here or in Voicebind Settings.']
    for section, values in cfg.items():
        if not isinstance(values, dict):
            raise ValueError('Configuration sections must be tables')
        lines.extend(['', '['+json.dumps(section)+']'])
        lines.extend(json.dumps(k)+' = '+toml_value(v) for k,v in values.items())
    rendered = '\n'.join(lines)+'\n'
    # Verify the serializer before replacing a working configuration.
    if tomllib.loads(rendered) != cfg: raise ValueError('Configuration could not be encoded')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.config-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(rendered)
            stream.flush(); os.fsync(stream.fileno())
        if current is not None:
            backup = path.with_name(path.name+'.previous')
            backup.write_bytes(current); backup.chmod(0o600)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def load_key():
    key = os.environ.get('TYPESAFE_API_KEY') or os.environ.get('JEV_API_KEY')
    if key:
        return key
    private = config_dir()/'api-key'
    if private.exists():
        return private.read_text().strip() or None
    legacy = Path.home()/'.config/typesafe/env'
    if legacy.exists():
        match = re.search(r'^\s*(?:export\s+)?TYPESAFE_API_KEY=(.+)$', legacy.read_text(), re.M)
        if match:
            return match[1].strip().strip('\'"')
    return None


def save_key(value):
    if not value or len(value) > 4096 or any(c.isspace() for c in value):
        raise ValueError('Enter a non-empty API key without whitespace')
    root = config_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix='.api-key-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value + '\n')
        os.replace(temp, root/'api-key')
    finally:
        Path(temp).unlink(missing_ok=True)


if __name__ == '__main__':
    if sys.argv[1:] in (['key'], ['key','--stdin']):
        value = sys.stdin.readline().strip() if '--stdin' in sys.argv else getpass.getpass('Jev API key (hidden): ').strip()
        save_key(value)
        print('API key saved privately. Restart Voicebind to use it.')
    elif sys.argv[1:] == ['show']:
        print(json.dumps({'config':load_config(), 'api_key_configured':bool(load_key())}, indent=2))
    else:
        sys.exit('Usage: configuration.py key [--stdin] | show')
