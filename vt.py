#!/usr/bin/env python
"""Voicebind transport and wake matching. See daemon.py for the live pipeline."""
from __future__ import annotations
import http.client
import json
import os
import re
import socket
import threading
import time
import wave
from io import BytesIO
from pathlib import Path

import numpy as np
from configuration import load_config, load_key

HERE = Path(__file__).resolve().parent
RATE = 16000
WAKE = os.environ.get('VT_WAKE', load_config()['voice']['wake_phrase'])
WHISPER = ('127.0.0.1', 8178)


class Jev:
    """Warm HTTP/TLS connection; bounded retry of inference, never execution."""
    def __init__(self, key, timeout=3):
        self.key, self.timeout, self.conn = key, timeout, None
        self.lock = threading.Lock()

    def connect(self):
        self.close()
        self.conn = http.client.HTTPSConnection('api.typesafe.ai', timeout=self.timeout)
        self.conn.connect()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def ask(self, transcript, state_extra, questions):
        if not self.key:
            raise RuntimeError('TYPESAFE_API_KEY is not configured')
        body = json.dumps({'model':os.environ.get('VT_JEV_MODEL', 'jev-1.13.0'),
                           'state':{'transcript':transcript, **state_extra}, 'questions':questions})
        headers = {'Authorization':'Bearer ' + self.key, 'Content-Type':'application/json'}
        started = time.perf_counter()
        with self.lock:
            for attempt in range(2):
                try:
                    if not self.conn:
                        self.connect()
                    self.conn.request('POST', '/v1/systemone', body, headers)
                    response = self.conn.getresponse()
                    data = response.read()
                    if response.status != 200:
                        raise RuntimeError(f'Jev HTTP {response.status}; try a direct command')
                    payload = json.loads(data)
                    if not isinstance(payload.get('answers'), dict):
                        raise ValueError('Jev returned no answers')
                    return (time.perf_counter()-started)*1000, payload
                except (OSError, http.client.HTTPException):
                    self.close()
                    if attempt or time.perf_counter()-started > self.timeout:
                        raise RuntimeError('Jev connection timed out; try a direct command') from None


def to_wav(pcm):
    buf = BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def whisper(pcm, prompt=None, timeout=5):
    boundary = '----jevvoice'
    parts = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="voice.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode() + to_wav(pcm)
    fields = [('response_format','text'), ('temperature','0.0')]
    if prompt:
        fields.append(('prompt', prompt))
    for name, value in fields:
        parts += (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}').encode()
    parts += f'\r\n--{boundary}--\r\n'.encode()
    conn = http.client.HTTPConnection(*WHISPER, timeout=timeout)
    started = time.perf_counter()
    try:
        conn.request('POST', '/inference', parts, {'Content-Type':f'multipart/form-data; boundary={boundary}'})
        response = conn.getresponse()
        text = response.read().decode(errors='replace').strip()
        if response.status != 200:
            raise RuntimeError(f'Whisper HTTP {response.status}')
        if re.fullmatch(r'[\s.\[\]()*_-]*|\(.*\)|\[.*\]', text):
            text = ''
        if prompt:
            text = text.lstrip(' ,')
        return (time.perf_counter()-started)*1000, text
    finally:
        conn.close()


def strip_wake(text):
    # Only measured exact spellings; fuzzy similarity used to wake on 'commuter'.
    # Preserve the rest verbatim so punctuation cannot hide negation or clauses.
    wake = re.escape(WAKE)
    match = re.match(rf'^\s*(?:(?:hey|ok|okay)\s+)?{wake}\b[\s,.:!?-]*', text, re.I)
    return (True, text[match.end():].strip()) if match else (False, text)


if __name__ == '__main__':
    from daemon import main
    main()
