"""Resident Whisper -> local grammar / Jev -> verified plan, with real confirmation."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import sys
import threading
import time
import tomllib
import wave

import numpy as np

from desktop_core.apps import AppCatalog
from desktop_core.desktop import capture_window
from plan import Planner, Confirmation, Question
from router import Router, Proposal, normalize
from vt import HERE, Jev, RATE, WAKE, load_key, strip_wake, whisper
from configuration import load_config
from bookmarks import Bookmarks, BookmarkPlan
from personalization import expand_phrase

RUNTIME = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'jev-voice'


def quiet_feedback(message, urgent=False):
    """The indicator shows activity; results and errors belong in History."""
    pass


def configuration():
    return load_config()


def components(jev=None, hypr=None):
    cfg = configuration()
    aliases = dict(cfg.get('apps', {}))
    if 'files' in aliases:
        aliases.setdefault('file manager', aliases['files'])
    catalog = AppCatalog(alias_overrides=aliases)
    return Router(catalog, cfg, jev), Planner(catalog, cfg, hypr)


class Engine:
    def __init__(self, router, planner, live=False, feedback=quiet_feedback, log=None, clock=time.monotonic, bookmarks=None):
        self.router, self.planner, self.live = router, planner, live
        self.feedback, self.log, self.clock = feedback, log, clock
        self.bookmarks = bookmarks or Bookmarks(planner.desktop)
        self.confirmation = Confirmation(clock=clock)
        self.question = None
        self.enabled = True
        self.lock = threading.RLock()

    def emit(self, result):
        if self.log:
            self.log(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result

    def tick(self):
        with self.lock:
            if self.question and self.clock() >= self.question.deadline:
                self.question = None
                self.feedback('Question expired. Nothing ran.')
                self.emit({'t':time.time(), 'event':'question_expired'})
            if self.confirmation.expire():
                self.feedback('Confirmation expired. Nothing ran.')
                self.emit({'t':time.time(), 'event':'confirmation_expired'})

    def execute(self, plan, valid=lambda:True):
        if not self.enabled:
            return {'ok':False, 'message':'Listener is stopping'}
        if not self.live:
            return {'ok':True, 'message':'Dry run: ' + plan.label, 'dry_run':True}
        if isinstance(plan, BookmarkPlan):
            try: return self.bookmarks.execute(plan, valid)
            except (ValueError, RuntimeError, OSError) as error:
                return {'ok':False, 'message':str(error)}
        return self.planner.execute(plan)

    def process(self, text, active=None, asr_ms=0, speech_end=None, activation=None, valid=lambda:True):
        with self.lock:
            addressed, rest = strip_wake(text)
            if activation in {'ptt', 'wake-followup'}:
                addressed = True
            if not valid():
                return None
            if not addressed or not rest:
                if addressed:
                    self.feedback(f'Ready. Say "{WAKE}" followed by a command.')
                return None
            started = self.clock()
            self.tick()
            record = {'t':time.time(), 'text':text, 'activation':activation or 'wake', 'ms':{'whisper':round(asr_ms,1)}}
            rest = expand_phrase(rest, self.router.config)
            phrase = normalize(rest)
            if phrase in {'yes', 'do it', 'confirm', 'go ahead', 'yes do it'}:
                self.question = None
                plan = self.confirmation.take()
                if plan:
                    result = self.execute(plan, valid)
                    record.update(verdict='confirmed', plan=plan.label, result=result)
                    self.feedback(result['message'], not result['ok'])
                else:
                    record.update(verdict='drop', reason='There is nothing waiting for confirmation')
                    self.feedback(record['reason'])
            elif phrase in {'no', 'cancel', 'never mind', 'nevermind', 'stop'}:
                self.confirmation.cancel()
                self.question = None
                record.update(verdict='cancelled')
                self.feedback('Cancelled')
            else:
                # A fresh instruction always invalidates an earlier proposal,
                # including if the new instruction fails to parse.
                self.confirmation.cancel()
                try:
                    previous_question, self.question = self.question, None
                    bookmark = self.bookmarks.prepare(rest, self.router.config if isinstance(self.router.config, dict) else {})
                    answered = previous_question.answer(phrase) if previous_question and not bookmark else None
                    proposal = (Proposal(route='bookmark', verdict='confirm' if bookmark.confirm else 'act') if bookmark else
                                Proposal(commands=[s.command for s in answered.steps], reason='Answered the missing value') if answered else self.router.decide(rest))
                    record.update(proposal.serial())
                    record['ms']['whisper'] = round(asr_ms,1)
                    if not valid():
                        record.update(verdict='cancelled', reason='Recording cancelled')
                        return self.emit(record)
                    if proposal.verdict == 'drop':
                        self.feedback(proposal.reason or 'Please rephrase that command')
                    elif proposal.verdict == 'clarify':
                        self.question = Question(proposal, self.planner, active, self.clock)
                        record['prompt'] = proposal.reason + f' Or say "{WAKE} cancel". Expires in 8 seconds.'
                        self.feedback(record['prompt'], True)
                    else:
                        plan = bookmark or answered or self.planner.prepare(proposal.commands, active)
                        record['plan'] = plan.label
                        if not valid():
                            record.update(verdict='cancelled', reason='Recording cancelled')
                            return self.emit(record)
                        if speech_end is not None and self.clock() - speech_end > 5:
                            record.update(verdict='drop', reason='That command became stale; please repeat it')
                            self.feedback(record['reason'])
                        elif proposal.verdict == 'confirm':
                            self.confirmation.set(plan)
                            record['prompt'] = f'{plan.label}? Say "{WAKE} yes" or "{WAKE} cancel" within 8 seconds.'
                            self.feedback(record['prompt'], True)
                        else:
                            result = self.execute(plan, valid)
                            record['result'] = result
                            self.feedback(result['message'], not result['ok'])
                except Exception as error:
                    # A failed network/decode/dispatch must not kill the worker.
                    record.update(verdict='error', reason=str(error))
                    self.feedback(str(error), True)
            record['ms']['decision_and_action'] = round((self.clock()-started)*1000,1)
            if speech_end is not None:
                key = 'release_to_result' if activation == 'ptt' else 'end_of_speech_to_result'
                record['ms'][key] = round((self.clock()-speech_end)*1000,1)
            return self.emit(record)


class Segmenter:
    """Adaptive energy gate; bounded audio, no repeated frame or stale backlog."""
    def __init__(self, silence_ms=560):
        self.hang = max(1, silence_ms // 20)
        self.ring = deque(maxlen=25)
        self.noise = 120.0
        self.speech = []
        self.loud = self.quiet = 0
        self.capturing = False
        self.last_speech = 0
        self.overlong = False
        self.levels = deque(maxlen=100)
        self.overlong_frames = 0

    @property
    def threshold(self):
        return max(self.noise*2.0, 180)

    def calibrate(self, levels):
        # A low percentile tolerates isolated clicks or a brief voiced onset.
        self.noise = max(60., float(np.percentile(levels, 20)))

    def feed(self, frame, now):
        rms = float(np.sqrt(np.mean(frame.astype(np.float32)**2)))
        self.levels.append(rms)
        loud = rms > self.threshold
        began = False
        if not self.capturing:
            self.ring.append(frame)
            if not loud:
                self.noise = .99*self.noise + .01*rms
            self.loud = self.loud + 1 if loud else 0
            if self.loud >= 3:
                self.capturing = began = True
                self.speech = list(self.ring)
                self.quiet = 0
        elif not self.overlong:
            self.speech.append(frame)
        if self.capturing:
            if not self.overlong: self.quiet = 0 if loud else self.quiet + 1
            if loud:
                self.last_speech = now
            if len(self.speech) >= 1000:
                # Do not execute the first half of an overlong utterance.
                self.overlong = True
                self.overlong_frames = 0
                self.speech = []
                # Noise can hold the gate open forever on a different microphone.
                # Recalibrate only after this segment has become non-executable.
                self.calibrate(self.levels)
            if self.overlong:
                self.overlong_frames += 1
                if self.overlong_frames % 50 == 0: self.calibrate(self.levels)
                loud = rms > self.threshold
                self.quiet = 0 if loud else self.quiet + 1
            if self.quiet >= self.hang:
                audio = np.concatenate(self.speech) if self.speech and not self.overlong else None
                end = self.last_speech
                self.capturing = self.overlong = False
                self.speech = []
                self.ring.clear()
                self.loud = self.quiet = 0
                return began, (audio, end)
        return began, None


def calibrate_microphone(proc, gate, seconds=.5):
    """Measure the existing capture before announcing readiness; keep no audio."""
    deadline = time.monotonic()+3
    target = round(seconds*50)
    levels = []; buffered = bytearray()
    while len(levels)<target:
        if time.monotonic()>=deadline or proc.poll() is not None:
            raise RuntimeError('Microphone capture is not delivering audio')
        if not select.select([proc.stdout], [], [], .1)[0]: continue
        chunk = os.read(proc.stdout.fileno(), (target-len(levels))*640-len(buffered))
        if not chunk: raise RuntimeError('Microphone capture stopped during calibration')
        buffered.extend(chunk)
        while len(buffered)>=640:
            frame = np.frombuffer(bytes(buffered[:640]), dtype=np.int16)
            del buffered[:640]
            levels.append(float(np.sqrt(np.mean(frame.astype(np.float32)**2))))
    gate.calibrate(levels)
    return {'noise_rms':round(gate.noise,1), 'speech_threshold':round(gate.threshold,1)}


def load_audio(path):
    with wave.open(str(path), 'rb') as f:
        if f.getnchannels() != 1 or f.getframerate() != RATE or f.getsampwidth() != 2:
            raise ValueError('Use a mono 16 kHz, 16-bit WAV')
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)


def main():
    ap = argparse.ArgumentParser(description='Voicebind: say computer, then a desktop command')
    ap.add_argument('--live', action='store_true', help='execute plans (default: dry run)')
    ap.add_argument('--text', help='process one transcript including the wake word')
    ap.add_argument('--wav', help='process one mono 16 kHz WAV through the live decision path')
    ap.add_argument('--source', help='PulseAudio source name')
    ap.add_argument('--silence-ms', type=int, default=load_config()['recognition']['end_silence_ms'])
    ap.add_argument('--no-compare', action='store_true', help=argparse.SUPPRESS)
    args = ap.parse_args()
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = None
    if not args.text and not args.wav:
        lock = (RUNTIME / 'listener.lock').open('w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('Voicebind is already listening')
        active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'omarchy-voice.service']).returncode == 0
        if active:
            sys.exit('Legacy Voicebind is still running. Use voicebind start to switch to the new version.')
    jev = Jev(load_key())
    if jev.key:
        try:
            jev.connect()
        except OSError:
            print('Jev unavailable at startup; direct commands remain available', file=sys.stderr)
    router, planner = components(jev)
    logs = HERE / 'sessions'
    logs.mkdir(exist_ok=True, mode=0o700)
    session = logs / f'{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.jsonl'
    status = {'pid':os.getpid(), 'wake':WAKE, 'live':args.live, 'session':str(session), 'state':'starting'}
    log_lock = threading.Lock()
    def write_log(event):
        with log_lock:
            with session.open('a') as f:
                f.write(json.dumps(event) + '\n')
            if not args.text and not args.wav:
                status['last_event'] = event
                tmp = RUNTIME / 'status.tmp'
                tmp.write_text(json.dumps(status))
                tmp.replace(RUNTIME / 'status.json')
    engine = Engine(router, planner, args.live, log=write_log)
    if args.text:
        engine.process(args.text)
        return
    if args.wav:
        ms, text = whisper(load_audio(args.wav))
        engine.process(text, asr_ms=ms)
        return
    stop = threading.Event()
    def shutdown(*_):
        engine.enabled = False
        stop.set()
        if proc.poll() is None:
            proc.terminate()
    cmd = ['parecord', '--client-name=Voicebind', '--format=s16le', '--rate=16000',
           '--channels=1', '--raw', '--latency-msec=20']
    source = args.source or load_config()['recognition'].get('source', '')
    if source:
        cmd.append('--device=' + source)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    # Advertise readiness only after PulseAudio reports the actual mic holder.
    for _ in range(20):
        if proc.poll() is not None:
            raise RuntimeError('Microphone capture failed to start')
        holders = subprocess.run(['pactl', '-f', 'json', 'list', 'source-outputs'],
                                 capture_output=True, text=True, timeout=2)
        if holders.returncode == 0 and any(
            h.get('properties', {}).get('application.name') == 'Voicebind' and not h.get('corked')
            for h in json.loads(holders.stdout)
        ):
            break
        time.sleep(.05)
    else:
        proc.terminate()
        proc.wait(timeout=2)
        raise RuntimeError('Could not verify microphone ownership')
    from listening import Listener
    gate = Segmenter(args.silence_ms)
    calibration = calibrate_microphone(proc, gate)
    status['state'] = 'listening'
    write_log({'event':'microphone_calibrated', 't':time.time(), **calibration})
    write_log({'event':'ready', 't':time.time(), 'wake':WAKE, 'live':args.live})
    listener = Listener(engine, planner, gate, load_config(), write_log, stop)
    try:
        listener.run(proc)
    finally:
        shutdown()
        proc.wait(timeout=2)
        status['state'] = 'stopped'
        write_log({'event':'stopped', 't':time.time()})
        jev.close()
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
