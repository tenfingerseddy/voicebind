"""Capture coordination: hold-to-talk, early wake indication and bounded decode."""
from dataclasses import dataclass
from collections import deque
import queue
import threading
import time
import numpy as np

from control import Control
from desktop_core.desktop import capture_window
from vt import RATE, WAKE, strip_wake, whisper
from dictation import Dictation, control_suffix
from dictation_target import parse_start, prepare_destination
from personalization import expand_phrase

PTT_HINT = 'Desktop voice commands: open, move, close, restore, fullscreen, workspace, files, browser, notifications, dictate, start dictation.'
DICTATION_HINT = f'Dictated prose and voice controls: {WAKE} stop, {WAKE} finish, {WAKE} finish dictation, {WAKE} stop dictation, {WAKE} cancel dictation.'


@dataclass
class AudioJob:
    pcm: object
    end: float
    target: object
    token: int
    epoch: int
    activation: str | None = None
    preview: bool = False


class Listener:
    def __init__(self, engine, planner, gate, config, log, stop, control=None, decode=whisper, clock=time.monotonic):
        self.engine, self.planner, self.gate = engine, planner, gate
        self.log, self.stop, self.decode, self.clock = log, stop, decode, clock
        self.control = control or Control(indicator=config['indicator'])
        self.control.update(wake_enabled=config['voice']['wake_enabled'])
        self.preview_frames = config['recognition']['wake_preview_ms']//20
        self.work = queue.Queue(maxsize=2); self.previews = queue.Queue(maxsize=1)
        self.lock = threading.RLock()
        self.token = self.epoch = 0
        self.ptt = False; self.frames = []; self.voiced = 0
        self.wake_enabled = config['voice']['wake_enabled']
        self.armed_until = 0; self.armed_target = None
        self.target = None; self.activation = None
        self.preview_count = 0; self.wake_seen = False
        self.last_level = 0; self.ui_deadline = 0
        self.waveform = deque(maxlen=160)
        self.last_wave = 0
        self.last_health = 0
        self.clear_pending = False
        self.dictation_tail = deque(maxlen=300)
        self.dictation_final = None
        self.dictation = Dictation(planner.desktop.hypr,engine.feedback,self.event,clock=clock)

    def event(self, name, **values):
        self.log({'event':name, 't':time.time(), **values})

    def show(self, phase, token=None, seconds=0, **extra):
        with self.lock:
            if token is not None and token != self.token: return
            self.ui_deadline = self.clock()+seconds if seconds else 0
            dictating = phase == 'dictating' or (phase == 'processing' and self.dictation.active)
            if phase == 'dictating' or not dictating:
                self.waveform.clear()
                extra['waveform'] = []
            extra.setdefault('prompt', '')
            self.control.update(phase=phase, level=0.0, dictation=bool(dictating), **extra)

    def current_target(self):
        try: return capture_window(self.planner.desktop.hypr.query('activewindow'))
        except Exception: return ''

    def reset_gate(self):
        noise = self.gate.noise
        self.gate = type(self.gate)(self.gate.hang*20)
        self.gate.noise = noise
        self.dictation_tail.clear()

    def command(self, action):
        with self.lock:
            if self.dictation.active and action == 'press':
                self.finish_dictation(); return
            if self.dictation.active and action in {'cancel','toggle-wake'}:
                self.dictation.cancel()
                self.dictation_final = None
            if action == 'press':
                if self.ptt: return
                self.epoch += 1; self.token += 1
                self.ptt = True; self.frames = []; self.voiced = 0
                self.first_voice = self.last_voice = None
                self.target = self.current_target(); self.armed_until = 0
                self.reset_gate(); self.show('listening')
                self.event('ptt_started')
            elif action == 'release':
                if not self.ptt: return
                self.ptt = False
                if self.voiced >= 4:
                    frames = self.frames[max(0,self.first_voice-12):self.last_voice+11]
                    pcm = np.concatenate([*frames, np.zeros(RATE//8, dtype=np.int16)])
                    self.enqueue(AudioJob(pcm,self.clock(),self.target,self.token,self.epoch,'ptt'))
                    self.show('processing')
                else: self.show('idle')
                self.frames = []; self.reset_gate()
                self.event('ptt_released')
            elif action in {'cancel','toggle-wake'}:
                self.epoch += 1; self.token += 1; self.ptt = False; self.frames = []
                self.armed_until = 0; self.clear_pending = True; self.reset_gate()
                if action == 'toggle-wake':
                    self.wake_enabled = not self.wake_enabled
                    self.control.update(wake_enabled=self.wake_enabled)
                    self.engine.feedback('Wake listening '+('on' if self.wake_enabled else 'off')+'. F10 still works.')
                self.show('idle'); self.event(action, wake_enabled=self.wake_enabled)

    def finish_dictation(self, voice=False):
        with self.lock:
            pcm = self.dictation.finish(voice=voice)
            if pcm is None: return
            # A dedicated slot prioritises completion over obsolete cue probes.
            self.epoch += 1
            self.dictation_final = AudioJob(pcm,self.clock(),self.target,self.token,self.epoch,'dictation-final')
            self.show('processing')

    def enqueue(self, job):
        try: self.work.put_nowait(job)
        except queue.Full:
            self.event('busy_audio_discarded'); self.show('error',job.token,seconds=1)

    def frame(self, frame, now):
        while True:
            try: self.command(self.control.events.get_nowait())
            except queue.Empty: break
        with self.lock:
            if self.ui_deadline and now >= self.ui_deadline:
                self.armed_until = 0; self.show('idle')
            rms = float(np.sqrt(np.mean(frame.astype(np.float32)**2)))
            if now-self.last_health >= .25:
                self.last_health = now
                self.control.update(emit=False,audio_rms=round(rms,1),noise_rms=round(self.gate.noise,1),
                                    speech_threshold=round(self.gate.threshold,1),speech_active=self.gate.capturing,
                                    overlong=self.gate.overlong,last_audio_at=time.time())
            if self.dictation.phase == 'recording':
                # Two signed peaks per 20 ms frame: a 1.6-second audio envelope,
                # not raw PCM or a fabricated sine wave. Publish at <=25 Hz.
                def peak(value):
                    magnitude = max(0,abs(int(value))-100)/32768
                    return round(min(1.0,magnitude)**.5*100)*(1 if value >= 0 else -1)
                self.waveform.extend((peak(frame.min()),peak(frame.max())))
                if now-self.last_wave >= .04:
                    self.last_wave = now
                    self.control.update(waveform=list(self.waveform),level=min(1.0,max(0.0,(rms-120)/3500)))
            elif now-self.last_level >= .08 and self.control.snapshot()['phase'] == 'listening':
                self.last_level = now
                self.control.update(level=min(1.0,max(0.0,(rms-120)/3500)))
            if self.ptt:
                self.frames.append(frame); self.voiced += rms > 180
                if rms > 180:
                    if self.first_voice is None: self.first_voice = len(self.frames)-1
                    self.last_voice = len(self.frames)-1
                if len(self.frames) >= 1000:
                    self.command('cancel')
                    self.event('recording_limit')
                    self.engine.feedback('Recording reached 20 seconds. Please use a shorter request.',True)
                return
            if self.dictation.active:
                if self.dictation.phase == 'recording':
                    self.dictation.feed(frame,rms)
                    self.dictation_tail.append(frame)
                    _,segment = self.gate.feed(frame,now)
                    if segment:
                        # Only the recent tail is needed to hear an ending cue.
                        # This also works after >20 seconds of continuous prose,
                        # when the ordinary command gate discards long speech.
                        pcm = segment[0][-RATE*6:] if segment[0] is not None else np.concatenate(self.dictation_tail)
                        self.dictation_tail.clear()
                        self.enqueue(AudioJob(pcm,segment[1],self.target,self.token,self.epoch,'dictation'))
                return
            if not self.wake_enabled: return
            began, segment = self.gate.feed(frame,now)
            if began:
                self.token += 1; self.preview_count = 0; self.wake_seen = False
                self.activation = 'wake-followup' if now < self.armed_until else None
                self.target = self.armed_target if self.activation else self.current_target()
                self.armed_until = 0
                if self.activation: self.show('listening')
            if self.gate.capturing and not self.gate.overlong and not self.activation and not self.wake_seen:
                threshold = self.preview_frames + self.preview_count*30
                if self.preview_count < 2 and len(self.gate.speech) >= threshold:
                    pcm = np.concatenate([*self.gate.speech,np.zeros(RATE//8,dtype=np.int16)])
                    try:
                        self.previews.put_nowait(AudioJob(pcm,now,self.target,self.token,self.epoch,preview=True))
                        self.preview_count += 1
                    except queue.Full: pass
            if segment:
                pcm,end = segment
                if pcm is None:
                    self.event('overlong_audio_discarded'); self.show('idle')
                else:
                    self.enqueue(AudioJob(pcm,end,self.target,self.token,self.epoch,self.activation))
                    if self.wake_seen or self.activation: self.show('processing')

    def valid(self, job):
        return not self.stop.is_set() and job.epoch == self.epoch

    def process_job(self, job):
        if not self.valid(job): return
        if job.activation == 'dictation-final':
            if self.dictation.phase != 'processing': return
            # Decode outside the capture lock so cancellation works during ASR.
            ms,text = self.decode(job.pcm,prompt=DICTATION_HINT,timeout=15) if len(job.pcm) else (0,'')
            with self.lock:
                if not self.valid(job) or self.dictation.phase != 'processing': return
                self.dictation.complete(text,ms)
                self.epoch += 1; self.reset_gate(); self.show('idle')
            return
        if job.activation == 'dictation':
            if self.dictation.phase != 'recording': return
            _,text = self.decode(job.pcm,prompt=DICTATION_HINT)
            if not self.valid(job) or self.dictation.phase != 'recording': return
            cue = control_suffix(text)
            if cue == 'cancel': self.command('cancel')
            elif cue == 'finish':
                self.finish_dictation(voice=True)
            return
        if job.preview:
            with self.lock:
                if job.token != self.token or self.wake_seen or not self.gate.capturing: return
            _, text = self.decode(job.pcm)
            with self.lock:
                if self.valid(job) and job.token == self.token and strip_wake(text)[0]:
                    self.wake_seen = True
                    self.show('listening' if self.gate.capturing else 'processing',job.token)
                    self.event('wake_detected')
            return
        if self.clock()-job.end > 2:
            self.event('stale_audio_discarded'); self.show('idle',job.token); return
        ms,text = self.decode(job.pcm,prompt=PTT_HINT) if job.activation else self.decode(job.pcm)
        if not self.valid(job): return
        addressed,rest = strip_wake(text)
        if not text or (not addressed and not job.activation):
            self.show('idle',job.token); return
        destination = parse_start(expand_phrase(rest, self.engine.router.config),self.planner.catalog)
        if destination is not None:
            if not self.engine.live:
                self.engine.feedback('Dry run: start dictation')
                self.event('dictation_dry_run'); self.show('idle',job.token); return
            self.event('dictation_requested',phrase=rest)
            target = job.target
            if destination.app:
                self.show('processing',job.token)
                target = prepare_destination(destination,self.planner,job.target,valid=lambda:self.valid(job))
                if target is None: return
            with self.lock:
                if not self.valid(job): return
                if destination.app: self.dictation.start(target,field=destination.field)
                else: self.dictation.start(target)
                self.epoch += 1; self.armed_until = 0; self.reset_gate()
                self.clear_pending = True
                self.show('dictating')
            return
        if addressed and not rest and job.activation != 'ptt':
            with self.lock:
                if job.token == self.token:
                    self.armed_target = job.target; self.armed_until = self.clock()+5
                    self.show('listening',job.token,seconds=5)
                    self.event('wake_armed')
            return
        self.show('processing',job.token)
        result = self.engine.process(text,job.target,ms,job.end,activation=job.activation,valid=lambda:self.valid(job))
        if result:
            self.event('audio_timing',audio_ms=round(len(job.pcm)/RATE*1000),endpoint_ms=0 if job.activation=='ptt' else self.gate.hang*20,whisper_ms=round(ms,1))
            phase = 'waiting' if result['verdict'] in {'confirm','clarify'} else ('error' if result['verdict'] in {'error','drop'} or result.get('result',{}).get('ok') is False else 'idle')
            self.show(phase,job.token,seconds=8 if phase=='waiting' else 1 if phase=='error' else 0,
                      prompt=(result.get('prompt') or result.get('reason', '')) if phase=='waiting' else '')
        else: self.show('idle',job.token)

    def worker(self):
        while not self.stop.is_set():
            with self.lock:
                if self.dictation.due: self.finish_dictation()
                job, self.dictation_final = self.dictation_final, None
            if self.clear_pending:
                with self.engine.lock:
                    self.engine.confirmation.cancel(); self.engine.question = None
                self.clear_pending = False
            if job is None:
                try: job = self.work.get(timeout=.05)
                except queue.Empty:
                    try: job = self.previews.get_nowait()
                    except queue.Empty:
                        self.engine.tick(); continue
            try: self.process_job(job)
            except Exception as error:
                if not job.preview:
                    if job.activation == 'dictation-final':
                        with self.lock:
                            if self.valid(job):
                                self.dictation.fail(error); self.epoch += 1
                                self.reset_gate(); self.show('error',job.token,seconds=2)
                        continue
                    self.engine.feedback('Voice recognition error: '+str(error),True)
                    self.event('recognition_error',reason=str(error)); self.show('error',job.token,seconds=1)

    def run(self, proc):
        self.control.start()
        thread = threading.Thread(target=self.worker,daemon=True); thread.start()
        try:
            while not self.stop.is_set():
                raw = proc.stdout.read(640)
                if len(raw) != 640:
                    if not self.stop.is_set(): raise RuntimeError('Microphone capture stopped')
                    break
                self.frame(np.frombuffer(raw,dtype=np.int16),self.clock())
        finally:
            self.stop.set()
            with self.lock:
                if self.dictation.active: self.dictation.cancel()
            self.show('idle'); self.control.close(); thread.join(timeout=6)
