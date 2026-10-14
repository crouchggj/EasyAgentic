"""Durable task outcomes and explicit detection of interrupted work."""
import json
import sqlite3
import time


class TaskJournal:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'tasks.sqlite3'
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,parent_id TEXT,status TEXT,updated REAL,details TEXT)')

    def record(self, task, status, **details):
        details.update(tokens=task.budget.tokens, estimated_cost=task.budget.cost,
                       pending_reviews=list(task.pending_reviews))
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated,details=excluded.details',
                       (task.task_id, task.parent.task_id if task.parent else None,
                        status,time.time(),json.dumps(details)))

    def recover(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('UPDATE tasks SET status="interrupted",updated=? WHERE status IN ("running","waiting")', (time.time(),))

    def list(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            rows = db.execute('SELECT id,parent_id,status,updated,details FROM tasks ORDER BY updated DESC').fetchall()
        return [dict(id=r[0],parent_id=r[1],status=r[2],updated=r[3],**json.loads(r[4])) for r in rows]
