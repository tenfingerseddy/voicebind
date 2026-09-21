"""Fast local intents first; one Jev fan-out for natural-language requests."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
import math
import re
import shutil
from pathlib import Path
from personalization import SETTINGS_PHRASES

from desktop_core.commands import Command, STANDARD_FOLDERS, NUMBERS
from desktop_core.plans import parse_plan
from settings import SETTINGS
from language import clauses as split_clauses, canonical, add_role_aliases, app_name, undecorate_apps
from panels import PANELS, parse_panel
from vt import WAKE

# Only fixed argv owned by the application may execute. No model-generated shell.
EXTRAS = {
    'voice.settings': ('Open Voicebind Settings', [str(Path(__file__).resolve().parent/'voice-control'), 'settings']),
    'brightness.up': ('Make the screen brighter', ['omarchy', 'brightness', 'display', '+10%']),
    'brightness.down': ('Dim the screen', ['omarchy', 'brightness', 'display', '10%-']),
    'keyboard.up': ('Brighten the keyboard backlight', ['omarchy', 'brightness', 'keyboard', 'up']),
    'keyboard.down': ('Dim the keyboard backlight', ['omarchy', 'brightness', 'keyboard', 'down']),
    'screenshot.region': ('Take a screenshot by selecting an area', ['omarchy', 'capture', 'screenshot', 'region']),
    'screenshot.full': ('Capture the whole screen', ['omarchy', 'capture', 'screenshot', 'fullscreen']),
    'screenshot.window': ('Capture a window', ['omarchy', 'capture', 'screenshot', 'windows']),
    'capture.text': ('Read or copy text from part of the screen', ['omarchy', 'capture', 'text']),
    'record.toggle': ('Start or stop recording the screen', ['omarchy', 'capture', 'screenrecording']),
    'theme.picker': ('Choose a desktop theme', ['omarchy', 'theme', 'switcher']),
    'background.next': ('Change to the next desktop wallpaper', ['omarchy', 'theme', 'bg', 'next']),
    'menu.clipboard': ('Show clipboard history', ['omarchy', 'menu', 'clipboard']),
    'menu.emoji': ('Show the emoji picker', ['omarchy', 'menu', 'emoji']),
    'menu.keys': ('Show keyboard shortcuts', ['omarchy', 'menu', 'keybindings']),
    'menu.main': ('Open the main desktop menu', ['omarchy', 'menu', 'summon']),
    'system.lock': ('Lock the screen', ['omarchy', 'system', 'lock']),
    'system.battery': ('Show battery level', ['omarchy', 'notification', 'battery']),
    'system.time': ('Show the time and date', ['omarchy', 'notification', 'time']),
    'system.reboot': ('Restart the laptop', ['omarchy', 'system', 'reboot']),
    'system.shutdown': ('Shut down the laptop', ['omarchy', 'system', 'shutdown']),
    'system.logout': ('Log out of the desktop session', ['omarchy', 'system', 'logout']),
    'audio.next': ('Skip the current song', ['omarchy', 'shell', 'media', 'next']),
    'audio.previous': ('Play the previous song', ['omarchy', 'shell', 'media', 'previous']),
    'audio.play': ('Resume music playback', ['omarchy', 'shell', 'media', 'play']),
    'audio.pause': ('Pause music playback', ['omarchy', 'shell', 'media', 'pause']),
    'audio.output': ('Choose another sound output', ['omarchy', 'audio', 'output', 'switch']),
    'network.status': ('Show network status', ['omarchy', 'network', 'status']),
    'network.speed': ('Test internet download speed', ['omarchy', 'network', 'speedtest', 'down']),
}
ALWAYS_CONFIRM = {'system.reboot', 'system.shutdown', 'system.logout', 'close_all'}
ACTIONS = {
    'open': 'Get an application ready, optionally on a workspace: launch if closed, otherwise move/focus its existing window. Also use for an app name and destination with no verb.',
    'focus': 'Focus an already open application, without starting it',
    'move': 'Place a window or named app on a workspace. A closed named app is launched there; current-window references require an existing window.',
    'switch': 'Switch to a numbered workspace; no application is being opened or moved',
    'folder': 'Open a folder in the file manager, e.g. Downloads',
    'close': 'Close one window or a named application; never all windows',
    'close_all': 'Explicitly close ALL windows on the desktop',
    'fullscreen': 'Set a window to full screen',
    'restore': 'Exit full screen or maximized mode',
    'maximize': 'Maximize a window while retaining the bar',
    'float': 'Set a window floating rather than tiled',
    'tile': 'Set a window tiled rather than floating',
    'volume_up': 'Raise speaker volume', 'volume_down': 'Lower speaker volume',
    'volume_set': 'Set speaker volume to a stated percentage',
    'mute': 'Mute the speakers', 'unmute': 'Unmute the speakers',
    'workspace_next': 'Go to the next occupied workspace',
    'workspace_previous': 'Go to the previous occupied workspace',
    'workspace_back': 'Return to the previously active workspace',
    **{k: v[0] for k, v in EXTRAS.items()},
    **SETTINGS,
    **PANELS,
    'none': 'Not a supported desktop instruction, ambiguous, negated, or conversation',
}
WINDOW_ACTIONS = {'close', 'focus', 'move', 'fullscreen', 'restore', 'maximize', 'float', 'tile'}
APP_ACTIONS = WINDOW_ACTIONS | {'open'}


@dataclass
class Proposal:
    commands: list[Command] = field(default_factory=list)
    route: str = 'local'
    confidence: float = 1.0
    command_like: float = 1.0
    verdict: str = 'act'
    reason: str = ''
    ms: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    slot: str = ''

    def serial(self):
        return asdict(self)


def normalize(text):
    text = text.casefold().strip().rstrip('.!?')
    text = re.sub(r'\s+', ' ', text)
    # Whisper's observed one-word tense slip, only at the imperative position.
    text = re.sub(r'^and(?: then)?\s+(?=(?:make|open|close|move|show|restore|focus)\b)', '', text)
    text = re.sub(r'^(?:closed|clothes)\b', 'close', text)
    return text


def probability(answer, options):
    choice = answer.get('choice')
    probs = answer.get('probabilities', {})
    if choice not in options or choice not in probs:
        raise ValueError('Jev returned an invalid choice')
    p = probs[choice]
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('Jev returned an invalid probability')
    return choice, float(p)


def noul(answer):
    p = answer.get('noul')
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError('Jev returned an invalid judgement')
    return float(p)


def choice(instructions, criteria):
    return {'type': 'choice', 'instructions': instructions, 'criteria': criteria}


class Router:
    def __init__(self, catalog, config, jev=None):
        self.catalog, self.config, self.jev = catalog, config, jev
        add_role_aliases(catalog)
        self.actions = {k:v for k,v in ACTIONS.items()
                        if k not in EXTRAS or shutil.which(EXTRAS[k][1][0])}
        self.folder_names = sorted(STANDARD_FOLDERS | set(config.get('folders', {})))

    def named_targets(self, text):
        """Resolve app names locally; never send the installed-app inventory."""
        text = normalize(text)
        found = {}
        spans = []
        for alias, ids in sorted(self.catalog.aliases.items(), key=lambda pair: -len(pair[0])):
            if len(ids) != 1 or len(alias) < 2:
                continue
            for match in re.finditer(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', text):
                if any(match.start() < end and match.end() > start for start,end in spans):
                    continue
                spans.append(match.span())
                found[alias] = ids[0]
        return found

    def validate(self, commands):
        if not 1 <= len(commands) <= 4:
            raise ValueError('Use at most four actions per request')
        for c in commands:
            if c.app:
                self.catalog.resolve(c.app)
            if c.workspace is not None and not 1 <= c.workspace <= 20:
                raise ValueError('Voice workspaces are numbered 1 to 20')
            if c.action in {'switch', 'move'} and c.workspace is None:
                raise ValueError('Say the workspace number')
            if c.action == 'open' and not c.app:
                raise ValueError('Say which application to open')
            if c.action == 'folder' and c.folder not in self.folder_names:
                raise ValueError('Say which folder to open')

    def local(self, text):
        text = normalize(text)
        commands = []
        latest_workspace = None
        for clause in split_clauses(text):
            clause = undecorate_apps(canonical(clause), self.catalog)
            if latest_workspace is not None:
                prep = ' to ' if clause.startswith(('move ', 'send ', 'go ', 'switch ')) else ' on '
                clause = re.sub(r'(?: (?:in|on|to))? there(?= in the background$|$)', prep + 'workspace ' + str(latest_workspace), clause)
            commands.extend(self.local_clause(clause))
            latest_workspace = commands[-1].workspace or latest_workspace
        commands = [replace(c, app=None, reference='previous') if c.app == 'voicepreviouswindow'
                    else replace(c, app=app_name(c.app, self.catalog)) for c in commands]
        self.validate(commands)
        confirm = any(c.action in ALWAYS_CONFIRM for c in commands)
        return Proposal(commands=commands, verdict='confirm' if confirm else 'act')

    def local_clause(self, text):
        if text in SETTINGS_PHRASES:
            return [Command('voice.settings')]
        panel = parse_panel(text)
        if panel:
            return [Command(panel)]
        repair = re.fullmatch(r'course (.+)', text)
        if repair:
            self.catalog.resolve(repair[1])
            return [Command('close', app=repair[1])]
        folder_close = re.fullmatch(r'close (?:the )?(' + '|'.join(map(re.escape, self.folder_names)) + r')(?: folder)?', text)
        if folder_close:
            # A folder is a file-manager window, not an application. Ask before
            # closing because some file managers do not expose the folder title.
            return [Command('close', app='file manager', folder=folder_close[1])]
        exact = {
            'next workspace': 'workspace_next', 'previous workspace': 'workspace_previous',
            'go to the next workspace': 'workspace_next', 'go back a workspace': 'workspace_previous',
            'go back': 'workspace_back', 'close all windows': 'close_all',
            'take a screenshot': 'screenshot.region', 'take a full screenshot': 'screenshot.full',
            'volume up': 'volume_up', 'volume down': 'volume_down',
        }
        if text in exact:
            action = exact[text]
            commands = [self.command(action)]
        else:
            # ASR often inserts a comma between an action and its object.
            text = re.sub(r'^(move|open|close|focus),\s*', r'\1 ', text)
            def parse(phrase):
                return list(parse_plan(phrase, self.config.get('workspaces'), self.config.get('folders'),
                                       app_aliases=self.catalog.aliases,
                                       shortcuts=self.config.get('shortcuts')))
            try:
                commands = parse(text)
            except ValueError:
                # An app plus a destination describes the desired desktop state.
                # Reuse the full parser and installed-app validation so every
                # alias gets shorthand without swallowing unsupported suffixes.
                commands = parse('open ' + text)
        return commands

    def incomplete(self, text):
        """Ask for a missing value only when the rest is unambiguous."""
        if len(split_clauses(text)) != 1:
            return None
        text = undecorate_apps(canonical(text), self.catalog)
        layout = re.fullmatch(r'make (.+)', text)
        workspace = re.fullmatch(r'(move|send|open|launch) (.+?)(?: (?:to|on|in))? (?:workspace|works based|works best)', text)
        bare_move = re.fullmatch(r'(?:move|send)(?: (.+?))?(?: to)?', text)
        app, reference = None, None
        if workspace:
            action, app = workspace[1], workspace[2]
            action = 'open' if action in {'open', 'launch'} else 'move'
            slot = 'workspace'
        elif bare_move:
            action, app, slot = 'move', bare_move[1], 'workspace'
        elif layout:
            action, app, slot = 'fullscreen', layout[1], 'layout'
        else:
            return None
        if app == 'voicepreviouswindow':
            app, reference = None, 'previous'
        elif app == 'this window':
            app = None
        elif app:
            try:
                app = app_name(app, self.catalog)
            except ValueError:
                return None
        if action == 'open' and not app:
            return None
        reason = (f'Which workspace? Say "{WAKE} five", for example.' if slot == 'workspace'
                  else f'How should that window look? Say "{WAKE} full screen", "{WAKE} maximized", or "{WAKE} floating".')
        return Proposal(commands=[Command(action, app=app, reference=reference)], verdict='clarify', reason=reason, slot=slot)

    @staticmethod
    def command(action, **kwargs):
        if action in {'volume_up', 'volume_down', 'volume_set'}:
            value = kwargs.pop('value', None)
            value = 5 if value is None else value
            return Command('volume', value=-value if action == 'volume_down' else value,
                           relative=action != 'volume_set')
        return Command(action, **kwargs)

    def questions(self, clauses):
        qs = {}
        for i, _ in enumerate(clauses):
            context = f"Read ONLY clause {i + 1} in 'clauses', a desktop request deliberately addressed by wake phrase or push-to-talk. "
            qs[f'action{i}'] = choice(context + 'Which operation best achieves the intended result? Infer omitted verbs from app names, destinations and desired states; no command syntax is required. An app wanted on a workspace means open: the local planner launches if closed, or moves/focuses its existing window. Choose move for an explicit relocation request and switch only when no app is requested. Interpret plausible speech recognition slips. Choose none if no single supported operation covers this clause.', self.actions)
            named = self.named_targets(clauses[i])
            qs[f'target{i}'] = choice(context + 'Which application is EXPLICITLY named? Use current for pronouns (this/it/that) or no named application. The planner binds it/that to the previous window in this chain, or the starting window if first. A folder such as Downloads is not an app.',
                {'current':'A window pronoun or implicit target; no application is named',
                 **{name:'The application named ' + name for name in named}})
            qs[f'workspace{i}'] = choice(context + 'Which destination workspace is requested? Understand digits, spoken numbers and ordinals. The word workspace may be omitted in an app-placement request. Use none if no destination is given; never substitute a different number.', {**{str(n):None for n in range(1,21)}, 'none':None})
            qs[f'folder{i}'] = choice(context + 'Which folder is explicitly named?', {**{n:None for n in self.folder_names}, 'none':None})
            qs[f'focus{i}'] = choice(context + 'Should the user stay on the present workspace, or follow the affected app? Stay only if explicitly requested.', {'follow':'Normal foreground command', 'stay':'In the background, silently, or stay here'})
            qs[f'new{i}'] = choice(context + 'Does the user explicitly request a NEW or ANOTHER window?', {'existing':'Normal open or focus', 'new':'Explicit new or another window'})
            # Most commands have no volume amount. Do not make Jev emit 101
            # probabilities for an irrelevant field on every window command.
            numbers = {n for n, word in {0:'zero', **NUMBERS, 100:'one hundred'}.items()
                       if re.search(r'(?<!\w)(?:'+str(n)+'|'+re.escape(word)+r')(?!\w)', clauses[i])}
            if re.search(r'\bhundred\b', clauses[i]): numbers.add(100)
            if numbers:
                qs[f'volume{i}'] = choice(context + 'Which volume percentage or amount is explicitly stated? none if unstated.', {**{str(n):None for n in sorted(numbers)}, 'none':None})
            qs[f'complete{i}'] = {'type':'noul', 'instructions': context + 'Can one operation from supported_actions achieve the entire intended result? Judge meaning, not grammatical completeness. A named app alone or with a destination is a complete request to open/move/focus it; an explicit verb is unnecessary. An app on a workspace is ONE open operation; the planner handles whether it is running. This/it refers to the current window and needs no app name. Explicitly named applications must be under named_apps. Workspace destinations must be in 1–20. Questions about battery, shortcuts or network speed are supported. Reject unsupported operations, unknown apps, negation, conditions, missing required values, or a request only partly covered by one operation.', 'criteria':{'true':'The whole intended result is supported, including shorthand or an implied action', 'false':'An unsupported result, missing required value, or only part of the request can be done'}}
        qs['addressed'] = {'type':'noul', 'instructions':'The transcript was deliberately addressed to a laptop using its wake phrase or push-to-talk. Does it convey a desktop intent? Accept natural shorthand, fragments, app names with destinations, desired states, colloquial requests and questions. No explicit action verb or fixed phrasing is required. Reject conversation, negated or quoted commands, narration, and unrelated word salad. Allow plausible speech-recognition slips.', 'criteria':{'true':'An intended desktop action or question, including shorthand', 'false':'Conversation, negation, narration, noise, or unrelated words'}}
        return qs

    def decide(self, text, force_jev=False):
        text = normalize(text)
        if re.match(r"^(?:(?:i|he|she|they|we) (?:said|told|was|were|used to)\b|(?:if|when|unless|after|before)\b|(?:do not|don't|dont|never) (?:open|close|move|launch|restart|shut)\b)", text):
            return Proposal(verdict='drop', reason='Say a direct command when you want me to act')
        if text in {'never mind', 'nevermind', 'cancel', 'stop', 'uh hang on', 'hang on'}:
            return Proposal(verdict='drop', reason='Cancelled')
        if re.search(r'\b(?:if|unless|provided that|only when|as long as)\b', text):
            return Proposal(verdict='drop', reason='Conditional commands are not supported; say the result you want directly')
        if not force_jev:
            try:
                return self.local(text)
            except ValueError:
                pass
            question = self.incomplete(text)
            if question:
                return question
        if re.search(r'\b(?:close|shut|quit) (?:another|the other|some other) (?:app|application|window)\b', text):
            return Proposal(verdict='drop', reason='Say which other app to close; the whole request is waiting for a name')
        if not self.jev:
            return Proposal(verdict='drop', reason='Jev is unavailable; use a direct desktop command')
        # Explicit clause boundaries only. Other conjunctions go to completeness
        # judgement rather than executing a plausible fragment of a request.
        clauses = split_clauses(text)
        if len(clauses) > 4 or len(text) > 800:
            return Proposal(verdict='drop', reason='Use at most four short actions per request')
        qs = self.questions(clauses)
        ms, payload = self.jev.ask(text, {'clauses':clauses,
            'supported_actions': self.actions,
            'named_apps':[list(self.named_targets(clause)) for clause in clauses]}, questions=qs)
        answers = payload['answers']
        p = Proposal(route='jev', ms={'jev':round(ms,1)}, usage=payload.get('usage',{}), answers=answers)
        p.command_like = noul(answers['addressed'])
        if p.command_like < .60:
            p.verdict, p.reason = 'drop', 'I did not hear a clear desktop request'
            return p
        scores = []
        for i in range(len(clauses)):
            def pick(name):
                key = name + str(i)
                if name == 'volume' and key not in qs: return 'none', 1.0
                value, score = probability(answers[key], qs[key]['criteria'])
                return value, score
            action, confidence = pick('action')
            complete = noul(answers[f'complete{i}'])
            if action == 'none' or complete < .60:
                p.verdict, p.reason = 'drop', 'Please give a complete supported desktop command'
                return p
            scores.extend([confidence, complete])
            kw = {}
            if action in APP_ACTIONS:
                app, score = pick('target')
                scores.append(score)
                kw['app'] = None if app == 'current' else self.catalog.resolve(app).id
                if app == 'current' and re.search(r'\b(?:it|that(?: app| window)?|the same window)\b', clauses[i]):
                    kw['reference'] = 'previous'
                if app == 'current' and self.named_targets(clauses[i]):
                    raise ValueError('The named application was not resolved; please rephrase')
            if action in APP_ACTIONS | {'switch','folder'}:
                ws, score = pick('workspace')
                scores.append(score)
                kw['workspace'] = None if ws == 'none' else int(ws)
            if action in {'open', 'move', 'folder', 'focus', 'fullscreen', 'restore', 'maximize', 'float', 'tile'}:
                follow, score = pick('focus')
                scores.append(score)
                kw['focus'] = follow == 'follow'
            if action == 'open':
                new, score = pick('new')
                scores.append(score)
                kw['new'] = new == 'new'
            if action == 'folder':
                folder, score = pick('folder')
                scores.append(score)
                kw['folder'] = folder
            if action.startswith('volume_'):
                amount, score = pick('volume')
                scores.append(score)
                if amount == 'none' and action == 'volume_set':
                    p.verdict, p.reason = 'drop', 'Say the volume percentage'
                    return p
                kw['value'] = None if amount == 'none' else int(amount)
            p.commands.append(self.command(action, **kw))
        self.validate(p.commands)
        p.confidence = min(scores)
        destructive = any(c.action in ALWAYS_CONFIRM for c in p.commands)
        # Scores remain diagnostic. An addressed, supported and validated
        # command uses the best interpretation without a second voice roundtrip.
        p.verdict = 'confirm' if destructive else 'act'
        return p
