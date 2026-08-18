from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"(?i)(Bearer\s+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*)([^\s,;]+)")

class ApprovalError(Exception): pass
class ApprovalNotFound(ApprovalError): pass
class ApprovalConflict(ApprovalError): pass

@dataclass(frozen=True)
class Approval:
    request_id: str
    profile: str
    session_id: str
    explanation: str
    source: str
    status: str
    version: int
    nonce: str
    created_at: float
    resolved_at: float | None = None
    decision: str | None = None


def redact(text: str) -> str:
    return _SECRET.sub(lambda m: m.group(1) + "[REDACTED]", text)[:2000]

class ApprovalStore:
    """SQLite-backed, profile-scoped approval state and append-only events."""
    def __init__(self, path: str | Path, *, profile: str):
        self.path, self.profile = Path(path), profile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS approvals (
              request_id TEXT PRIMARY KEY, profile TEXT NOT NULL, session_id TEXT NOT NULL,
              explanation TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
              version INTEGER NOT NULL, nonce TEXT NOT NULL, created_at REAL NOT NULL,
              resolved_at REAL, decision TEXT
            );
            CREATE INDEX IF NOT EXISTS approvals_pending ON approvals(profile, status, created_at);
            CREATE TABLE IF NOT EXISTS approval_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL,
              request_id TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL
            );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(row) -> Approval:
        return Approval(**dict(row))

    def create(self, *, session_id: str, explanation: str, source: str) -> Approval:
        now, rid = time.time(), secrets.token_urlsafe(24)
        approval = Approval(rid, self.profile, session_id, redact(explanation), source, "pending", 1, secrets.token_urlsafe(18), now)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(approval.__dict__.values()))
            self._event(db, approval, "approval.created")
        return approval

    def list_pending(self) -> list[Approval]:
        with self._connect() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM approvals WHERE profile=? AND status='pending' ORDER BY created_at", (self.profile,))]

    def history(self, *, limit: int = 100) -> list[Approval]:
        with self._connect() as db:
            return [self._row(r) for r in db.execute("SELECT * FROM approvals WHERE profile=? AND status!='pending' ORDER BY resolved_at DESC LIMIT ?", (self.profile, limit))]

    def events(self, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM approval_events WHERE profile=? AND id>? ORDER BY id", (self.profile, after_id))
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def respond(self, request_id: str, decision: str, *, expected_version: int, nonce: str | None = None) -> Approval:
        if decision not in {"approve", "deny"}: raise ValueError("decision must be approve or deny")
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE request_id=? AND profile=?", (request_id, self.profile)).fetchone()
            if not row: raise ApprovalNotFound(request_id)
            if row["status"] != "pending" or row["version"] != expected_version or (nonce is not None and nonce != row["nonce"]):
                raise ApprovalConflict("approval is stale or already resolved")
            now = time.time()
            db.execute("UPDATE approvals SET status=?, decision=?, version=version+1, resolved_at=? WHERE request_id=?", ("approved" if decision == "approve" else "denied", decision, now, request_id))
            updated = db.execute("SELECT * FROM approvals WHERE request_id=?", (request_id,)).fetchone()
            result = self._row(updated)
            self._event(db, result, "approval.resolved")
            return result

    def resolve_legacy_session(self, session_id: str, decision: str) -> Approval:
        pending = [a for a in self.list_pending() if a.session_id == session_id]
        if not pending: raise ApprovalNotFound(session_id)
        return self.respond(pending[0].request_id, decision, expected_version=pending[0].version)

    def _event(self, db, approval: Approval, kind: str):
        payload = {"request_id": approval.request_id, "status": approval.status, "version": approval.version}
        db.execute("INSERT INTO approval_events(profile,request_id,type,payload,created_at) VALUES (?,?,?,?,?)", (self.profile, approval.request_id, kind, json.dumps(payload), time.time()))
