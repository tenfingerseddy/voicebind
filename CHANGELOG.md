# Changelog

## 0.7.1 — Verified Python dependencies

- Lock SHA-256 hashes for every supported NumPy wheel: standard CPython
  3.12–3.14 on Linux/glibc x86_64 and aarch64.
- Enforce pip hash checks and binary-only installation; reinstall matching
  versions so older unverified installations cannot bypass artifact verification.
- Reject unsupported runtimes before setup and test all six wheel combinations
  in CI, including rejection of tampered artifacts.

## 0.7.0 — Marketplace packaging

- Publish one root manifest with author, license and permanent plugin ID
  `io.github.tenfingerseddy.voicebind`.
- Support Omarchy plugin installation without rewriting its Git checkout.
- Keep the Python environment, models, logs and rollback backups outside the
  plugin folder; preserve existing personal configuration and local history.
- Show setup instructions when the bar is installed before the speech backend.
- Document dependencies, microphone/F10 opt-in, updates and the two-step removal
  of the separate listener service and plugin.

## 0.6.2 — Natural app placement

- Accept app/destination shorthand such as “files workspace two”, “browser on
  three” and shorthand followed by another action, using installed app aliases.
- Interpret the desired result: named-app placement launches a closed app or
  reuses an existing window, including when Jev classifies the request as “move”.
- Teach Jev to accept implied verbs, fragments and desired states. Existing
  validation of supported operations, workspace bounds and conditions remains.
- 343 regression tests and 14 real Jev interpretation checks passed. The optional
  synthetic API evaluation is available as `tests/check_jev.py`.

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
