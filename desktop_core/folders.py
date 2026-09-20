"""Resolve spoken folder shortcuts through XDG, without evaluating shell text."""
from pathlib import Path
import subprocess
import unicodedata


STANDARD_FOLDERS = {
    "desktop": "DESKTOP", "documents": "DOCUMENTS", "downloads": "DOWNLOAD",
    "music": "MUSIC", "pictures": "PICTURES", "videos": "VIDEOS",
}


def resolve_folder(name, aliases=None):
    normalized = {}
    for alias, value in (aliases or {}).items():
        key = " ".join(unicodedata.normalize("NFKC", alias).casefold().split())
        if key in normalized and normalized[key] != value:
            raise ValueError(f"Ambiguous folder shortcut: {alias}")
        normalized[key] = value
    if name in normalized:
        path = Path(normalized[name]).expanduser()
    elif name == "home":
        path = Path.home()
    elif name in STANDARD_FOLDERS:
        result = subprocess.run(["xdg-user-dir", STANDARD_FOLDERS[name]],
                                capture_output=True, text=True, timeout=2, check=True)
        path = Path(result.stdout.strip())
    else:
        raise ValueError(f"Unknown folder shortcut: {name}")
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"Folder shortcut {name!r} must point to an existing absolute directory")
    return path
