"""Small, closed desktop command grammar with no shell interpretation."""

from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata


@dataclass(frozen=True)
class Command:
    action: str
    app: str | None = None
    workspace: int | None = None
    focus: bool = True
    new: bool = False
    folder: str | None = None
    value: int | None = None
    relative: bool = False
    reference: str | None = None


NUMBERS = dict(enumerate((
    "one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty"
).split(), start=1))
for _tens, _word in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), start=2):
    NUMBERS[_tens * 10] = _word
    for _ones in range(1, 10):
        NUMBERS[_tens * 10 + _ones] = f"{_word} {NUMBERS[_ones]}"
_SPOKEN_NUMBERS = {word: number for number, word in NUMBERS.items()}
MAX_WORKSPACE = 2147483647
STANDARD_FOLDERS = frozenset({"home", "downloads", "documents", "pictures", "music", "videos", "desktop"})
# "Music" commonly names a configured player. An explicit folder word keeps
# that app alias working while still making the music folder available.
_BARE_FOLDERS = STANDARD_FOLDERS - {"music"}
_CURRENT_WINDOW = frozenset({"this", "this window", "current window", "the current window", "the window"})
_VOLUME_NUMBERS = {"zero": 0, **_SPOKEN_NUMBERS, "hundred": 100, "one hundred": 100}
WINDOW_SUFFIX_ACTIONS = {
    "fullscreen": "fullscreen", "full screen": "fullscreen",
    "maximize": "maximize", "maximise": "maximize",
    "maximized": "maximize", "maximised": "maximize",
    "restore": "restore", "float": "float", "floating": "float",
    "tile": "tile", "tiled": "tile",
}
_WINDOW_SUFFIX = re.compile(r"(.+?) (" + "|".join(map(re.escape, WINDOW_SUFFIX_ACTIONS)) + r")")


@lru_cache(maxsize=32)
def _patterns(workspace_names: tuple[str, ...]):
    names = sorted(set(_SPOKEN_NUMBERS) | set(workspace_names), key=lambda name: (-len(name), name))
    number = r"(?:[0-9]+|" + "|".join(re.escape(name).replace(r"\ ", "[ -]") for name in names) + r")"
    workspace = r"(?:(?:workspace|desktop) )?(?P<workspace>" + number + r")"
    destination = r"(?:(?:in|on) (?:(?:workspace|desktop) )?|(?:workspace|desktop) )(?P<workspace>" + number + r")"
    return (
        re.compile(
            r"(?:open|launch|start|show) (?:(?:a |an )?(?P<new>new) |a |an )?"
            r"(?P<app>.+?)(?: " + destination + r")?"
            r"(?P<background> in the background)?"
        ),
        re.compile(r"(?:move|send) (?P<app>.+?) to " + workspace + r"(?P<background> in the background)?"),
        re.compile(r"(?:(?:switch|go) to |open (?:the )?)(?:workspace|desktop) (?P<workspace>" + number + r")"),
        re.compile(r"close(?: (?P<app>.+?))?(?: " + destination + r")?"),
    )
# App names are resolved against installed applications in a separate step. These
# words cannot become part of an app name after a failed destination parse.
_RESERVED = frozenset((
    "open launch start show move send switch go delete remove run execute close "
    "shutdown reboot quit cancel don't dont not never no except while after before "
    "then and or with without but also instead please workspace desktop in on to "
    "background new do unless if stop make fullscreen maximize maximise maximized "
    "maximised restore float floating tile tiled mute unmute turn set exit full "
    "screen current this window folder folders percent up down by for all every everything there"
).split())


def _normalize_text(text: str) -> str:
    if not isinstance(text, str) or not text or len(text) > 300:
        raise ValueError("Say one short desktop command.")
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    # Preserve punctuation inside words so fractions and command separators
    # cannot disappear during normalization.
    if re.search(r"[;:&|/\\$`\"(){}\[\]<>\n\r\x00-\x1f]", text):
        raise ValueError("Command contains unsupported characters.")
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".!?").strip()
    text = re.sub(r"^please(?:,)? +", "", text)
    text = re.sub(r",? +please$", "", text)
    if not text:
        raise ValueError("Say a desktop command after please.")
    return text


def _workspace(word: str | None, aliases: dict[str, int]) -> int | None:
    if word is None:
        return None
    word = word.replace("-", " ")
    value = aliases.get(word, _SPOKEN_NUMBERS.get(word))
    if value is None:
        value = int(word)
    if not 1 <= value <= MAX_WORKSPACE:
        raise ValueError(f"Workspace must be between 1 and {MAX_WORKSPACE}.")
    return value


def _app_name(text: str) -> str:
    if (
        not re.fullmatch(r"[\w][\w .+'-]*", text)
        or any(word in _RESERVED for word in text.split())
        or any(word.startswith("-") for word in text.split())
        or not any(character.isalpha() for character in text)
    ):
        raise ValueError("App name or command suffix is not supported.")
    return text


def _window_target(text: str | None) -> str | None:
    return None if text is None or text in _CURRENT_WINDOW else _app_name(text)


def _volume_number(text: str) -> int:
    value = _VOLUME_NUMBERS.get(text.replace("-", " "))
    if value is None:
        if not re.fullmatch(r"[0-9]+", text):
            raise ValueError("Volume must be a whole number from zero to one hundred.")
        value = int(text)
    if not 0 <= value <= 100:
        raise ValueError("Volume must be between zero and one hundred.")
    return value


