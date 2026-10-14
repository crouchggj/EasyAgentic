"""SQLite outbox/inbox with leases, explicit acknowledgements and stable IDs."""
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from .execution import current_task
from .storage import file_lock


class MessageBus:
    def __init__(self, team_dir, lease_seconds=30, max_attempts=5):
        self.dir = Path(team_dir) / 'inbox'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'messages.sqlite3'
        if self.path.is_symlink():
            raise ValueError('Message database may not be a symlink')
        if not math.isfinite(lease_seconds) or lease_seconds <= 0 or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("Message lease and attempt limit must be positive")
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self._contexts = {}
        self._context_lock = threading.Lock()
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, recipient TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT "pending", attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0, lease_token TEXT, last_error TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS delivery ON messages(recipient,state,lease_until)')
        self._migrate_legacy()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
            raise ValueError('Agent name must contain 1-64 letters, digits, underscores or hyphens')

    def _migrate_legacy(self):
        for path in self.dir.glob('*.jsonl'):
            self.validate_name(path.stem)
            if path.is_symlink():
                raise ValueError('Legacy inbox may not be a symlink')
            with file_lock(path.with_suffix('.lock')):
                if not path.exists():
                    continue
                lines = path.read_text(encoding='utf-8').splitlines()
                parsed = [json.loads(line) for line in lines if line.strip()]
                with self.connection() as db:
                    for index, payload in enumerate(parsed):
                        # Stable across interrupted migration; preserve the original file as backup.
                        import hashlib
                        identifier = 'legacy-' + hashlib.sha256(f'{path.stem}:{index}:{json.dumps(payload,sort_keys=True)}'.encode()).hexdigest()
                        payload['id'] = identifier
                        db.execute('INSERT OR IGNORE INTO messages(id,recipient,payload) VALUES(?,?,?)',
                                   (identifier, path.stem, json.dumps(payload)))
                backup = path.with_suffix('.jsonl.migrated')
                if backup.exists():
                    backup = path.with_suffix('.jsonl.' + uuid.uuid4().hex + '.migrated')
                path.rename(backup)

    def send(self, sender, to, content, msg_type='message', extra=None, message_id=None):
        self.validate_name(sender)
        self.validate_name(to)
        identifier = message_id or uuid.uuid4().hex
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128:
            raise ValueError('Message ID must contain 1-128 characters')
        payload = {'id': identifier, 'type': msg_type, 'from': sender, 'content': content}
        if extra:
            if any(key in extra for key in payload):
                raise ValueError('Message metadata cannot replace identity or content')
            payload.update(extra)
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT recipient,payload FROM messages WHERE id=?', (identifier,)).fetchone()
            if existing and (existing['recipient'] != to or existing['payload'] != serialized):
                raise ValueError('Message ID already belongs to different content or recipient')
            if not existing:
                db.execute('INSERT INTO messages(id,recipient,payload) VALUES(?,?,?)', (identifier,to,serialized))
            if msg_type == 'message':
                with self._context_lock:
                    self._contexts[to] = current_task.get()
        return f'Message sent to {to} (id: {identifier})'

    def claim(self, name, limit=100):
        self.validate_name(name)
        now = time.time()
        token = uuid.uuid4().hex
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE messages SET state="dead", last_error="delivery attempts exhausted" WHERE recipient=? AND attempts>=? AND (state="pending" OR (state="inflight" AND lease_until<=?))', (name,self.max_attempts,now))
            rows = db.execute('SELECT * FROM messages WHERE recipient=? AND (state="pending" OR (state="inflight" AND lease_until<=?)) ORDER BY rowid LIMIT ?', (name,now,limit)).fetchall()
            result = []
            for row in rows:
                db.execute('UPDATE messages SET state="inflight", attempts=attempts+1, lease_until=?,lease_token=? WHERE id=?', (now+self.lease_seconds,token,row['id']))
                result.append({'id': row['id'], 'token': token, 'payload': json.loads(row['payload']), 'attempt': row['attempts']+1})
            return result

    def acknowledge(self, deliveries):
        with self.connection() as db:
            for delivery in deliveries:
                db.execute('UPDATE messages SET state="acked",lease_token=NULL,lease_until=0 WHERE id=? AND lease_token=? AND state="inflight"', (delivery['id'],delivery['token']))

    def reject(self, deliveries, error='checkpoint failed'):
        with self.connection() as db:
            for delivery in deliveries:
                db.execute('UPDATE messages SET state=CASE WHEN attempts>=? THEN "dead" ELSE "pending" END,lease_token=NULL,lease_until=0,last_error=? WHERE id=? AND lease_token=? AND state="inflight"', (self.max_attempts,error,delivery['id'],delivery['token']))

    def receive(self, name, seen, checkpoint):
        """Checkpoint newly received messages before acknowledging them."""
        deliveries = self.claim(name)
        fresh = [d for d in deliveries if d['id'] not in seen]
        try:
            if fresh:
                checkpoint(json.dumps([d['payload'] for d in fresh], ensure_ascii=False),
                           seen | {d['id'] for d in fresh})
            self.acknowledge(deliveries)
        except BaseException as exc:
            self.reject(deliveries, type(exc).__name__)
            raise
        seen.update(d['id'] for d in fresh)
        return bool(fresh)

    def read_inbox(self, name):
        """Legacy convenience read; application loops use receive/checkpoint instead."""
        deliveries = self.claim(name)
        self.acknowledge(deliveries)
        return json.dumps([d['payload'] for d in deliveries], ensure_ascii=False)

    def status(self, name=None):
        if name:
            self.validate_name(name)
        with self.connection() as db:
            rows = db.execute('SELECT id,recipient,state,attempts,lease_until,last_error FROM messages' +
                              (' WHERE recipient=?' if name else '') + ' ORDER BY rowid', (name,) if name else ()).fetchall()
        return [dict(row) for row in rows]

    def discard(self, name):
        self.validate_name(name)
        with self.connection() as db:
            db.execute('UPDATE messages SET state="acked",lease_token=NULL,lease_until=0 WHERE recipient=? AND state!="acked"', (name,))

    def retry(self, identifier):
        with self.connection() as db:
            row = db.execute('SELECT state FROM messages WHERE id=?', (identifier,)).fetchone()
            if not row or row['state'] != 'dead':
                raise ValueError('Only dead-letter messages can be retried')
            db.execute('UPDATE messages SET state="pending",attempts=0,last_error=NULL WHERE id=?', (identifier,))
        return 'Message requeued'

    def take_context(self, name):
        with self._context_lock:
            return self._contexts.pop(name, None)

    def broadcast(self, sender, content, msg_type='broadcast', members=None):
        recipients = [name for name in members or [] if name != sender]
        for name in recipients:
            self.send(sender,name,content,msg_type)
        return f'Broadcast sent to {len(recipients)} members'
