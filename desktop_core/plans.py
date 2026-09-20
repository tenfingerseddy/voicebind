"""Parse a complete, ordered desktop plan before any action can execute."""

from dataclasses import replace
from functools import lru_cache
import re
import unicodedata

from .apps import normalize_alias
from .commands import Command, parse_command, target_first_window_command


MAX_PLAN_COMMANDS = 12
MAX_PLAN_CHARACTERS = 3600
_CONNECTOR = re.compile(
    r"\s+(?:and\s+then|then|and)\s+|\s*,\s*(?:(?:and\s+then|then|and)\s+)?"
)
_COMMAND_START = re.compile(
    r"^(?:open|launch|start|show|close|move|send|switch|go|focus|fullscreen|full screen|"
    r"maximize|maximise|restore|float|tile|make|exit|volume|sound|turn|set|mute|unmute|un mute)\b"
)
_COORDINATED_VERBS = frozenset({"open", "launch", "start", "show", "close"})
_THERE = re.compile(r"(?: (?:in|on|to))? there(?P<background> in the background)?$")


def _normalize(text: str) -> str:
    if not isinstance(text, str) or not text or len(text) > MAX_PLAN_CHARACTERS:
        raise ValueError(f"Say a desktop plan of at most {MAX_PLAN_COMMANDS} commands.")
    text = unicodedata.normalize("NFKC", text).casefold().strip()
    # Check before splitting: a newline or shell operator cannot become an
    # innocuous boundary between individually valid commands.
    if re.search(r"[;:&|/\\$`\"(){}\[\]<>\n\r\x00-\x1f]", text):
        raise ValueError("Command contains unsupported characters.")
    text = re.sub(r"\s+", " ", text).rstrip(".!?").strip()
    text = re.sub(r"^please(?:,)? +", "", text)
    text = re.sub(r",? +please$", "", text)
    if not text:
        raise ValueError("Say a desktop command after please.")
    return text


def _expand_shortcut(text: str, shortcuts) -> str:
    """Expand one exact phrase, without interpreting shortcut values again."""
    normalized: dict[str, str] = {}
    for name, value in (shortcuts or {}).items():
        try:
            phrase, expansion = _normalize(name), _normalize(value)
        except ValueError as error:
            raise ValueError(f"Invalid voice shortcut {name!r}: {error}") from error
        if phrase in normalized and normalized[phrase] != expansion:
            raise ValueError(f"Conflicting voice shortcuts normalize to {phrase!r}.")
        normalized[phrase] = expansion
    selected = text if text in normalized else None
    if selected is not None:
        text = normalized[selected]
    if not normalized:
        return text

    # A shortcut must occupy the entire original utterance. Recognizing one
    # inside a chain could otherwise turn it into an app name, or encourage
    # recursive expansion. Boundaries also cover shortcut names containing and.
    connectors = list(_CONNECTOR.finditer(text))
    starts = {0, *(match.end() for match in connectors)}
    ends = {len(text), *(match.start() for match in connectors)}
    for start in starts:
        for phrase in normalized:
            if start + len(phrase) in ends and text.startswith(phrase, start):
                if selected is not None:
                    raise ValueError(
                        f"Voice shortcut {selected!r} refers to shortcut {phrase!r}. "
                        "Nested shortcuts are not supported; use desktop commands directly."
                    )
                raise ValueError("Voice shortcuts must be spoken as a complete command, without chaining shortcut names.")
    return text


@lru_cache(maxsize=8)
def _alias_patterns(aliases: tuple[str, ...]):
    patterns = []
    available = frozenset(aliases)
    for alias in sorted(aliases, key=lambda item: (-len(item), item)):
        if " " not in alias:
            continue
        try:
            candidate = parse_command("open " + alias.replace(" and ", " plus "))
        except ValueError:
            continue
        if candidate.action != "open" or candidate.workspace is not None:
            continue
        # Desktop catalogs normalize punctuation in names, including the
        # standalone hyphen in "Code - OSS".
        pattern = re.compile(r"(?<![\w])" + r"(?: +| +- +)".join(
            re.escape(word) for word in alias.split()
        ) + r"(?![\w])")
        ambiguous = " and " in alias and all(part in available for part in alias.split(" and "))
        patterns.append((alias, pattern, ambiguous))
    return tuple(patterns)


