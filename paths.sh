# Shared by the CLI and listener. Keep these paths in sync with runtime_paths.py.
vt_data="${XDG_DATA_HOME:-$HOME/.local/share}/voicebind"
vt_state="${XDG_STATE_HOME:-$HOME/.local/state}/voicebind"
vt_python="$vt_data/venv/bin/python"
export PYTHONDONTWRITEBYTECODE=1
umask 077
