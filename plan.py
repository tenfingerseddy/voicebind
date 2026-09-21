"""Compile complete requests to bound, verified desktop actions.

Runtime plans have explicit completion conditions and never need a model
'satisfied' loop.
"""
from dataclasses import dataclass, field, replace
import subprocess
import time
import re

from desktop_core.commands import Command, NUMBERS
from desktop_core.desktop import DesktopController, WindowTarget, capture_window, recent_key
from router import EXTRAS, WINDOW_ACTIONS
from settings import SETTINGS, execute_setting
from panels import PANELS, resolve_panel, execute_panel


@dataclass(frozen=True)
class ResultRef:
    step: int


@dataclass
class Step:
    command: Command
    target: object = None
    label: str = ''


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)

    @property
    def label(self):
        return '; then '.join(s.label for s in self.steps)


class Planner:
    def __init__(self, catalog, config, hypr=None):
        self.desktop = DesktopController(catalog, config, hypr)
        self.catalog = catalog

    def prepare(self, commands, active=None):
        desktop = self.desktop
        clients = desktop.hypr.query('clients')
        if active is None:
            active = capture_window(desktop.hypr.query('activewindow'))
        planned_targets = {}
        previous, previous_name = active, 'this window'
        closed_targets = []
        steps = []
        for command in commands:
            action = command.action
            app = self.catalog.resolve(command.app) if command.app else None
            name = app.name if app else 'this window'
            label = action.replace('_', ' ').capitalize()
            target = active
            if command.reference == 'previous':
                target, name = previous, previous_name
                if not target:
                    raise ValueError('There is no earlier window for "it" in this request')
            if action == 'close_all':
                if not clients:
                    raise ValueError('There are no windows to close')
                for client in clients:
                    steps.append(Step(Command('close'), capture_window(client), 'Close ' + client.get('class', 'window')))
                    closed_targets.append(capture_window(client))
                previous = None
                continue
            if action in WINDOW_ACTIONS or action == 'open':
                if app:
                    candidates = [c for c in clients if desktop.app_matches(app, c)]
                    if command.folder:
                        matching = [c for c in candidates if command.folder.casefold() in c.get('title','').casefold()]
                        if matching:
                            candidates = matching
                        elif len(candidates) != 1:
                            raise ValueError('I cannot identify that folder window; focus it and say close this window')
                    if action in {'close', 'focus'} and command.workspace is not None:
                        candidates = [c for c in candidates if c['workspace']['id'] == command.workspace]
                    candidates.sort(key=lambda c: recent_key(c, command.workspace))
                    if app.id in planned_targets and action != 'open':
                        target = planned_targets[app.id]
                        command = replace(command, app=None)
                    elif candidates and not command.new:
                        target = capture_window(candidates[0])
                        command = replace(command, app=None)
                        if action == 'open':
                            command = replace(command, action='focus')
                    elif action == 'move' and command.workspace is not None and app.id not in planned_targets:
                        # Placement is a desired state: if the named app is
                        # closed, launch it there instead of failing a natural
                        # request just because the classifier chose "move".
                        command = replace(command, action='open')
                        action, target, label = 'open', None, 'Open'
                    elif action != 'open' and app.id not in planned_targets:
                        raise ValueError(f'{name} has no matching open window')
                    elif action != 'open':
                        target = planned_targets[app.id]
                        command = replace(command, app=None)
                    else:
                        target = None
                elif not target:
                    raise ValueError('There is no current window')
                if target in closed_targets:
                    raise ValueError('That window is already being closed earlier in this request')
                label += ' ' + name
            elif action == 'folder':
                label = 'Open ' + str(command.folder) + ' folder'
            elif action == 'switch':
                label = 'Switch'
            elif action == 'volume':
                label = ('Change volume by ' if command.relative else 'Set volume to ') + str(command.value) + '%'
            elif action in EXTRAS:
                label = EXTRAS[action][0]
            elif action in SETTINGS:
                label = SETTINGS[action]
            elif action in PANELS:
                label = PANELS[action]
                target = resolve_panel(action)
            if command.workspace is not None:
                label += ' on workspace ' + str(command.workspace)
            if not command.focus:
                label += ' in the background'
            if action not in EXTRAS and action not in SETTINGS and action not in PANELS and not action.startswith('workspace_'):
                desktop.validate(command)
            if action in {'fullscreen', 'restore', 'maximize', 'float', 'tile'}:
                # Layout setters do not focus/place on their own. Honor a named
                # window's foreground request and any destination explicitly.
                if ((app or command.reference == 'previous') and command.focus) or command.workspace is not None:
                    placement = replace(command, action='focus')
                    steps.append(Step(placement, target, 'Focus ' + name +
                        (f' on workspace {command.workspace}' if command.workspace is not None else '')))
                    command = replace(command, workspace=None)
            steps.append(Step(command, target, label))
            if action in WINDOW_ACTIONS | {'open', 'folder'}:
                if action == 'close':
                    closed_targets.append(target)
                    previous = None
                else:
                    previous = ResultRef(len(steps)-1) if target is None or action == 'folder' else target
                    previous_name = name if action != 'folder' else str(command.folder) + ' folder'
                    if app:
                        planned_targets[app.id] = previous
        return Plan(steps)

    def execute(self, plan):
        started = time.monotonic()
        completed = []
        targets = {}
        for index, step in enumerate(plan.steps):
            try:
                c = step.command
                target = step.target
                if isinstance(target, ResultRef):
                    target = targets.get(target.step)
                    if target is None:
                        raise RuntimeError('The earlier action did not produce a usable window')
                if c.action in PANELS:
                    result = execute_panel(c.action, target)
                elif c.action in SETTINGS:
                    result = execute_setting(c.action)
                elif c.action == 'voice.settings':
                    from panel_backend import open_panel
                    open_panel()
                    result = {'ok':True, 'message':'Voicebind settings opened in the bar', 'verified':True}
                elif c.action in EXTRAS:
                    interactive = c.action in {'screenshot.region', 'screenshot.window', 'capture.text',
                        'record.toggle', 'theme.picker', 'menu.clipboard', 'menu.emoji',
                        'menu.keys', 'menu.main', 'audio.output', 'network.speed'}
                    if not interactive:
                        process = subprocess.run(EXTRAS[c.action][1], capture_output=True,
                                                 text=True, timeout=4)
                        if process.returncode or process.stdout.strip() == 'unhandled':
                            raise RuntimeError(process.stderr.strip() or 'No application handled that command')
                        result = {'ok':True, 'message':step.label, 'verified':False}
                        completed.append(result)
                        continue
                    # Some desktop pickers intentionally outlive the command.
                    # Report launched, never claim a final state for these.
                    proc = subprocess.Popen(EXTRAS[c.action][1], stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
                    try:
                        code = proc.wait(timeout=.12)
                        if code:
                            raise RuntimeError(f'Command exited with status {code}')
                        result = {'ok':True, 'message':step.label, 'verified':False}
                    except subprocess.TimeoutExpired:
                        result = {'ok':True, 'message':'Started: ' + step.label, 'verified':False}
                elif c.action.startswith('workspace_'):
                    selector = {'workspace_next':'e+1', 'workspace_previous':'e-1', 'workspace_back':'previous'}[c.action]
                    self.desktop.hypr.dispatch('hl.dsp.focus({ workspace = "' + selector + '" })')
                    result = {'ok':True, 'message':'Workspace ' + str(self.desktop.hypr.query('activeworkspace')['id'])}
                else:
                    result = self.desktop.execute(c, target)
                if not result.get('ok'):
                    raise RuntimeError(result.get('message', 'Action failed'))
                if result.get('window'):
                    # Capture identity returned by the action, never whatever is focused now.
                    targets[index] = WindowTarget(result['window'], result['stable_id']) if result.get('stable_id') is not None else result['window']
                completed.append(result)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                return {'ok':False, 'message':f'Step {index + 1}: {error}', 'completed':completed,
                        'action_ms':round((time.monotonic()-started)*1000,1)}
        message = completed[-1]['message'] if completed else 'Nothing to do'
        if len(completed) > 1:
            message = f'Completed {len(completed)} steps. ' + message
        return {'ok':True, 'message':message,
                'completed':completed, 'action_ms':round((time.monotonic()-started)*1000,1)}


class Confirmation:
    """One expiring plan. A new request replaces it; confirmation is one-shot."""
    def __init__(self, seconds=8, clock=time.monotonic):
        self.seconds, self.clock = seconds, clock
        self.pending = None
        self.deadline = 0

    def set(self, plan):
        self.pending, self.deadline = plan, self.clock() + self.seconds

    def cancel(self):
        old, self.pending = self.pending, None
        return old

    def expire(self):
        if self.pending and self.clock() >= self.deadline:
            return self.cancel()
        return None

    def take(self):
        self.expire()
        return self.cancel()


class Question:
    """A missing value, with all affected windows already bound. Never executable."""
    def __init__(self, proposal, planner, active, clock=time.monotonic):
        self.slot, self.deadline = proposal.slot, clock() + 8
        c = proposal.commands[0]
        # Placeholder values are used only to validate and bind the plan.
        self.template = planner.prepare([replace(c, workspace=1) if self.slot == 'workspace' else c], active)

    def answer(self, text):
        if self.slot == 'workspace':
            raw = re.sub(r'^(?:to )?(?:(?:workspace|desktop) )?', '', text)
            value = int(raw) if raw.isdigit() else {w:n for n,w in NUMBERS.items()}.get(raw)
            if value is None or not 1 <= value <= 20:
                return None
            return Plan([Step(replace(s.command, workspace=value), s.target,
                              re.sub(r'on workspace 1\b', 'on workspace ' + str(value), s.label))
                         for s in self.template.steps])
        modes = {'full screen':'fullscreen', 'fullscreen':'fullscreen', 'maximized':'maximize',
                 'maximise':'maximize', 'maximize':'maximize', 'maximised':'maximize',
                 'floating':'float', 'float':'float', 'tiled':'tile', 'tile':'tile', 'restore':'restore'}
        mode = modes.get(re.sub(r'^make it ', '', text))
        if mode is None:
            return None
        return Plan([Step(replace(s.command, action=mode), s.target,
                          mode.capitalize() + s.label.removeprefix('Fullscreen'))
                     if s.command.action == 'fullscreen' else s for s in self.template.steps])
