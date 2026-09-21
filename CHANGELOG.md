# Changelog

## 0.6.1 — F10 indicator completion

- Fix the indicator remaining animated after F10 release when background audio
  starts another potential wake turn while the command is processing.
- Keep indicator ownership separate from background audio detection, while
  preventing an older result from hiding a newer recording.
- Add regressions for indicator completion and excluding speech after release
  from the finished F10 recording. All 335 Python tests pass.

## 0.6.0 — First public release

- Local Whisper recognition with optional Jev semantic routing.
- Wake phrase and F10 hold-to-talk, app aliases and chained desktop commands.
- Buffered dictation with spoken/F10 finish and named destinations.
- Workspace/window bookmarks with personal phrases.
- A themed listening circle and microphone waveform pill.
- Native bar settings and history with paged layouts and searchable selectors.
- Per-user setup with a checksum-verified model download and fresh-install support.

Initially tested on Omarchy 4.0.3 / Hyprland 0.56.2. Wider hardware and desktop
compatibility is still being tested.
