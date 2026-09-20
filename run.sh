#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [[ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
  mapfile -t vt_sockets < <(find "$XDG_RUNTIME_DIR/hypr" -mindepth 2 -maxdepth 2 -name .socket.sock)
  if (( ${#vt_sockets[@]} != 1 )); then
    echo 'Cannot identify a unique Hyprland session' >&2
    exit 1
  fi
  HYPRLAND_INSTANCE_SIGNATURE=$(basename -- "$(dirname -- "${vt_sockets[0]}")")
  export HYPRLAND_INSTANCE_SIGNATURE
fi
hyprctl -j activeworkspace >/dev/null
# A warm server is shared by text/audio diagnostics and the listener.
exec 9>"$XDG_RUNTIME_DIR/jev-whisper-start.lock"
flock 9
if ! curl -fsS --max-time 1 http://127.0.0.1:8178/health >/dev/null 2>&1; then
  whisper-server -m "$PWD/models/ggml-base.en.bin" -t 4 --host 127.0.0.1 --port 8178 \
    --suppress-nst --no-fallback --best-of 1 >"$PWD/whisper-server.log" 2>&1 9>&- &
  vt_whisper_pid=$!
  for ((vt_attempt=0; vt_attempt<80; vt_attempt++)); do
    curl -fsS --max-time 1 http://127.0.0.1:8178/health >/dev/null 2>&1 && break
    kill -0 "$vt_whisper_pid" 2>/dev/null || { echo 'Whisper failed; see whisper-server.log' >&2; exit 1; }
    sleep .1
  done
fi
flock -u 9
exec 9>&-
curl -fsS --max-time 2 http://127.0.0.1:8178/health >/dev/null
exec ./.venv/bin/python -u vt.py "$@"
