"""Conservative, durable generation cap. Submission intent consumes a slot."""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager


class GenerationLimit(RuntimeError):
    pass


class CallLedger:
    LIMIT = 2

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, request_hash TEXT UNIQUE, status TEXT, response TEXT)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            with db:yield db
        finally:db.close()

    def generate(self, request, callback):
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved = db.execute('SELECT status,response FROM calls WHERE request_hash=?', (digest,)).fetchone()
            if saved:
                if saved[0] == 'COMPLETED':
                    return json.loads(saved[1])
                raise GenerationLimit('A previous submission has an uncertain outcome; automatic resubmission is disabled')
            if db.execute('SELECT count(*) FROM calls').fetchone()[0] >= self.LIMIT:
                raise GenerationLimit('The two Sol request limit has been reached')
            db.execute('INSERT INTO calls(request_hash,status) VALUES (?,?)', (digest, 'SUBMITTING'))
        # SDK retries must be zero. Network failures still consume the reserved slot.
        response = callback(request)
        with self.connect() as db:
            db.execute('UPDATE calls SET status=?,response=? WHERE request_hash=?',
                       ('COMPLETED', json.dumps(response, allow_nan=False), digest))
        return response

    def summary(self):
        with self.connect() as db:
            rows = db.execute('SELECT status,count(*) FROM calls GROUP BY status').fetchall()
        return dict(model='gpt-6.1-sol', limit=self.LIMIT, reserved_requests=sum(n for _, n in rows), states=dict(rows),
                    numerical_optimization_requests=0)
