"""Private local controls and an event-driven, credential-free UI status stream."""
import json
import os
from pathlib import Path
import queue
import selectors
import socket
import struct
import threading


def runtime_dir():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))/'jev-voice'


class Control:
    def __init__(self, path=None, indicator=None):
        self.path = Path(path or runtime_dir()/'control.sock')
        self.events = queue.Queue(maxsize=16)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.state = {'phase':'idle','level':0.0,'waveform':[],'dictation':False,
                      'wake_enabled':True, **(indicator or {})}
        self.thread = None

    def update(self, emit=True, **values):
        with self.lock:
            self.state = {**self.state, **values}
        if emit: self.changed.set()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.path)); os.chmod(self.path, 0o600)
        self.server.listen(8); self.server.setblocking(False)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        clients = {}
        poll = selectors.DefaultSelector(); poll.register(self.server, selectors.EVENT_READ)
        def close(client):
            try: poll.unregister(client)
            except (KeyError, ValueError): pass
            clients.pop(client, None); client.close()
        def send(client, value):
            try:
                raw = (json.dumps(value, separators=(',', ':'))+'\n').encode()
                if client.send(raw) != len(raw): close(client)
            except OSError: close(client)
        try:
            while not self.stop.is_set():
                for selected, _ in poll.select(.04):
                    client = selected.fileobj
                    if client is self.server:
                        conn, _ = self.server.accept()
                        _, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if uid != os.getuid() or len(clients) >= 16:
                            conn.close(); continue
                        conn.setblocking(False); clients[conn] = {'data':b'', 'subscribe':False}
                        poll.register(conn, selectors.EVENT_READ)
                        continue
                    try: data = client.recv(2048)
                    except OSError: data = b''
                    if not data:
                        close(client); continue
                    item = clients[client]; item['data'] += data
                    if len(item['data']) > 1024:
                        close(client); continue
                    if b'\n' not in item['data']: continue
                    try: action = json.loads(item['data'].split(b'\n')[0]).get('action')
                    except (ValueError, AttributeError): action = None
                    if not isinstance(action, str): action = None
                    item['data'] = b''
                    if action == 'subscribe':
                        item['subscribe'] = True; send(client, self.snapshot())
                    elif action == 'status':
                        send(client, self.snapshot()); close(client)
                    elif action in {'press','release','cancel','toggle-wake'}:
                        try:
                            self.events.put_nowait(action)
                            send(client, {'ok':True})
                        except queue.Full:
                            send(client, {'ok':False,'message':'Voice control is busy'})
                        close(client)
                    else:
                        send(client, {'ok':False,'message':'Unknown control'}); close(client)
                if self.changed.is_set():
                    self.changed.clear(); state = self.snapshot()
                    for client, item in list(clients.items()):
                        if item['subscribe']: send(client, state)
        finally:
            for client in list(clients): close(client)
            poll.close(); self.server.close(); self.path.unlink(missing_ok=True)

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=1)


def request(action, path=None):
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(2)
        conn.connect(str(path or runtime_dir()/'control.sock'))
        conn.sendall((json.dumps({'action':action})+'\n').encode())
        data = b''
        while b'\n' not in data:
            chunk = conn.recv(4096)
            if not chunk: raise RuntimeError('Voice service disconnected')
            data += chunk
            if len(data)>8192: raise RuntimeError('Invalid voice service reply')
        return json.loads(data.split(b'\n')[0])


if __name__ == '__main__':
    import sys
    try:
        result = request(sys.argv[1])
        if result.get('ok') is False: sys.exit(result['message'])
        if sys.argv[1] == 'status': print(json.dumps(result, indent=2))
    except (OSError, RuntimeError) as error:
        sys.exit('Voicebind is unavailable: ' + str(error))
