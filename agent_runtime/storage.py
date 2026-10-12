"""Portable interprocess locks and atomic private JSON files."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Lock path may not be a symlink')
    with path.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def jsonable(value):
    if hasattr(value, 'model_dump'):
        return jsonable(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, '__dict__'):
        return jsonable(vars(value))
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(jsonable(value), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SessionStore:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, messages, todo, status, task_id=None):
        with file_lock(self.path.with_suffix('.lock')):
            atomic_json(self.path, {'version': 1, 'messages': messages, 'todo': todo,
                                    'status': status, 'task_id': task_id})

    def load(self):
        if not self.path.exists():
            return None
        with file_lock(self.path.with_suffix('.lock')):
            data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('messages'), list):
            raise ValueError('Unsupported or invalid session checkpoint')
        return data


def repair_history(messages):
    """Do not replay tools after a crash; close incomplete calls explicitly."""
    repaired = []
    pending = {}
    def finish():
        for identifier in pending:
            repaired.append({'role': 'tool', 'tool_call_id': identifier,
                             'content': 'Interrupted before a durable result. Inspect state before retrying; side effects may already have occurred.'})
        pending.clear()
    for message in messages:
        if message['role'] != 'tool':
            finish()
        repaired.append(message)
        if message['role'] == 'assistant':
            pending.update({call['id']: True for call in message.get('tool_calls') or []})
        elif message['role'] == 'tool':
            pending.pop(message['tool_call_id'], None)
    finish()
    return repaired
