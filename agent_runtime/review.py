"""Human-only, one-use execution grants; persisted requests are not authorization."""
import difflib
import hashlib
import json
import threading
import time
import uuid
import unicodedata

from .storage import atomic_json, file_lock


class ReviewRequired(RuntimeError):
    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__(f'Operator review required: {request_id}')


class ReviewQueue:
    def __init__(self, directory, timeout=900):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.lock = threading.RLock()
        self.grants = {}  # Only an in-process operator decision can populate this.
        self.denied = set()
        self.viewed = {}

    @staticmethod
    def fingerprint(operation):
        return hashlib.sha256(json.dumps(operation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def requests(self):
        with self.lock:
            requests = [json.loads(p.read_text(encoding='utf-8')) for p in self.directory.glob('*.json')]
            return sorted(requests, key=lambda r: r['created'])

    def get(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('Invalid review ID')
        return json.loads((self.directory / f'{identifier}.json').read_text(encoding='utf-8'))

    def require(self, operation, task_id=None):
        fingerprint = self.fingerprint(operation)
        with self.lock, file_lock(self.directory / 'queue.lock'):
            if fingerprint in self.denied:
                raise PermissionError('Operator rejected this exact operation')
            grant = self.grants.pop(fingerprint, None)
            if grant and grant['expires'] > time.time():
                request = self.get(grant['id'])
                request['status'] = 'consumed'
                atomic_json(self.directory / f"{request['id']}.json", request)
                return
            for request in self.requests():
                if request['fingerprint'] == fingerprint and request['status'] == 'pending' and request['expires'] > time.time():
                    raise ReviewRequired(request['id'])
            identifier = uuid.uuid4().hex
            request = {'id': identifier, 'fingerprint': fingerprint, 'operation': operation,
                       'task_id': task_id, 'created': time.time(), 'expires': time.time() + self.timeout,
                       'status': 'pending'}
            atomic_json(self.directory / f'{identifier}.json', request)
            raise ReviewRequired(identifier)

    def decide(self, identifier, approve):
        """Operator API, intentionally absent from the model tool registry."""
        with self.lock, file_lock(self.directory / 'queue.lock'):
            request = self.get(identifier)
            if request['expires'] <= time.time():
                raise ValueError('Review expired; continue the task to request a new preview')
            if request['status'] not in ('pending', 'approved'):
                raise ValueError('Review already decided or consumed')
            # Recompute rather than trusting a persisted fingerprint.
            fingerprint = self.fingerprint(request['operation'])
            if fingerprint != request['fingerprint'] or self.viewed.get(identifier) != fingerprint:
                raise ValueError('Review data changed; request a fresh preview')
            request['status'] = 'approved' if approve else 'rejected'
            atomic_json(self.directory / f'{identifier}.json', request)
            if approve:
                self.grants[fingerprint] = {'id': identifier, 'expires': request['expires']}
            else:
                self.denied.add(fingerprint)
                self.grants.pop(fingerprint, None)
            return request

    def preview(self, identifier):
        request = self.get(identifier)
        operation = request['operation']
        with self.lock:
            self.viewed[identifier] = self.fingerprint(operation)
        if operation['tool'] == 'write_file':
            lines = difflib.unified_diff(operation['before'].splitlines(True),
                        operation['content'].splitlines(True), fromfile=operation['path'], tofile=operation['path'])
            diff = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
            detail = f"File: {operation['path']}\nCreate: {not operation['existed']}\n" + (diff or '(no textual changes)\n')
        else:
            detail = f"Mode: {operation['mode']}\nImage: {operation.get('image', '')}\nWorkspace: {operation['workdir']}\nCommand:\n{operation['command']}"
        preview = f"Review {identifier} ({request['status']})\n{detail}"
        # Untrusted file/command text must not hide itself with terminal escapes or bidi controls.
        return ''.join(f'\\u{ord(char):04x}' if (ord(char) < 32 and char not in '\n\t') or ord(char) == 127 or unicodedata.category(char) == 'Cf' else char for char in preview)


_queues = {}
_queue_lock = threading.Lock()


def review_queue(settings):
    path = settings.state_path / 'reviews'
    with _queue_lock:
        if path not in _queues:
            _queues[path] = ReviewQueue(path, settings.review_timeout)
        return _queues[path]
