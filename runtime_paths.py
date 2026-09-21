"""Writable runtime files live outside the plugin's disposable Git checkout."""
import os
from pathlib import Path

PLUGIN_ID = 'io.github.tenfingerseddy.voicebind'


def data_home():
    return Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'voicebind'


def state_home():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'voicebind'
