#!/usr/bin/env python3
"""Per-user setup. Run from an unlocked, supported Omarchy desktop."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.request

from runtime_paths import PLUGIN_ID, data_home, state_home

ROOT = Path(__file__).resolve().parent
MODEL_SHA256 = 'a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002'
MODEL_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin'
COMMANDS = ('hyprctl', 'omarchy', 'whisper-server', 'parecord', 'pactl',
            'wpctl', 'wtype', 'uwsm-app', 'curl', 'flock', 'systemctl')


def config_root():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))


def check():
    check_python_platform()
    missing = [cmd for cmd in COMMANDS if not shutil.which(cmd)]
    if missing:
        raise RuntimeError('Missing commands: '+', '.join(missing)+'. See README prerequisites.')
    cfg = config_root()
    plugin = cfg/'omarchy/plugins'/PLUGIN_ID
    if (plugin/'.git').exists() and plugin.resolve() != ROOT.resolve():
        raise RuntimeError(f'Voicebind is managed by Omarchy at {plugin}. Run its install.py instead.')
    if not (cfg/'hypr/bindings.lua').is_file() or not (cfg/'omarchy/shell.json').is_file():
        raise RuntimeError('Requires Omarchy with Lua Hyprland bindings and the Quickshell bar (tested on Omarchy 4.0.3).')
    print('Dependency and desktop configuration checks passed.')


def check_python_platform():
    libc, version = platform.libc_ver()
    try:
        glibc_ok = libc == 'glibc' and tuple(map(int, version.split('.')[:2])) >= (2, 27)
    except ValueError:
        glibc_ok = False
    if (sys.implementation.name != 'cpython' or not (3, 12) <= sys.version_info[:2] <= (3, 14)
            or sys.platform != 'linux' or platform.machine() not in {'x86_64', 'aarch64'}
            or not glibc_ok or sysconfig.get_config_var('Py_GIL_DISABLED') == 1):
        raise RuntimeError('Verified wheels require CPython 3.12–3.14 (standard GIL build) '
                           'on Linux/glibc 2.27+ x86_64 or aarch64. No source build will be attempted.')


def prepare_python():
    subprocess.run([sys.executable, '-m', 'venv', str(data_home()/'venv')], check=True)
    python = data_home()/'venv/bin/python'
    # Reinstall even a matching version: an older unverified installation must
    # not satisfy the requirement without downloading and checking the wheel.
    subprocess.run([str(python), '-m', 'pip', '--isolated', 'install',
                    '--require-hashes', '--only-binary=:all:', '--force-reinstall',
                    '-r', str(ROOT/'requirements.txt')], check=True)


def model_valid(path):
    if not path.is_file():
        return False
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == MODEL_SHA256


def prepare_model(source=None):
    dest = data_home()/'models/ggml-base.en.bin'
    if model_valid(dest):
        return
    if source is None and model_valid(ROOT/'models/ggml-base.en.bin'):
        source = ROOT/'models/ggml-base.en.bin'
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.download-', dir=dest.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            if source:
                with source.open('rb') as stream:
                    shutil.copyfileobj(stream, output)
            else:
                print('Downloading Whisper base.en (148 MB) from the whisper.cpp model repository.', flush=True)
                with urllib.request.urlopen(MODEL_URL, timeout=60) as stream:
                    shutil.copyfileobj(stream, output)
        if not model_valid(temp):
            raise RuntimeError('Model checksum mismatch; the existing model was kept.')
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def quote(value, executable=False):
    value = str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"')
    if executable:
        value = value.replace('$', '$$')
    if any(c in value for c in '\n\r\0'):
        raise ValueError('The installation path must not contain line breaks.')
    return '"'+value+'"'


def service_text(root):
    return f'''[Unit]
Description=Voicebind — local speech and desktop control
After=graphical-session.target pipewire-pulse.service
PartOf=graphical-session.target
ConditionPathExists={str(root/'run.sh').replace('%', '%%')}
Conflicts=omarchy-voice.service
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={str(root).replace('%', '%%')}
ExecStart={quote(root/'run.sh', executable=True)} --live
Restart=on-failure
RestartSec=2
TimeoutStopSec=8
KillMode=control-group
RuntimeDirectory=jev-voice
RuntimeDirectoryMode=0700
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment={quote('XDG_CONFIG_HOME='+str(config_root()))}
Environment={quote('XDG_DATA_HOME='+str(data_home().parent))}
Environment={quote('XDG_STATE_HOME='+str(state_home().parent))}

[Install]
WantedBy=graphical-session.target
'''


def write_service():
    path = config_root()/'systemd/user/jev-voice.service'
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = service_text(ROOT)
    backup = state_home()/'backups/previous-jev-voice.service'
    if path.exists() and path.read_text() != rendered and not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
    path.write_text(rendered)


def uninstall():
    path = config_root()/'systemd/user/jev-voice.service'
    if not path.exists() or path.read_text() != service_text(ROOT):
        raise RuntimeError('The installed service belongs to another checkout or was edited. No files changed.')
    subprocess.run(['systemctl', '--user', 'disable', '--now', 'jev-voice.service'], check=True)
    from desktop_integration import integrate
    integrate(original=True)
    backup = state_home()/'backups/previous-jev-voice.service'
    if backup.exists():
        shutil.copy2(backup, path)
    else:
        path.unlink()
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Voicebind removed from the bar, shortcuts and autostart. Settings and local history are retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true', help='only check prerequisites; change nothing')
    mode.add_argument('--uninstall', action='store_true', help='remove this installation; retain personal settings')
    parser.add_argument('--model', type=Path, help='copy an existing, checksum-verified ggml-base.en.bin')
    parser.add_argument('--start', action='store_true', help='also install bar/F10 bindings and enable listening at login')
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
        return
    check()
    if args.check:
        return
    os.umask(0o077)
    # Older releases kept these files in the checkout. Preserve rollback/history
    # before switching paths; never overwrite newer external state on an update.
    state_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    for folder in ('backups', 'reports', 'sessions'):
        source = ROOT/folder
        if source.is_dir() and not (state_home()/folder).exists():
            shutil.copytree(source, state_home()/folder, symlinks=True)
    prepare_python()
    prepare_model(args.model)
    write_service()
    from desktop_integration import install_command
    install_command()
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    print('Setup complete. Run ~/.local/bin/voicebind start to install the bar and F10 shortcuts and begin listening.')
    if args.start:
        # enable --now alone leaves an already running older checkout alive.
        subprocess.run(['systemctl', '--user', 'stop', 'jev-voice.service'], check=True)
        subprocess.run([str(ROOT/'voice-control'), 'start'], check=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