def _protect_aliases(text: str, app_aliases) -> tuple[str, dict[str, str]]:
    """Keep exact, trusted compound app names out of the connector splitter.

    The single-command parser intentionally rejects conjunctions in arbitrary
    app names. A temporary alphabetic app name preserves that restriction while
    admitting names that the installed application catalog actually contains.
    """
    aliases = tuple(sorted({normalize_alias(alias) for alias in app_aliases}))
    restored: dict[str, str] = {}
    for alias, pattern, ambiguous in _alias_patterns(aliases):
        matches = list(pattern.finditer(text))
        if not matches:
            continue
        # Ordinary multiword aliases already parse correctly. Substituting them
        # globally could accidentally hide a same-named workspace or folder.
        if " and " not in alias and not any(" - " in match.group() for match in matches):
            continue
        if ambiguous:
            raise ValueError(
                f"App name {alias!r} also describes separate apps. "
                "Use a configured app alias without 'and', or repeat 'open' for each app."
            )
        token = "voiceapplication" + ("z" * (len(restored) + 1))
        while re.search(r"\b" + token + r"\b", text):
            token += "z"
        text = pattern.sub(
            lambda match: token if " and " in alias or " - " in match.group() else match.group(), text
        )
        restored[token] = alias
    return text, restored


def _groups(text: str) -> list[list[str]]:
    groups: list[list[str]] = []
    start, previous_connector = 0, None
    matches = list(_CONNECTOR.finditer(text))
    boundaries = [(match.start(), match.end(), match.group()) for match in matches]
    boundaries.append((len(text), len(text), ""))
    for end, next_start, connector in boundaries:
        fragment = text[start:end].strip()
        if not fragment:
            raise ValueError("Every command connector needs a command on both sides.")
        if _COMMAND_START.match(fragment) or target_first_window_command(fragment) is not None:
            groups.append([fragment])
        else:
            if not groups:
                raise ValueError("Start with a desktop action such as 'open' or 'close'.")
            verb = groups[-1][0].split()[0]
            if verb not in _COORDINATED_VERBS or "then" in (previous_connector or "").split():
                raise ValueError("Repeat the action after 'then', or say a complete desktop command.")
            groups[-1].append(verb + " " + fragment)
        start, previous_connector = next_start, connector
    return groups


def _bind_there(text: str, workspace: int | None) -> str:
    if not re.search(r"\bthere\b", text):
        return text
    match = _THERE.search(text)
    if match is None or len(re.findall(r"\bthere\b", text)) != 1:
        raise ValueError("Use 'there' only as a command's workspace destination.")
    if workspace is None:
        raise ValueError("'There' needs an earlier workspace in this command plan.")
    preposition = "to" if text.split()[0] in {"move", "send", "switch", "go"} else "on"
    return text[:match.start()] + f" {preposition} workspace {workspace}" + (match["background"] or "")


def parse_plan(text: str, workspace_aliases: dict[str, int] | None = None,
               folder_aliases=None, *, app_aliases=(),
               shortcuts: dict[str, str] | None = None) -> tuple[Command, ...]:
    """Return all ordered commands, or reject the entire utterance.

    An explicit action starts a new clause. Coordinated open or close objects
    share a trailing workspace, while earlier explicit destinations remain
    intact. The word "there" refers to the latest workspace in this plan;
    workspace scope never carries silently into a new explicit action.

    Pass the installed catalog's aliases to preserve exact compound app names.
    Shortcuts map exact spoken phrases to ordinary desktop plans. Only a whole
    utterance can expand a shortcut, and shortcut values cannot invoke shortcuts.
    Catalog resolution and execution belong to the caller, after this returns.
    """
    text, restored = _protect_aliases(_expand_shortcut(_normalize(text), shortcuts), app_aliases)
    groups = _groups(text)
    if sum(map(len, groups)) > MAX_PLAN_COMMANDS:
        raise ValueError(f"A desktop plan supports at most {MAX_PLAN_COMMANDS} commands.")
    plan: list[Command] = []
    latest_workspace = None
    for group in groups:
        commands: list[Command] = []
        unscoped: list[int] = []
        for fragment in group:
            try:
                command = parse_command(
                    _bind_there(fragment, latest_workspace), workspace_aliases, folder_aliases
                )
                if command.app in restored:
                    command = replace(command, app=restored[command.app])
                if len(group) > 1 and command.action not in {"open", "folder", "close"}:
                    raise ValueError("Repeat the action for each workspace or window operation.")
            except ValueError as error:
                raise ValueError(f"Command {len(plan) + len(commands) + 1}: {error}") from error
            if command.workspace is None:
                unscoped.append(len(commands))
            else:
                latest_workspace = command.workspace
                for index in unscoped:
                    commands[index] = replace(commands[index], workspace=command.workspace)
                unscoped.clear()
            commands.append(command)
        plan.extend(commands)
    return tuple(plan)