def _volume_command(text: str) -> Command | None:
    if re.fullmatch(r"(?:un ?mute|mute)(?: (?:the )?(?:sound|audio|volume))?", text):
        return Command("unmute" if text.startswith("un") else "mute")
    match = re.fullmatch(r"(?:turn (?:the )?)?sound (on|off)", text)
    if match:
        return Command("unmute" if match[1] == "on" else "mute")
    match = re.fullmatch(r"(?:set (?:the )?)?volume to (.+?)(?: ?%| percent)?", text)
    if match:
        return Command("volume", value=_volume_number(match[1]))
    match = re.fullmatch(r"(?:turn (?:the )?)?volume (up|down)(?: (?:by )?(.+?)(?: ?%| percent)?)?", text)
    if match:
        value = _volume_number(match[2]) if match[2] else 5
        return Command("volume", value=value if match[1] == "up" else -value, relative=True)
    return None


def target_first_window_command(text: str) -> Command | None:
    """A complete target plus window mode also starts a clause in a chain."""
    match = _WINDOW_SUFFIX.fullmatch(text)
    if match:
        return Command(WINDOW_SUFFIX_ACTIONS[match[2]], app=_window_target(match[1]))
    return None


def _window_command(text: str) -> Command | None:
    match = re.fullmatch(r"(?:focus(?: on)?|switch to) (.+)", text)
    if match:
        return Command("focus", app=_window_target(match[1]))
    actions = {"fullscreen": "fullscreen", "full screen": "fullscreen", "maximize": "maximize",
               "maximise": "maximize", "restore": "restore", "float": "float", "tile": "tile"}
    match = re.fullmatch(r"(fullscreen|full screen|maximize|maximise|restore|float|tile)(?: (.+))?", text)
    if match:
        return Command(actions[match[1]], app=_window_target(match[2]))
    match = re.fullmatch(r"make (.+?) (fullscreen|full screen|maximized|maximised|floating|tiled)", text)
    if match:
        action = {"fullscreen": "fullscreen", "full screen": "fullscreen", "maximized": "maximize",
                  "maximised": "maximize", "floating": "float", "tiled": "tile"}[match[2]]
        return Command(action, app=_window_target(match[1]))
    match = re.fullmatch(r"exit (?:fullscreen|full screen)(?: for (.+))?", text)
    if match:
        return Command("restore", app=_window_target(match[1]))
    return target_first_window_command(text)


def _folder_name(text: str, aliases) -> str | None:
    names = set(STANDARD_FOLDERS)
    for alias in aliases or ():
        normalized = _normalize_text(alias)
        if normalized not in STANDARD_FOLDERS:
            _app_name(normalized)
        names.add(normalized)
    match = re.fullmatch(r"(?:the )?(.+?) folder", text)
    if not match:
        match = re.fullmatch(r"(?:the )?folder (.+)", text)
    if match:
        name = match[1]
        if name not in names:
            raise ValueError(f"No configured folder matches {name!r}.")
        return name
    bare = text.removeprefix("the ")
    return bare if bare in _BARE_FOLDERS else None


def parse_command(text: str, workspace_aliases: dict[str, int] | None = None,
                  folder_aliases=None) -> Command:
    """Parse one complete command; ambiguous or unsupported input fails closed."""
    text = _normalize_text(text)
    aliases = {}
    for name, number in (workspace_aliases or {}).items():
        normalized = _normalize_text(name).replace("-", " ")
        if (
            not re.fullmatch(r"[\w]+(?: [\w]+)*", normalized)
            or normalized.isdigit() or normalized in _SPOKEN_NUMBERS
            or any(word in _RESERVED for word in normalized.split())
            or not isinstance(number, int) or isinstance(number, bool)
            or not 1 <= number <= MAX_WORKSPACE
        ):
            raise ValueError(f"Invalid workspace alias: {name!r}.")
        if normalized in aliases and aliases[normalized] != number:
            raise ValueError(f"Ambiguous workspace alias: {name!r}.")
        aliases[normalized] = number
    open_pattern, move_pattern, switch_pattern, close_pattern = _patterns(tuple(sorted(aliases)))
    match = switch_pattern.fullmatch(text)
    if match:
        return Command("switch", workspace=_workspace(match["workspace"], aliases))
    match = move_pattern.fullmatch(text)
    if match:
        return Command("move", app=_window_target(match["app"]),
                       workspace=_workspace(match["workspace"], aliases), focus=not bool(match["background"]))
    match = close_pattern.fullmatch(text)
    if match:
        return Command("close", app=_window_target(match["app"]), workspace=_workspace(match["workspace"], aliases))
    specialized = _volume_command(text) or _window_command(text)
    if specialized:
        return specialized
    match = open_pattern.fullmatch(text)
    if match:
        app = match["app"]
        folder = _folder_name(app, folder_aliases)
        if folder is not None:
            if match["new"]:
                raise ValueError("Creating folders is not supported.")
            return Command("folder", folder=folder, workspace=_workspace(match["workspace"], aliases),
                           focus=not bool(match["background"]))
        return Command(
            "open", app=_app_name(app), workspace=_workspace(match["workspace"], aliases),
            focus=not bool(match["background"]), new=bool(match["new"]),
        )
    raise ValueError("Try 'open Firefox in workspace three' or 'move this to three'.")


def grammar_phrases(catalog) -> list[str]:
    """Expose app phrases; the recognizer owns its command word whitelist."""
    return sorted(catalog.aliases)
