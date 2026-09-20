# Contributing

Voicebind aims to keep desktop voice control fast and easy to personalize.
Changes should preserve the local path for common commands, reuse the existing
microphone stream and keep settings discoverable in the bar.

For a bug report, include your Omarchy/Hyprland versions, microphone type,
wake-word or F10 activation, the phrase you said, what happened and what you
expected. A redacted History detail can help. Never include API keys, full
personal config, private bookmark titles or unreviewed session logs.

Run `python -m unittest discover -s tests -q` in the project virtual environment.
For QML changes, also run the Qt 6 tests in `tests/qml/README.md`. Add focused
regressions for behavior changes; desktop integration tests must use temporary
XDG directories and mocked system commands.

The main paths are `listening.py` (capture and turns), `router.py` (local/Jev
interpretation), `plan.py` and `desktop_core/` (bound desktop operations),
`dictation.py`, `bookmarks.py`, and `omarchy-plugin/` plus `panel_backend.py`
(settings and history). Keep credentials and personal diagnostics out of commits.
