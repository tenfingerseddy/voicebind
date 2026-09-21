"""On-demand bar settings: one JSON request on stdin, no resident process."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from configuration import config_dir, config_path, load_config, load_key, save_config, save_key
from control import request
from runtime_paths import PLUGIN_ID


def revision(raw):
    return hashlib.sha256(raw if raw is not None else b'absent').hexdigest()


def raw_config():
    path = config_path()
    return path.read_bytes() if path.exists() else None


def service(*args):
    return subprocess.run(['systemctl', '--user', *args, 'jev-voice.service'],
                          capture_output=True, text=True, timeout=15)


def idle():
    try:
        state = request('status')
        if state.get('phase') not in {'idle', 'error'} or state.get('dictation'):
            raise ValueError('Finish or cancel the current voice request before applying settings.')
    except (OSError, RuntimeError):
        if service('is-active', '--quiet').returncode == 0:
            raise ValueError('The listener is starting; try again shortly.')


def snapshot():
    from bookmarks import Store
    from desktop_core.apps import AppCatalog
    from language import add_role_aliases
    cfg = load_config()
    catalog = AppCatalog(alias_overrides=cfg.get('apps', {}))
    add_role_aliases(catalog)
    roles = {k: catalog.resolve(k).id for k in ('browser', 'files', 'terminal', 'code')
             if len(catalog.aliases.get(k, ())) == 1}
    microphones = [{'name': '', 'description': 'System default microphone'}]
    try:
        result = subprocess.run(['pactl', '-f', 'json', 'list', 'sources'], capture_output=True, text=True, timeout=2)
        microphones += [{'name': s['name'], 'description': s.get('description', s['name'])}
                        for s in json.loads(result.stdout) if not s['name'].endswith('.monitor')]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {'config': cfg, 'revision': revision(raw_config()), 'key_configured': bool(load_key()),
            'apps': [{'id': a.id, 'name': a.name} for a in sorted(catalog.apps.values(), key=lambda a: a.name.casefold())],
            'roles': roles, 'microphones': microphones,
            'bookmarks': [{'name': n, 'phrases': b.get('phrases', []), 'windows': len(b['windows']),
                           'revision': Store.revision(b)} for n, b in Store().read().items()]}


def validate_settings(cfg):
    from bookmarks import Store
    from personalization import phrase_key
    from settings_validate import validate
    validate(cfg)
    names = {p for n, b in Store().read().items() for p in [n, *b.get('phrases', [])]}
    if names & {phrase_key(p) for p in cfg.get('phrases', {})}:
        raise ValueError('A custom phrase already belongs to a bookmark.')


def save(data):
    idle()
    current = load_config()
    cfg = deepcopy(current)
    values = data['config']
    # Preserve advanced per-machine mappings that the panel does not edit.
    for section in ('voice', 'recognition', 'interpretation', 'indicator', 'desktop'):
        if section in values:
            if not isinstance(values[section], dict): raise ValueError('Invalid settings section')
            for key in current[section]:
                if key in values[section]: cfg[section][key] = values[section][key]
            if section == 'recognition': cfg[section]['source'] = values[section].get('source', '')
    for section in ('apps', 'phrases'):
        if section in values: cfg[section] = values[section]
    validate_settings(cfg)
    key = data.get('api_key', '')
    if not isinstance(key, str) or len(key) > 4096 or any(c.isspace() for c in key):
        raise ValueError('Enter an API key without whitespace.')
    config_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(config_dir()/'settings.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = raw_config()
        if data.get('revision') != revision(raw):
            raise ValueError('Settings changed elsewhere. Reopen this panel before saving.')
        save_config(cfg, expected=[raw])
        if key: save_key(key)
    running = service('is-active', '--quiet').returncode == 0
    if running:
        result = service('try-restart')
        if result.returncode: raise RuntimeError('Settings saved, but the listener could not restart.')
    return 'Saved and applied.' if running else 'Saved. Start the listener to use these settings.'


def handle(data):
    action = data.get('action')
    if action == 'panel': return {'ok': True, 'document': snapshot()}
    if action == 'history':
        from history import recent
        return {'ok': True, 'history': recent()}
    if action == 'save': message = save(data)
    elif action in {'start', 'stop'}:
        if action == 'stop': idle()
        result = service('start' if action == 'start' else 'stop')
        if result.returncode: raise RuntimeError('Could not '+action+' Voicebind.')
        message = 'Listener started.' if action == 'start' else 'Listener stopped.'
    elif action in {'bookmark.save', 'bookmark.restore', 'bookmark.delete', 'bookmark.phrases'}:
        from bookmark_cli import manager
        from bookmarks import Store
        idle()
        manager_, cfg = manager()
        name = data['name']
        if action == 'bookmark.phrases':
            _, entry = manager_.store.resolve(name)
            if data.get('revision') != Store.revision(entry): raise ValueError('Bookmark changed. Reopen the panel.')
            manager_.store.aliases(name, data['phrases'], cfg)
            message = 'Bookmark phrases saved.'
        else:
            plan = manager_.prepare(action.split('.')[1]+' bookmark '+name, cfg)
            if action in {'bookmark.save', 'bookmark.delete'}:
                if plan.expected != data.get('revision', Store.revision(None)):
                    raise ValueError('That bookmark already exists or changed. Reopen the panel.')
            result = manager_.execute(plan)
            if not result['ok']: raise RuntimeError(result['message'])
            message = result['message']
    else: raise ValueError('Unknown settings action')
    return {'ok': True, 'message': message, 'document': snapshot()}


def open_panel():
    from desktop_integration import environment
    result = subprocess.run(['omarchy', 'shell', 'shell', 'summon', PLUGIN_ID, '{}'],
                            capture_output=True, text=True, timeout=3, env=environment())
    if result.returncode or result.stdout.strip() != 'ok':
        raise RuntimeError('Voicebind bar extension is unavailable. Run voicebind start to install it.')


if __name__ == '__main__':
    if sys.argv[1:] == ['open']:
        try: open_panel()
        except Exception as error: sys.exit(str(error))
    else:
        try:
            line = sys.stdin.readline(131073)
            if len(line) > 131072: raise ValueError('Settings request is too large')
            data = json.loads(line)
            if not isinstance(data, dict): raise ValueError('Invalid settings request')
            result = handle(data)
        except Exception as error:
            result = {'ok': False, 'message': str(error)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
