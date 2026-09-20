"""Local, private desktop bookmarks. No generated shell or application contents."""
from __future__ import annotations
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from configuration import config_dir
from desktop_core.desktop import capture_window, launch_identifier, selector
from personalization import CONTROLS, SETTINGS_PHRASES, phrase_key

ACTIONS = {
    'bookmark.save': 'Save workspaces and windows under a spoken name',
    'bookmark.restore': 'Restore a saved desktop bookmark by name or custom phrase',
    'bookmark.list': 'List saved desktop bookmarks',
    'bookmark.delete': 'Delete a saved bookmark after confirmation',
    'voice.settings': 'Open Voicebind Settings',
}
SETTINGS_CLASS = 'org.omarchy.JevVoiceSettings'


def name_key(name):
    name = phrase_key(name)
    if not re.fullmatch(r"[\w][\w '’-]{0,69}", name) or len(name.split()) > 10:
        raise ValueError('Use a bookmark name of 1–10 words, up to 70 characters')
    if name in CONTROLS | SETTINGS_PHRASES or re.match(
            r'^(?:computer|hey|bookmark|bookmarks|open|close|move|save|delete|restore|'
            r'dictate|dictation|dictating|start|stop|finish|cancel|yes|no|please|'
            r'can|could|would|switch|go|launch|show|set|turn|make|exit|'
            r'lock|shutdown|reboot|restart|mute|unmute|volume)\b', name):
        raise ValueError('Choose a distinct name, such as “work mode” or “writing time”')
    return name


