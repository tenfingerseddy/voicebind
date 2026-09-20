"""Idempotent desktop settings with observed state checks."""
import json
from pathlib import Path
import subprocess

SETTINGS = {
 'nightlight.on':'Turn on warm night light', 'nightlight.off':'Turn off warm night light',
 'bar.show':'Show the status bar', 'bar.hide':'Hide the status bar',
 'notifications.quiet':'Silence notifications / enable do not disturb',
 'notifications.allow':'Allow notifications / disable do not disturb',
 'idle.awake':'Keep the laptop awake', 'idle.normal':'Allow normal automatic sleep',
 'touchpad.on':'Enable the touchpad', 'touchpad.off':'Disable the touchpad',
 'microphone.mute':'Mute the microphone', 'microphone.unmute':'Unmute the microphone',
}


def run(argv):
 r=subprocess.run(argv,capture_output=True,text=True,timeout=4)
 if r.returncode: raise RuntimeError(r.stderr.strip() or f'{argv[0]} failed')
 return r.stdout.strip()


def execute_setting(action):
 state=Path.home()/'.local/state/omarchy'
 if action.startswith('nightlight.'):
  desired=action.endswith('.on')
  def read(): return bool(json.loads(run(['omarchy','toggle','nightlight','--status']))['enabled'])
  if read()!=desired: run(['omarchy','toggle','nightlight'])
  ok=read()==desired
 elif action.startswith('bar.'):
  hidden=action.endswith('.hide')
  # The Omarchy flag is named bar-off: on means hidden.
  run(['omarchy','toggle','bar','on' if hidden else 'off'])
  ok=(state/'toggles/bar-off').exists()==hidden
 elif action.startswith('notifications.'):
  desired=action.endswith('.quiet')
  run(['omarchy','shell','notifications','setDnd','true' if desired else 'false'])
  ok=run(['omarchy','shell','notifications','dndState'])==('on' if desired else 'off')
 elif action.startswith('idle.'):
  desired=action.endswith('.awake')
  run(['omarchy','toggle','idle','stay-awake' if desired else 'allow-idle'])
  ok=(state/'indicators/stay-awake').exists()==desired
 elif action.startswith('touchpad.'):
  desired=action.endswith('.on')
  run(['omarchy','toggle','touchpad','on' if desired else 'off'])
  ok=(state/'toggles/hypr/touchpad-disabled-name').exists()!=desired
 elif action.startswith('microphone.'):
  desired=action.endswith('.mute')
  run(['wpctl','set-mute','@DEFAULT_AUDIO_SOURCE@','1' if desired else '0'])
  ok=('[MUTED]' in run(['wpctl','get-volume','@DEFAULT_AUDIO_SOURCE@']))==desired
 else:
  raise ValueError('Unknown setting')
 if not ok: raise RuntimeError('The requested setting did not take effect')
 return {'ok':True,'message':SETTINGS[action],'verified':True}
