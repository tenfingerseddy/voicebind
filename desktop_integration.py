"""Install this project's Omarchy overlay and reversible hold-to-talk bindings."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from runtime_paths import PLUGIN_ID, state_home

ROOT = Path(__file__).resolve().parent
BIN_DIR = Path.home()/'.local/bin'
BLOCK = re.compile(r'-- BEGIN (?:omarchy-voice|jev-voice) managed shortcuts\n.*?-- END (?:omarchy-voice|jev-voice) managed shortcuts', re.S)
UNBIND = '\n'.join(f'hl.unbind("{key}")' for key in ('F10','SHIFT + F10','CTRL + F10'))
BINDS = '''-- BEGIN jev-voice managed shortcuts
{unbind}
o.bind("F10", "Voicebind: Hold to speak", "voicebind press")
o.bind("F10", "Voicebind: Release to execute", "voicebind release", {{ release = true }})
o.bind("SHIFT + F10", "Voicebind: Toggle wake listening", "voicebind toggle-wake")
o.bind("CTRL + F10", "Voicebind: Cancel", "voicebind cancel")
-- END jev-voice managed shortcuts'''.format(unbind=UNBIND)


def environment():
    env = dict(os.environ)
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    if not env.get('WAYLAND_DISPLAY'):
        displays = [p for p in Path(env['XDG_RUNTIME_DIR']).glob('wayland-*') if p.is_socket()]
        if len(displays) == 1:
            env['WAYLAND_DISPLAY'] = displays[0].name
    if not env.get('HYPRLAND_INSTANCE_SIGNATURE'):
        paths = list((Path(env['XDG_RUNTIME_DIR'])/'hypr').glob('*/.socket.sock'))
        if len(paths) != 1: raise RuntimeError('Cannot identify a unique Hyprland session')
        env['HYPRLAND_INSTANCE_SIGNATURE'] = paths[0].parent.name
    return env


def install_overlay(dest):
    if dest.resolve() == ROOT.resolve():
        # Native `omarchy plugin add` already installed the repository. Do not
        # rewrite tracked files: future fast-forward updates must stay clean.
        return
    if (dest/'.git').exists():
        raise RuntimeError(f'Voicebind is managed by Omarchy at {dest}. Run its install.py instead.')
    dest.mkdir(parents=True,exist_ok=True)
    source = ROOT/'omarchy-plugin'
    assets = {p.relative_to(source): p.read_bytes() for p in sorted(source.rglob('*'))
              if p.is_file() and p.name != 'manifest.json'}
    # This shell's Qt component cache survives plugin rescans. A content-based
    # entry URL loads updates without restarting the shell or its lock service.
    digest = hashlib.sha256()
    for name, content in assets.items(): digest.update(str(name).encode()+b'\0'+content+b'\0')
    revision = 'revisions/h'+digest.hexdigest()[:12]
    for name, content in assets.items():
        path = dest/revision/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists(): path.write_bytes(content)
    manifest = json.loads((ROOT/'manifest.json').read_text())
    manifest['entryPoints'] = {key: revision+'/'+str(Path(entry).relative_to('omarchy-plugin'))
                               for key, entry in manifest['entryPoints'].items()}
    rendered = json.dumps(manifest,indent=2)+'\n'
    path = dest/'manifest.json'
    if not path.exists() or path.read_text() != rendered: path.write_text(rendered)


def install_settings_launcher():
    apps = Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')))/'applications'
    apps.mkdir(parents=True,exist_ok=True)
    executable = str(ROOT/'voice-control').replace('\\','\\\\').replace('"','\\"')
    (apps/'voicebind-settings.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Voicebind Settings\n'
        'Comment=Customize voice phrases, app names and desktop bookmarks\n'
        'Exec="'+executable+'" settings\nIcon=audio-input-microphone\n'
        'StartupWMClass=org.omarchy.JevVoiceSettings\nTerminal=false\n'
        'Categories=Settings;Utility;\nKeywords=Voicebind;Voice;Dictation;Bookmarks;Jev;\n')
    # Remove only our previous launcher, keeping unrelated desktop entries.
    old = apps/'jev-voice-settings.desktop'
    if old.exists() and executable in old.read_text(): old.unlink()


def install_command(original=False):
    """Keep the original entry point available for the explicit rollback."""
    path = BIN_DIR/'voicebind'
    backup = state_home()/'backups/original-voicebind-command'
    if original:
        if not (path.is_symlink() and path.resolve() == ROOT/'voice-control'):
            return  # A user-installed replacement is not ours to remove.
        if backup.exists() or backup.is_symlink():
            path.unlink(missing_ok=True)
            shutil.copy2(backup, path, follow_symlinks=False)
        elif path.is_symlink() and path.resolve() == ROOT/'voice-control':
            path.unlink()
        return
    if path.is_symlink() and path.resolve() == ROOT/'voice-control': return
    if (path.exists() or path.is_symlink()) and not (backup.exists() or backup.is_symlink()):
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup, follow_symlinks=False)
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.voicebind-'+str(time.time_ns()))
    temp.symlink_to(ROOT/'voice-control')
    os.replace(temp, path)


def integrate(original=False):
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    dest = config/'omarchy/plugins'/PLUGIN_ID
    if not original and (dest/'.git').exists() and dest.resolve() != ROOT.resolve():
        raise RuntimeError(f'Voicebind is managed by Omarchy at {dest}. Run its install.py instead.')
    install_command(original)
    if not original:
        install_settings_launcher()
    else:
        launcher = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))/'applications/voicebind-settings.desktop'
        if launcher.exists() and str(ROOT/'voice-control') in launcher.read_text():
            launcher.unlink()
    path = config/'hypr/bindings.lua'
    backup = state_home()/'backups/original-voice-bindings.lua'
    if original and not backup.exists():
        # Setup without --start installed no shortcuts or bar integration.
        return
    text = path.read_text()
    match = BLOCK.search(text)
    if not original and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(match[0] if match else '')
    if original:
        old = backup.read_text() if backup.exists() else ''
        if old:
            first, _, rest = old.partition('\n')
            replacement = first+'\n'+UNBIND+'\n'+rest
        else:
            replacement = ''
    else:
        replacement = BINDS
    updated = BLOCK.sub(lambda m:replacement, text, count=1) if match else text+'\n'+replacement+'\n'
    if updated != text:
        shutil.copy2(path, path.with_name(path.name+f'.before-jev-voice-{time.time_ns()}'))
        path.write_text(updated)
    shell_path = config/'omarchy/shell.json'
    shell = json.loads(shell_path.read_text())
    ident = lambda p: p.get('id') if isinstance(p, dict) else p
    related = {PLUGIN_ID, 'jev-voice', 'voicebind'}
    plugins = [p for p in shell.get('plugins',[]) if ident(p) not in related]
    old_bar = state_home()/'backups/original-voice-bar.json'
    if not original and not old_bar.exists():
        old_bar.parent.mkdir(parents=True,exist_ok=True)
        old_ids = [ident(p) for rows in shell.get('bar',{}).get('layout',{}).values()
                   for p in rows if ident(p) in {'voicebind', 'jev-voice'}]
        old_bar.write_text(json.dumps({'disabled': shell.get('disabledPlugins', []),
                                      'replacement_id': next(iter(old_ids), None),
                                      'plugins': [p for p in shell.get('plugins', []) if ident(p) in {'voicebind', 'jev-voice'}]}, indent=2)+'\n')
    prior = json.loads(old_bar.read_text()) if old_bar.exists() else {}
    disabled = [p for p in shell.get('disabledPlugins',[]) if p not in related]
    if not original:
        install_overlay(dest)
        plugins.append({'id':PLUGIN_ID})
        disabled += ['voicebind', 'jev-voice']
    else:
        disabled += [p for p in prior.get('disabled', []) if p in {'voicebind','jev-voice'}]
        disabled.append(PLUGIN_ID)
        plugins += prior.get('plugins', [])
    shell['disabledPlugins'] = disabled
    bar = shell.setdefault('bar',{})
    layout = bar.get('layout')
    if layout is None and not original:
        default_path = Path('/usr/share/omarchy/config/omarchy/shell.json')
        defaults = json.loads(default_path.read_text()) if default_path.exists() else {}
        layout = defaults.get('bar',{}).get('layout', {'left': [], 'center': [], 'right': []})
        bar['layout'] = layout
    found = False
    if layout is not None:
        for section, rows in layout.items():
            updated_rows = []
            for item in rows:
                ident = item.get('id') if isinstance(item,dict) else item
                if ident in related:
                    if not found:
                        restore_id = prior.get('replacement_id', 'voicebind' if prior.get('voicebind') else None)
                        if not original or restore_id: updated_rows.append({'id':restore_id if original else PLUGIN_ID})
                        found = True
                else: updated_rows.append(item)
            layout[section] = updated_rows
        if not found and not original: layout.setdefault('right',[]).append({'id':PLUGIN_ID})
    shell['plugins'] = plugins
    rendered=json.dumps(shell,indent=2)+'\n'
    if rendered != shell_path.read_text():
        shutil.copy2(shell_path,shell_path.with_name(f'shell.json.before-jev-voice-{time.time_ns()}'))
        shell_path.write_text(rendered)
    env = environment()
    subprocess.run(['hyprctl','reload'],env=env,check=True,capture_output=True,text=True)
    errors = subprocess.run(['hyprctl','configerrors'],env=env,check=True,capture_output=True,text=True).stdout.strip()
    if errors: raise RuntimeError(errors)
    subprocess.run(['omarchy','shell','shell','rescanPlugins'],env=env,check=True,capture_output=True,text=True)
    print('Legacy Voicebind bindings restored.' if original else 'Voicebind bar and F10 bindings installed and validated.')


if __name__ == '__main__':
    if sys.argv[1:] not in (['install'],['original']): sys.exit('Usage: desktop_integration.py install | original')
    integrate(sys.argv[1]=='original')
