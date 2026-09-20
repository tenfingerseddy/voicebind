"""Fixed voice panel roles resolved against enabled Omarchy plugins locally."""
import json
import re
import subprocess

ROLES = {
    'notifications': ('notifications', 'jankeesvw.notification-center', ('notifications', 'notification center', 'notification centre', 'notification history', 'alerts')),
    'usage': ('AI usage', 'omarchy.agents', ('ai usage', 'usage', 'model usage', 'token usage', 'agents')),
    'notes': ('notes', 'io.github.agata.omanano', ('notes', 'note', 'notepad', 'sticky notes')),
    'controls': ('control center', 'davedes.omcontrol', ('control center', 'control centre', 'quick settings', 'controls')),
    'audio': ('audio controls', 'omarchy.audio', ('audio controls', 'sound settings', 'volume controls', 'audio settings')),
    'network': ('network settings', 'omarchy.network', ('network settings', 'wifi', 'wi fi', 'wi-fi', 'wifi settings')),
    'bluetooth': ('Bluetooth', 'omarchy.bluetooth', ('bluetooth', 'bluetooth settings')),
    'power': ('power controls', 'omarchy.power', ('power menu', 'power settings', 'power controls')),
    'clock': ('calendar', 'omarchy.clock', ('calendar', 'clock')),
    'weather': ('weather', 'omarchy.weather', ('weather', 'weather forecast')),
    'shelf': ('file shelf', 'io.github.thisisgm.flea-shelf', ('file shelf', 'flea shelf', 'shelf')),
}
PANELS = {f'panel.{role}.{verb}': f'{verb.capitalize()} the {data[0]} panel'
          for role, data in ROLES.items() for verb in ('show', 'hide')}


def parse_panel(text):
    match = re.fullmatch(r'(open|show|launch|hide|close|dismiss) (?:the |my )?(.+?)(?: panel| popup| extension)?', text)
    if match:
        for role, (_, _, aliases) in ROLES.items():
            if match[2] in aliases:
                return f'panel.{role}.{"hide" if match[1] in {"hide", "close", "dismiss"} else "show"}'
    return None


def run(*args):
    p = subprocess.run(['omarchy', 'shell', 'shell', *args], capture_output=True, text=True, timeout=4)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or 'The desktop bar is unavailable')
    return p.stdout.strip()


def resolve_panel(action, plugins=None):
    _, role, _ = action.split('.')
    title, plugin_id, _ = ROLES[role]
    plugins = json.loads(run('listPlugins')) if plugins is None else plugins
    matches = [p for p in plugins if p.get('enabled') and
               (p['id'] == plugin_id or p.get('clonedFrom') == plugin_id) and
               set(p.get('kinds', ())) & {'bar-widget', 'panel', 'overlay', 'menu'}]
    if len(matches) != 1:
        raise ValueError(f'The {title} panel is unavailable or ambiguous')
    return matches[0]['id']


def execute_panel(action, plugin_id):
    showing = action.endswith('.show')
    response = run('summon', plugin_id, '{}') if showing else run('hide', plugin_id)
    if showing and response != 'ok':
        raise RuntimeError('The bar did not open that panel')
    # summon reports acceptance; shell IPC does not expose rendered/hidden state.
    return {'ok': True, 'message': PANELS[action], 'verified': False, 'plugin': plugin_id}
