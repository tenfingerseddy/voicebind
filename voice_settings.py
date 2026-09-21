#!/usr/bin/python
"""On-demand native settings. The listener remains the only microphone owner."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import tomllib

from desktop_integration import environment

# GTK reads its display environment during import/initialization.
if __name__=='__main__': os.environ.update(environment())

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib

from bookmarks import Store, name_key, SETTINGS_CLASS
from configuration import config_path, load_config, load_key, save_config, save_key
from desktop_core.apps import AppCatalog
from desktop_integration import environment
from personalization import phrase_key, validate_phrases

ROOT = Path(__file__).resolve().parent
from runtime_paths import data_home
PYTHON = str(data_home()/'venv/bin/python')


def label(text, style=None):
    item = Gtk.Label(label=text, xalign=0, wrap=True)
    if style: item.add_css_class(style)
    return item


def button(text, callback, style=None):
    item = Gtk.Button(label=text); item.connect('clicked', callback)
    if style: item.add_css_class(style)
    return item


def row(*items):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    for item in items: box.append(item)
    return box


class Settings(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=SETTINGS_CLASS)
        self.cfg = load_config()
        self.expected = [config_path().read_bytes() if config_path().exists() else None]
        self.phrases = []; self.apps = []; self.controls = {}
        self.bookmarks = Store(); self.busy = False
        self.connect('activate', self.activate)

    def activate(self, _):
        if hasattr(self, 'window'):
            self.window.present(); return
        self.window = Gtk.ApplicationWindow(application=self, title='Voicebind Settings', default_width=970, default_height=720)
        self.window.connect('close-request', self.closing)
        self.dirty = False
        header = Gtk.HeaderBar()
        header.set_title_widget(label('Voicebind Settings', 'title-3'))
        self.apply = button('Save and apply', self.save, 'suggested-action'); header.pack_end(self.apply)
        self.window.set_titlebar(header)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.stack.set_hhomogeneous(False); self.stack.set_vhomogeneous(False)
        sidebar = Gtk.StackSidebar(stack=self.stack, width_request=165)
        root.append(row(sidebar, self.stack))
        self.status = label('Changes stay on this computer. No settings account is required.')
        self.status.set_margin_start(18); self.status.set_margin_end(18)
        self.status.set_margin_top(10); self.status.set_margin_bottom(10)
        root.append(self.status); self.window.set_child(root)
        self.voice_page(); self.phrase_page(); self.apps_page(); self.bookmark_page(); self.service_page()
        self.theme_key = None; self.theme()
        GLib.timeout_add_seconds(2, self.theme)
        self.mic_pending=False
        GLib.timeout_add_seconds(1,self.microphone_health)
        self.window.present()

    def page(self, name, title, description):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for edge in ('start','end','top','bottom'): getattr(box, 'set_margin_'+edge)(24)
        box.append(label(title, 'title-1')); box.append(label(description, 'dim-label'))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=box)
        self.stack.add_titled(scroller, name, title)
        return box

    def changed(self, *_): self.dirty = True

    def setting(self, box, section, key, title, note='', limits=None):
        value = self.cfg[section][key]
        if isinstance(value, bool):
            widget = Gtk.Switch(active=value, valign=Gtk.Align.CENTER)
            widget.connect('notify::active', self.changed)
        elif limits:
            widget = Gtk.SpinButton.new_with_range(*limits)
            widget.set_value(value); widget.connect('value-changed', self.changed)
        else:
            widget = Gtk.Entry(text=str(value), width_chars=18)
            widget.connect('changed', self.changed)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        text.append(label(title))
        if note: text.append(label(note, 'dim-label'))
        box.append(row(text,widget)); self.controls[section,key] = widget
        return widget

    def voice_page(self):
        box = self.page('voice', 'Voice & appearance', 'Choose how you speak, when commands end and how listening appears.')
        self.setting(box,'voice','wake_phrase','Wake phrase','One to three words. F10 works without a wake phrase.')
        self.setting(box,'voice','wake_enabled','Listen for the wake phrase')
        self.setting(box,'recognition','end_silence_ms','Silence wait (ms)','Quiet time that ends a wake command. Lower is faster; increase it if commands cut off when you pause. F10 release skips this wait.',(200,1500,20))
        self.setting(box,'recognition','wake_preview_ms','Wake indicator preview','Milliseconds of speech before checking for the wake phrase.',(600,1600,50))
        self.sources = [('', 'Follow the system microphone')]
        try:
            result = subprocess.run(['pactl','-f','json','list','sources'],capture_output=True,text=True,timeout=3,check=True)
            self.sources.extend((s['name'],s.get('description',s['name'])) for s in json.loads(result.stdout) if not s['name'].endswith('.monitor'))
        except (OSError,ValueError,subprocess.SubprocessError): pass
        selected = self.cfg['recognition'].get('source','')
        if selected and selected not in dict(self.sources): self.sources.append((selected,selected+' (unavailable)'))
        self.source = Gtk.DropDown.new_from_strings([s[1] for s in self.sources])
        self.source.set_selected(next(i for i,s in enumerate(self.sources) if s[0]==selected))
        self.source.set_enable_search(True); self.source.connect('notify::selected',self.changed)
        box.append(label('Microphone')); box.append(self.source)
        self.mic_level=Gtk.LevelBar(min_value=0,max_value=1,value=0)
        self.mic_status=label('Checking the listener’s microphone…','dim-label')
        box.append(self.mic_level); box.append(self.mic_status)
        self.setting(box,'indicator','enabled','Show the listening indicator')
        self.setting(box,'indicator','size','Circle size','Pixels',(28,96,2))
        self.setting(box,'indicator','top','Distance from the top','Pixels',(0,240,2))
        self.setting(box,'indicator','dictation_width','Dictation pill width','Pixels',(120,480,8))
        self.setting(box,'desktop','focus_by_default','Follow opened or moved apps')
        box.append(label('The circle, dictation waveform and this window follow your Omarchy theme.', 'dim-label'))

    def microphone_health(self):
        if self.mic_pending or not self.window.get_visible() or self.stack.get_visible_child_name()!='voice':
            return GLib.SOURCE_CONTINUE
        self.mic_pending=True
        def update(state):
            self.mic_pending=False
            fresh=state and time.time()-state.get('last_audio_at',0)<2
            self.mic_level.set_value(min(1,max(0,(20*math.log10(max(1,state.get('audio_rms',0))/32768)+65)/65)) if fresh else 0)
            if not state: message='Listener stopped or unavailable.'
            elif not fresh: message='No fresh microphone audio. Check your microphone selection.'
            elif state.get('overlong'): message='Recalibrating after continuous sound…'
            else: message='Receiving audio · wake listening '+('on' if state.get('wake_enabled') else 'off (F10 still works)')
            self.mic_status.set_text(message)
            return GLib.SOURCE_REMOVE
        def read():
            try:
                from control import request
                state=request('status')
            except (OSError,RuntimeError,ValueError): state={}
            GLib.idle_add(update,state)
        threading.Thread(target=read,daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def phrase_page(self):
        self.phrase_box = self.page('phrases','Custom phrases', 'Give your own wording a meaning. Enter phrases without the wake word. Exact whole phrases are matched locally, including wording the recognizer consistently hears.')
        self.phrase_box.append(label('For example: “bring up my stuff” → “open files”; “writing time” → “dictate”. Commands can contain up to four linked actions.', 'dim-label'))
        self.phrase_box.append(row(button('Add a phrase',lambda _:self.add_phrase()),button('Check phrases',self.check_phrases)))
        for spoken,command in self.cfg.get('phrases',{}).items(): self.add_phrase(spoken,command)
        self.dirty = False

    def check_phrases(self, _):
        try:
            cfg=self.collect()
            def check():
                result=subprocess.run([PYTHON,str(ROOT/'settings_validate.py')],input=json.dumps(cfg),capture_output=True,text=True,timeout=10)
                if result.returncode: raise ValueError(result.stderr.strip() or result.stdout.strip())
                return 'All phrases are supported. Nothing ran; use Save and apply to enable changes.'
            self.run_task(check)
        except Exception as error: self.tell(str(error))

    def add_phrase(self, spoken='', command=''):
        first = Gtk.Entry(text=spoken, placeholder_text='What I say / what was heard', hexpand=True)
        second = Gtk.Entry(text=command, placeholder_text='Command or restore bookmark name', hexpand=True)
        item = row(first, label('→'), second)
        entry = (first, second, item)
        def remove(_): self.phrase_box.remove(item); self.phrases.remove(entry); self.changed()
        item.append(button('Remove',remove)); self.phrase_box.append(item); self.phrases.append(entry)
        first.connect('changed',self.changed); second.connect('changed',self.changed)
        self.changed()

    def apps_page(self):
        self.catalog = AppCatalog(alias_overrides=self.cfg.get('apps',{}))
        self.app_choices = sorted(self.catalog.apps.values(), key=lambda a:(a.name.casefold(),a.id))
        self.app_ids = [a.id for a in self.app_choices]
        self.app_box = self.page('apps','App names', 'Choose which installed app a spoken name means. Roles such as browser and files are detected automatically; add a row to override them or give any app another name.')
        defaults=[]
        for role in ('browser','files','terminal'):
            try: defaults.append(role.capitalize()+': '+self.catalog.resolve(role).name)
            except ValueError: defaults.append(role.capitalize()+': choose an app')
        self.app_box.append(label('Detected defaults · '+' · '.join(defaults), 'dim-label'))
        self.app_box.append(button('Add an app name',lambda _:self.add_app()))
        for name,app in self.cfg.get('apps',{}).items(): self.add_app(name,app)
        self.dirty = False

    def add_app(self, name='', app=''):
        first = Gtk.Entry(text=name, placeholder_text='Spoken name, e.g. browser',width_chars=18)
        values = [a.name[:45]+('…' if len(a.name)>45 else '') for a in self.app_choices]
        ids = list(self.app_ids)
        if app and app not in ids: ids.append(app); values.append(app+' (not installed)')
        second = Gtk.DropDown.new_from_strings(values); second.set_hexpand(True); second.set_enable_search(True)
        if app in ids: second.set_selected(ids.index(app))
        def tooltip(*_):
            if second.get_selected()<len(ids): second.set_tooltip_text(ids[second.get_selected()])
        second.connect('notify::selected',tooltip); tooltip()
        item = row(first,second); entry=(first,second,ids,item)
        def remove(_): self.app_box.remove(item); self.apps.remove(entry); self.changed()
        item.append(button('Remove',remove)); self.app_box.append(item); self.apps.append(entry)
        first.connect('changed',self.changed); second.connect('notify::selected',self.changed); self.changed()

    def bookmark_page(self):
        box = self.page('bookmarks','Bookmarks', 'Arrange your windows, then save the desktop under a phrase such as “work mode”. Say “computer work mode” to return to it. Your configured wake phrase also works.')
        box.append(label('Saves normal workspaces 1–20, monitor assignments, active window, fullscreen and floating positions. Tiled windows use the current layout. App documents and tabs are not captured. Special, grouped and pinned windows are excluded.', 'dim-label'))
        self.bookmark_name = Gtk.Entry(placeholder_text='New bookmark name',hexpand=True)
        box.append(row(self.bookmark_name,button('Save current desktop', self.capture)))
        box.append(button('Refresh bookmarks',lambda _:self.refresh_bookmarks()))
        self.bookmark_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=16)
        box.append(self.bookmark_rows); self.refresh_bookmarks()

    def refresh_bookmarks(self):
        while child := self.bookmark_rows.get_first_child(): self.bookmark_rows.remove(child)
        try: entries = self.bookmarks.read()
        except Exception as error: self.tell(str(error)); return
        if not entries: self.bookmark_rows.append(label('No bookmarks yet.', 'dim-label'))
        for name,entry in entries.items():
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            card.append(label(name, 'title-3'))
            workspaces = len({w['workspace'] for w in entry['windows']})
            card.append(label(f'{len(entry["windows"])} windows · {workspaces} workspaces', 'dim-label'))
            phrase = Gtk.Entry(text=', '.join(entry.get('phrases',[])),placeholder_text='Extra phrases, separated by commas',hexpand=True)
            def save_phrases(_, name=name, field=phrase):
                try:
                    self.bookmarks.aliases(name,field.get_text().split(','),self.collect())
                    self.tell('Bookmark phrases saved. Available immediately.')
                except Exception as error: self.tell(str(error))
            card.append(row(phrase,button('Save phrases',save_phrases)))
            card.append(row(button('Restore',lambda _,n=name:self.restore(n)),
                            button('Update from desktop',lambda _,n=name:self.confirm('Replace “'+n+'” with the current desktop?',lambda:self.capture_name(n,True))),
                            button('Delete',lambda _,n=name:self.confirm('Delete bookmark “'+n+'”?',lambda:self.delete(n)))))
            self.bookmark_rows.append(card)

    def service_page(self):
        box = self.page('service','Service & diagnostics', 'Recognition and custom phrases run locally. Jev handles requests that need language interpretation. Bookmarks work without an API key.')
        self.key = Gtk.PasswordEntry(placeholder_text='Enter a new Jev API key', show_peek_icon=True, hexpand=True)
        self.key.connect('changed',self.changed)
        box.append(label('Jev API key: '+('configured' if load_key() else 'not configured')))
        box.append(self.key)
        box.append(label('Leave the field empty to keep the current key. Keys are stored separately and never shown in diagnostics.', 'dim-label'))
        box.append(button('Refresh recent commands',lambda _:self.refresh_diagnostics()))
        self.diagnostics = label(''); self.diagnostics.set_selectable(True); box.append(self.diagnostics)
        self.refresh_diagnostics()
        box.append(label('Advanced configuration', 'title-3'))
        box.append(label(str(config_path()),'dim-label'))
        box.append(button('Open configuration folder',lambda _:subprocess.Popen(['xdg-open',str(config_path().parent)])))
        box.append(label('Save and apply restarts the listener when it is idle. F10: hold to speak. Shift+F10: toggle wake listening. Ctrl+F10: cancel.', 'dim-label'))

    def refresh_diagnostics(self):
        try:
            runtime = Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))/'jev-voice'
            state = json.loads((runtime/'status.json').read_text())
            path = Path(state['session']); records = []
            # Dictation body is never logged by the listener.
            with path.open('rb') as stream:
                stream.seek(0,2); size = stream.tell(); stream.seek(max(0,size-65536))
                if size>65536: stream.readline()
                for line in stream:
                    try: event=json.loads(line)
                    except ValueError: continue
                    if 'text' in event:
                        outcome = event.get('result',{}).get('message') or event.get('reason') or event.get('verdict','')
                        records.append('Heard: '+event['text']+'\n'+outcome)
            self.diagnostics.set_text(state.get('state','Unknown')+'\n\n'+'\n\n'.join(records[-5:]))
        except (OSError,ValueError,KeyError): self.diagnostics.set_text('No recent listener diagnostics are available.')

    def collect(self):
        cfg = deepcopy(self.cfg)
        for (section,key),widget in self.controls.items():
            if isinstance(widget,Gtk.Switch): value=widget.get_active()
            elif isinstance(widget,Gtk.SpinButton): value=widget.get_value_as_int()
            else: value=widget.get_text().strip()
            cfg[section][key]=value
        cfg['recognition']['source']=self.sources[self.source.get_selected()][0]
        cfg['phrases']={}
        for first,second,_ in self.phrases:
            spoken,command=first.get_text().strip(),second.get_text().strip()
            if not spoken and not command: continue
            if phrase_key(spoken) in {phrase_key(p) for p in cfg['phrases']}: raise ValueError('Each custom phrase must be unique')
            cfg['phrases'][spoken]=command
        validate_phrases(cfg['phrases'])
        reserved={p for n,b in self.bookmarks.read().items() for p in [n,*b.get('phrases',[])]}
        if reserved & {phrase_key(p) for p in cfg['phrases']}: raise ValueError('A custom phrase already belongs to a bookmark')
        cfg['apps']={}
        for first,second,ids,_ in self.apps:
            name=first.get_text().strip().casefold()
            if not name: continue
            if name in cfg['apps']: raise ValueError('Each app name must be unique')
            if second.get_selected()>=len(ids): raise ValueError('Choose an app')
            cfg['apps'][name]=ids[second.get_selected()]
        return cfg

    def idle(self):
        try:
            from control import request
            status=request('status')
            if status.get('phase') not in {'idle','error'} or status.get('dictation'):
                raise ValueError('Finish or cancel the current voice request before applying settings')
        except (OSError,RuntimeError):
            if subprocess.run(['systemctl','--user','is-active','--quiet','jev-voice.service']).returncode==0:
                raise ValueError('The listener is busy or unavailable; try again shortly')

    def save(self, _):
        try:
            cfg=self.collect(); self.idle()
            value=self.key.get_text().strip()
            if value and (len(value)>4096 or any(c.isspace() for c in value)): raise ValueError('Enter an API key without whitespace')
            # Reject aliases that cannot be executed, before changing working settings.
            result=subprocess.run([PYTHON,str(ROOT/'settings_validate.py')],input=json.dumps(cfg),capture_output=True,text=True,timeout=10)
            if result.returncode: raise ValueError(result.stderr.strip() or result.stdout.strip())
            save_config(cfg,self.expected)
            if value: save_key(value); self.key.set_text('')
            self.cfg=cfg; self.expected=[config_path().read_bytes()]; self.dirty=False
            def apply():
                active=subprocess.run(['systemctl','--user','is-active','--quiet','jev-voice.service']).returncode==0
                subprocess.run(['systemctl','--user','try-restart','jev-voice.service'],check=True,capture_output=True,text=True,timeout=15)
                if not active: return 'Settings saved. The listener is stopped; start it with voicebind start.'
                return 'Settings saved and applied. Say “'+cfg['voice']['wake_phrase']+' voice settings” to return.'
            self.run_task(apply)
        except Exception as error: self.tell(str(error))

    def tell(self, message): self.status.set_text(str(message))

    def confirm(self, text, action):
        dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,text=text,buttons=Gtk.ButtonsType.OK_CANCEL)
        def response(d, result):
            d.destroy()
            if result==Gtk.ResponseType.OK: action()
        dialog.connect('response',response); dialog.present()

    def closing(self, _):
        if self.busy: self.tell('Wait for the current operation to finish.'); return True
        if self.dirty:
            self.confirm('Discard unsaved voice settings?',lambda:(setattr(self,'dirty',False),self.window.close()))
            return True
        return False

    def run_task(self, task, hide=False, leave_hidden=False):
        if self.busy: self.tell('An operation is already running.'); return
        self.busy=True; self.apply.set_sensitive(False); self.tell('Working…')
        self.hold()
        if hide: self.window.set_visible(False)
        def finish(message, ok):
            self.busy=False; self.apply.set_sensitive(True); self.tell(message)
            self.refresh_bookmarks()
            if hide and not (leave_hidden and ok): self.window.present()
            if leave_hidden and ok:
                self.quit()
            self.release()
            return GLib.SOURCE_REMOVE
        def worker():
            try:
                if hide: time.sleep(.35)
                message=task(); GLib.idle_add(finish,message,True)
            except Exception as error: GLib.idle_add(finish,str(error),False)
        threading.Thread(target=worker,daemon=True).start()

    def capture(self, _):
        try:
            name=name_key(self.bookmark_name.get_text())
            if name in self.bookmarks.read(): self.confirm('Replace “'+name+'” with the current desktop?',lambda:self.capture_name(name,True))
            else: self.capture_name(name)
        except Exception as error: self.tell(str(error))

    def capture_name(self, name, replace=False):
        try:
            if name in {phrase_key(p) for p in self.collect().get('phrases',{})}: raise ValueError('That name is already a custom phrase')
            self.idle()
        except Exception as error: self.tell(str(error)); return
        args=['save',name]+(['--replace'] if replace else [])
        self.run_task(lambda:self.bookmark_command(args),hide=True)

    def bookmark_command(self, args):
        result=subprocess.run([PYTHON,str(ROOT/'bookmark_cli.py'),*args],capture_output=True,text=True,timeout=120)
        if result.returncode: raise ValueError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()

    def restore(self, name):
        if self.dirty:
            self.tell('Save and apply your settings before restoring a bookmark.'); return
        try: self.idle()
        except Exception as error: self.tell(str(error)); return
        self.run_task(lambda:self.bookmark_command(['restore',name]),hide=True,leave_hidden=True)

    def delete(self, name):
        try:
            _,value=self.bookmarks.resolve(name)
            self.bookmarks.write(name,None,self.bookmarks.revision(value))
            self.refresh_bookmarks(); self.tell('Deleted bookmark “'+name+'”.')
        except Exception as error: self.tell(str(error))

    def theme(self):
        path=Path.home()/'.local/state/omarchy/current/theme/colors.toml'
        try:
            raw=path.read_text()
            if raw==self.theme_key: return GLib.SOURCE_CONTINUE
            colors=tomllib.loads(raw)
            bg,fg,accent=[colors.get(k,'') for k in ('background','foreground','accent')]
            if not all(re.fullmatch(r'#[0-9a-fA-F]{6}',c) for c in (bg,fg,accent)): return GLib.SOURCE_CONTINUE
            if hasattr(self,'css'): Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(),self.css)
            self.css=Gtk.CssProvider()
            self.css.load_from_string(f'''@define-color window_bg_color {bg}; @define-color window_fg_color {fg};
                @define-color view_bg_color {bg}; @define-color view_fg_color {fg};
                @define-color accent_bg_color {accent}; @define-color accent_fg_color {bg};
                window, headerbar, stacksidebar {{ background: {bg}; color: {fg}; }}
                switch:checked {{ background: {accent}; }}
                switch slider {{ background: {fg}; }}
                stacksidebar row:selected {{ background: alpha({accent},0.18); color: {fg}; }}
                entry, dropdown > button, spinbutton {{ background: alpha({fg},0.07); color: {fg}; }}
                button.suggested-action {{ background: {accent}; color: {bg}; }}
                .title-1 {{ font-size: 23px; font-weight: bold; }}
                .title-3 {{ font-weight: bold; }} .dim-label {{ opacity: 0.72; }}''')
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(),self.css,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.theme_key=raw
        except (OSError,ValueError): pass
        return GLib.SOURCE_CONTINUE


if __name__=='__main__':
    if not Gtk.init_check(): raise SystemExit('Cannot connect to the desktop display')
    Settings().run()
