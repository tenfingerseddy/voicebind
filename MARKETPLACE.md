# Marketplace submission

Repository: https://github.com/tenfingerseddy/voicebind

Category: **Productivity**

Tags: **AI**, **Bar**, **Workspaces**

Plugin ID: `io.github.tenfingerseddy.voicebind`

Maintainer notes for the [submission form](https://plugins.omarchy.org/publish.html):

Voicebind combines local Whisper speech recognition with optional Jev AI command
interpretation, dictation, window/workspace bookmarks and a native Quickshell
settings/history popup. Users can add their own Jev API key. Tested on Omarchy 4.0.3
and Hyprland 0.56.2 with Lua bindings. English only in this public preview.

Version 0.7.2 makes Jev the first desktop-command interpreter when a key is
configured, with local fallback for API failures. Voice → Jev exposes confidence
rejection (off by default) and its adjustable 0–100% threshold (initially 60).
Low scores no longer prevent a supported action when rejection is off. Explicit
cancellation, target validation and destructive-action confirmation are retained.
Each ordinary desktop command now makes a Jev request when a key is configured;
dictation controls and bookmarks stay local. No new dependency is introduced.

Please list this as **Manual setup**: the root plugin installs through
`omarchy plugin add`, but the speech backend needs the explicit setup steps in
the README. The catalog supports an installation override with `mode: "manual"`.
Suggested note: “Requires Python/Whisper backend setup and a model download.
Follow the README for installation and removal.”

Setup installs NumPy from six SHA-256-locked wheels covering standard CPython
3.12–3.14 on Linux/glibc x86_64 and aarch64. pip enforces hashes, accepts only
wheels and reinstalls an existing matching version from the verified artifact.
Unsupported runtimes fail before setup; there is no source-build fallback.
The complete lock and verification process are in `requirements.txt` and
`DEPENDENCIES.md`. Setup also downloads the 148 MB Whisper base.en model
from Hugging Face with SHA-256 verification. The README lists all system packages.
`install.py --start` opts into microphone access, a per-user listener service,
F10/Shift+F10/Ctrl+F10 bindings and replacing an existing voice daemon. Affected
files are backed up. The bar alone does not start microphone capture.

Backend data lives outside the plugin checkout. Removal requires `voicebind
uninstall` **before** `omarchy plugin remove io.github.tenfingerseddy.voicebind`;
Omarchy has no backend uninstallation hook. User settings, keys and history are
retained. Optional Jev requests send command transcripts and app/action choices;
speech recognition and dictation stay local. There is no analytics service.

MIT license, including the retained desktop component notice. The root `preview.png`
provides the marketplace image. The README includes listening, Jev and personal
phrase screenshots. These render the real UI components with example configuration;
the waveform is illustrated with sample data. No personal history or credentials
are included.