def explicit_request(text):
    text = phrase_key(text)
    text = re.sub(r'^(?:(?:can|could|would) you (?:please )?|please )', '', text)
    text = re.sub(r',? please$', '', text)
    if text in {'list bookmarks', 'show bookmarks', 'my bookmarks', 'what are my bookmarks', 'bookmarks'}:
        return 'list', ''
    match = re.fullmatch(r'(?:save|update|replace)(?: this| the desktop)?(?: as)?(?: a)? bookmark(?: called| named| as)? (.+)', text)
    if not match: match = re.fullmatch(r'bookmark (?:this|the desktop)(?: as| called| named) (.+)', text)
    if match: return 'save', name_key(match[1])
    match = re.fullmatch(r'(?:delete|remove|forget) bookmark (.+)', text)
    if match: return 'delete', match[1]
    match = re.fullmatch(r'(?:(?:restore|load|open|activate|switch to)(?: the)? bookmark|bookmark) (.+)', text)
    if match: return 'restore', match[1]
    return None


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config_dir()/'bookmarks.json'

    def read(self):
        if not self.path.exists(): return {}
        data = json.loads(self.path.read_text())
        if data.get('version') != 1 or not isinstance(data.get('bookmarks'), dict):
            raise ValueError('Unsupported bookmarks file; it has not been changed')
        validate_entries(data['bookmarks'])
        return data['bookmarks']

    @staticmethod
    def revision(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(self.path)+'.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def write(self, name, value, expected):
        with self.locked():
            entries = self.read()
            if self.revision(entries.get(name)) != expected:
                raise ValueError('This bookmark changed while you were reviewing it; try again')
            if value is None: entries.pop(name, None)
            else:
                for other, entry in entries.items():
                    if other != name and set([name, *value.get('phrases', [])]) & set([other, *entry.get('phrases', [])]):
                        raise ValueError('That phrase already belongs to another bookmark')
                entries[name] = value
            validate_entries(entries)
            fd, temp = tempfile.mkstemp(prefix='.bookmarks-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'version':1, 'bookmarks':entries}, stream, ensure_ascii=False, indent=2)
                    stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, self.path)
            finally: Path(temp).unlink(missing_ok=True)

    def resolve(self, text):
        key = phrase_key(text)
        for name, item in self.read().items():
            if key in [name, *item.get('phrases', [])]: return name, item
        raise ValueError(f'No bookmark named “{text}”. Say “list bookmarks” or save one first.')

    def aliases(self, name, phrases, config):
        name, item = self.resolve(name)
        expected = self.revision(item)
        values = list(dict.fromkeys(name_key(p) for p in phrases if p.strip()))
        if len(values) > 12: raise ValueError('Use at most 12 phrases per bookmark')
        custom = {phrase_key(p) for p in config.get('phrases', {})}
        if custom & {name, *values}: raise ValueError('That phrase is already a custom command')
        item['phrases'] = values
        self.write(name, item, expected)


def validate_entries(entries):
    if len(entries)>100: raise ValueError('Use at most 100 bookmarks')
    all_phrases=set()
    def integer(value, low, high): return type(value) is int and low<=value<=high
    for name,entry in entries.items():
        if name_key(name)!=name: raise ValueError('Invalid bookmark name')
        phrases=[name,*entry.get('phrases',[])]
        if any(name_key(p)!=p or p in all_phrases for p in phrases): raise ValueError('Duplicate or invalid bookmark phrase')
        all_phrases.update(phrases)
        windows=entry.get('windows')
        if not isinstance(windows,list) or not 1<=len(windows)<=200: raise ValueError('A bookmark needs 1–200 windows')
        if not isinstance(entry.get('session'),str): raise ValueError('Invalid bookmark session')
        addresses=set()
        for w in windows:
            selector(w.get('address',''))
            if w['address'] in addresses: raise ValueError('Duplicate bookmarked window')
            addresses.add(w['address'])
            if not integer(w.get('workspace'),1,20): raise ValueError('Invalid bookmarked workspace')
            if not integer(w.get('fullscreen'),0,3) or not integer(w.get('fullscreen_client'),0,3): raise ValueError('Invalid fullscreen state')
            if type(w.get('floating')) is not bool: raise ValueError('Invalid floating state')
            if w.get('stable_id') is not None and not (integer(w['stable_id'],0,2**64) or isinstance(w['stable_id'],str) and re.fullmatch(r'[0-9a-fA-F]{1,32}',w['stable_id'])): raise ValueError('Invalid stable window identity')
            for k in ('class','title','monitor'):
                if not isinstance(w.get(k),str) or len(w[k])>4096: raise ValueError('Invalid window metadata')
            if w.get('app') is not None and (not isinstance(w['app'],str) or not w['app'].endswith('.desktop')): raise ValueError('Invalid app identity')
            rect=w.get('rect')
            if not isinstance(rect,list) or len(rect)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>100 for v in rect) or any(v<=0 for v in rect[2:]): raise ValueError('Invalid window geometry')
        if entry.get('active') not in addresses or not integer(entry.get('workspace'),1,20): raise ValueError('Invalid bookmark focus')
        if any(not isinstance(m,str) or not integer(ws,1,20) for m,ws in entry.get('visible',{}).items()): raise ValueError('Invalid visible workspace')


def monitor_box(monitor):
    width, height = monitor['width']/monitor['scale'], monitor['height']/monitor['scale']
    if monitor.get('transform', 0) % 2: width, height = height, width
    left, top, right, bottom = monitor.get('reserved', [0,0,0,0])
    return [monitor['x']+left, monitor['y']+top, width-left-right, height-top-bottom]


def eligible(client):
    return (client.get('mapped', True) and not client.get('hidden', False)
            and 1 <= client.get('workspace', {}).get('id', 0) <= 20
            and client.get('class') != SETTINGS_CLASS and not client.get('pinned')
            and not client.get('grouped') and client.get('swallowing') in (None,0,'0x0','0',''))


@dataclass
class BookmarkPlan:
    action: str
    name: str = ''
    value: dict | None = None
    expected: str = ''
    targets: list | None = None
    confirm: bool = False

    @property
    def label(self):
        if self.action == 'list': return 'List bookmarks'
        return f'{self.action.capitalize()} bookmark “{self.name}”'


