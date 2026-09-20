"""Speaker controls through WirePlumber. Never execute recognized shell text."""
import re
import shutil
import subprocess


def check_available():
    if not shutil.which("wpctl"):
        raise ValueError("Sound commands need wpctl from WirePlumber")


def request(*args):
    result = subprocess.run(["wpctl", *args], capture_output=True, text=True, timeout=2)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "WirePlumber could not change the speaker volume")
    return result.stdout.strip()


def execute_sound(command):
    check_available()
    sink = "@DEFAULT_AUDIO_SINK@"
    if command.action == "volume":
        value = command.value
        if isinstance(value, bool) or not isinstance(value, int) or not -100 <= value <= 100:
            raise ValueError("Volume must be between 0 and 100 percent")
        if not command.relative and value < 0:
            raise ValueError("Volume must be between 0 and 100 percent")
        before = request("get-volume", sink)
        match = re.search(r"Volume:\s*([0-9.]+)", before)
        if not match:
            raise RuntimeError("Could not read the speaker volume")
        target = max(0, min(100, round(float(match[1]) * 100) + value)) if command.relative else value
        request("set-volume", "-l", "1.0", sink, f"{target}%")
    elif command.action in {"mute", "unmute"}:
        request("set-mute", sink, "1" if command.action == "mute" else "0")
    else:
        raise ValueError("Unsupported sound action")
    after = request("get-volume", sink)
    match = re.search(r"Volume:\s*([0-9.]+)", after)
    if not match:
        raise RuntimeError("Could not verify the speaker volume")
    volume = round(float(match[1]) * 100)
    muted = "[MUTED]" in after
    if command.action == "volume" and abs(volume - target) > 1:
        raise RuntimeError("The speaker volume did not reach the requested level")
    if command.action in {"mute", "unmute"} and muted != (command.action == "mute"):
        raise RuntimeError("The speaker mute state did not change")
    return {"ok": True, "volume": volume, "muted": muted,
            "message": "Sound muted" if muted else f"Volume {volume}%"}