class Bookmarks:
    def __init__(self, desktop, store=None):
        self.desktop = desktop
        self.hypr = desktop.hypr
        self.store = store or Store()

    def session(self):
        return str(self.hypr.directory)

    def guard(self, valid):
        if not valid(): raise RuntimeError('Bookmark restore cancelled')
        if self.hypr.request('locked').strip().casefold() != 'false':
            raise RuntimeError('Unlock the desktop before using bookmarks')
        if not any(m.get('dpmsStatus', True) for m in self.hypr.query('monitors')):
            raise RuntimeError('Wake the display before using bookmarks')

    def identify(self, client):
        matches = [app for app in self.desktop.catalog.apps.values() if self.desktop.app_matches(app, client)]
        exact = [app for app in matches if app.id.removesuffix('.desktop').casefold() == client['class'].casefold()]
        candidates = exact or matches
        return candidates[0].id if len(candidates) == 1 else None

    def capture(self):
        self.guard(lambda:True)
        monitors = self.hypr.query('monitors')
        by_id = {m['id']:m for m in monitors}
        clients = self.hypr.query('clients')
        windows = []
        for c in clients:
            if not eligible(c): continue
            monitor = by_id[c['monitor']]
            x,y,w,h = monitor_box(monitor)
            windows.append({
                'address':c['address'], 'stable_id':c.get('stableId'), 'app':self.identify(c),
                'class':c['class'], 'title':c.get('title', ''), 'workspace':c['workspace']['id'],
                'monitor':monitor['name'], 'floating':bool(c.get('floating')),
                'fullscreen':c.get('fullscreen', 0), 'fullscreen_client':c.get('fullscreenClient', 0),
                'rect':[(c['at'][0]-x)/w, (c['at'][1]-y)/h, c['size'][0]/w, c['size'][1]/h],
            })
        if not windows: raise ValueError('There are no normal windows to bookmark')
        active = self.hypr.query('activewindow').get('address')
        if active not in {c['address'] for c in windows}:
            recent = sorted([c for c in clients if eligible(c)], key=lambda c:c.get('focusHistoryID',999))
            active = recent[0]['address'] if recent else None
        return {'saved_at':time.time(), 'session':self.session(), 'windows':windows,
                'active':active, 'workspace':next((c['workspace'] for c in windows if c['address']==active), windows[0]['workspace']),
                'visible':{m['name']:m['activeWorkspace']['id'] for m in monitors if 1 <= m['activeWorkspace']['id'] <= 20},
                'phrases':[], 'excluded':len(clients)-len(windows)}

    def prepare(self, text, config=None):
        text = phrase_key(text)
        request = explicit_request(text)
        if request and request[0] == 'list': return BookmarkPlan('list')
        if request and request[0] == 'save':
            name = request[1]
            if name in {phrase_key(p) for p in (config or {}).get('phrases', {})}:
                raise ValueError('That name already belongs to a custom phrase')
            old = self.store.read().get(name)
            value = self.capture()
            if old: value['phrases'] = old.get('phrases', [])
            return BookmarkPlan('save', name, value, self.store.revision(old), confirm=old is not None)
        if request and request[0] == 'delete':
            name, value = self.store.resolve(request[1])
            return BookmarkPlan('delete', name, expected=self.store.revision(value), confirm=True)
        if request and request[0] == 'restore':
            name, value = self.store.resolve(request[1])
        else:
            try: name, value = self.store.resolve(text)
            except ValueError:
                if re.search(r'\bbookmarks?\b', text):
                    raise ValueError('Say “save bookmark work mode”, “restore bookmark work mode” or “list bookmarks”')
                return None
        return BookmarkPlan('restore', name, value, targets=self.match(value))

    def match(self, value):
        self.guard(lambda:True)
        clients = [c for c in self.hypr.query('clients') if eligible(c)]
        same_session = value['session']==self.session()
        used = set(); targets = [None]*len(value['windows'])
        # First claim immutable identities from this compositor session.
        for i, saved in enumerate(value['windows']):
            match = next((c for c in clients if c['address']==saved['address']
                          and c.get('stableId') == saved['stable_id'] and saved['stable_id'] is not None
                          and c['class']==saved['class']), None) if same_session else None
            if match:
                targets[i] = capture_window(match); used.add(match['address'])
        # Then exact titles. A title is metadata only: never a shell argument or URL.
        for i, saved in enumerate(value['windows']):
            if targets[i] is not None: continue
            candidates = [c for c in clients if not same_session and c['address'] not in used and c['class']==saved['class']]
            exact = [c for c in candidates if c.get('title') == saved['title']]
            if len(exact)==1:
                targets[i] = capture_window(exact[0]); used.add(exact[0]['address'])
        for i, saved in enumerate(value['windows']):
            if targets[i] is not None: continue
            candidates = [c for c in clients if not same_session and c['address'] not in used and c['class']==saved['class']]
            pending = sum(targets[j] is None and s['class']==saved['class'] for j,s in enumerate(value['windows']))
            if len(candidates)==1 and pending==1:
                targets[i] = capture_window(candidates[0]); used.add(candidates[0]['address'])
            elif candidates:
                raise ValueError(f'Multiple {saved["class"]} windows are ambiguous. Keep their original titles or save this bookmark again.')
            else:
                app = self.desktop.catalog.apps.get(saved['app'])
                if app is None: raise ValueError(f'Open {saved["class"]} first; its installed launcher could not be identified')
                # New windows are only requested through advertised desktop actions.
                launch_identifier(app, any(c['class']==saved['class'] for c in clients) or
                                  sum(s['class']==saved['class'] for s in value['windows'][:i]) > 0)
        return targets

    def current(self, target):
        client = next((c for c in self.hypr.query('clients') if capture_window(c)==target and eligible(c)), None)
        if client is None: raise RuntimeError('A bookmarked window closed or changed identity; restore again')
        return client

    def execute(self, plan, valid=lambda:True):
        if plan.action == 'list':
            names = list(self.store.read())
            return {'ok':True, 'message':'Bookmarks: '+', '.join(names) if names else 'No bookmarks yet. Say “save bookmark work mode”.'}
        self.guard(valid)
        if plan.action in {'save','delete'}:
            self.store.write(plan.name, plan.value if plan.action=='save' else None, plan.expected)
            message = f'Saved bookmark “{plan.name}” — {len(plan.value["windows"])} windows. Say its name to restore.' if plan.action=='save' else f'Deleted bookmark “{plan.name}”'
            if plan.value and plan.value.get('excluded'): message += ' Special, pinned, grouped and settings windows are excluded.'
            return {'ok':True, 'message':message}
        completed = 0; opened = 0; warnings = []
        resolved = list(plan.targets)
        geometry = {}
        try:
            # Revalidate all captured targets before the first mutation.
            for target in resolved:
                if target is not None: self.current(target)
            monitors = {m['name']:m for m in self.hypr.query('monitors')}
            for i, saved in enumerate(plan.value['windows']):
                self.guard(valid)
                if resolved[i] is None:
                    app = self.desktop.catalog.apps[saved['app']]
                    before = self.hypr.query('clients')
                    new = any(self.desktop.app_matches(app,c) for c in before)
                    current = self.desktop.launch(app, saved['workspace'], False, new, before, valid=lambda:self.guard(valid) is None)
                    resolved[i] = capture_window(current); opened += 1
                current = self.current(resolved[i])
                # Move only this stable window; unrelated windows are never closed.
                self.desktop.place(current, saved['workspace'], False)
            # Restore workspace-to-monitor assignments after all workspaces exist.
            for ws, monitor in dict((s['workspace'],s['monitor']) for s in plan.value['windows']).items():
                self.guard(valid)
                if monitor not in monitors:
                    warnings.append(f'{monitor} is disconnected; used an available display')
                    continue
                current_ws = next((w for w in self.hypr.query('workspaces') if w['id']==ws), None)
                if current_ws and current_ws.get('monitor') != monitor:
                    self.hypr.dispatch('hl.dsp.workspace.move({ workspace = '+json.dumps(str(ws))+', monitor = '+json.dumps(monitor)+' })')
            for saved, target in zip(plan.value['windows'], resolved):
                self.guard(valid); current = self.current(target)
                want = (saved['fullscreen'],saved['fullscreen_client'])
                state = (current.get('fullscreen',0),current.get('fullscreenClient',0))
                if state != (0,0) and (state != want or saved['floating'] != current['floating']):
                    current = self.desktop.layout(current, 'restore')
                if current['floating'] != saved['floating']:
                    current = self.desktop.layout(current, 'float' if saved['floating'] else 'tile')
                if saved['floating'] and want==(0,0):
                    monitor = next(m for m in self.hypr.query('monitors') if m['id']==current['monitor'])
                    x,y,w,h = monitor_box(monitor); rx,ry,rw,rh = saved['rect']
                    width, height = max(100,min(round(rw*w),round(w))),max(80,min(round(rh*h),round(h)))
                    px, py = round(x+max(0,min(rx*w,w-width))),round(y+max(0,min(ry*h,h-height)))
                    geometry[target] = ([px,py],[width,height])
                    self.hypr.dispatch(f'hl.dsp.window.resize({{ x = {width}, y = {height}, relative = false, window = '+selector(current['address'])+' })')
                    self.guard(valid); self.current(target)
                    self.hypr.dispatch(f'hl.dsp.window.move({{ x = {px}, y = {py}, relative = false, window = '+selector(current['address'])+' })')
                self.guard(valid); current = self.current(target)
                if (current.get('fullscreen',0),current.get('fullscreenClient',0)) != want:
                    self.hypr.dispatch(f'hl.dsp.window.fullscreen_state({{ internal = {want[0]}, client = {want[1]}, action = "set", window = '+selector(current['address'])+' })')
                completed += 1
            for monitor, ws in plan.value.get('visible', {}).items():
                self.guard(valid)
                current_ws = next((w for w in self.hypr.query('workspaces') if w['id']==ws), None)
                if monitor in monitors and current_ws and current_ws.get('monitor')==monitor:
                    self.hypr.dispatch('hl.dsp.focus({ workspace = '+json.dumps(str(ws))+' })')
            self.guard(valid)
            index = next((i for i,s in enumerate(plan.value['windows']) if s['address']==plan.value['active']),0)
            active = self.current(resolved[index])
            self.desktop.place(active, plan.value['workspace'], True)
            for saved,target in zip(plan.value['windows'],resolved):
                c = self.current(target)
                if (c['workspace']['id'] != saved['workspace'] or c['floating'] != saved['floating'] or
                    c.get('fullscreen',0) != saved['fullscreen'] or c.get('fullscreenClient',0) != saved['fullscreen_client']):
                    raise RuntimeError('A window did not keep its saved state')
            for target,(at,size) in geometry.items():
                deadline = time.monotonic()+.75
                while True:
                    c = self.current(target)
                    if all(abs(a-b)<=3 for actual,expected in ((c['at'],at),(c['size'],size)) for a,b in zip(actual,expected)): break
                    if time.monotonic()>=deadline: raise RuntimeError('An app or window rule prevented its saved floating size or position')
                    self.guard(valid); time.sleep(.02)
            message = f'Restored “{plan.name}” — {completed} windows'
            if opened: message += f', reopened {opened}'
            if warnings: message += '. '+'. '.join(dict.fromkeys(warnings))
            return {'ok':True, 'message':message, 'restored':completed, 'opened':opened, 'warnings':list(dict.fromkeys(warnings))}
        except Exception as error:
            return {'ok':False, 'message':f'Bookmark stopped after {completed} windows: {error}', 'restored':completed,'opened':opened}
