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

MAX_TEXT_LENGTH = 2000
MAX_PAGE_SIZE = 500
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_EVENTS = 10_000
MAX_RETENTION_DAYS = 3_650
MAX_RETENTION_EVENTS = 100_000

# Match values associated with credentials in both shell-style and key/value
# text.  Option names are deliberately broad: an approval explanation may
# contain an arbitrary command and should never persist a value for an option
# that looks like a credential.
_PEM_PRIVATE_KEY_BEGIN = re.compile(
    r"(?is)-{5}BEGIN (?P<label>(?:[A-Z0-9]+ )*PRIVATE KEY)-{5}"
)
# These patterns identify the sensitive key/prefix only.  Values are consumed
# by _consume_secret_value below instead of by regex: a backslash escapes the
# following character, so escaped quotes cannot terminate a quoted value.
_SECRET_PREFIX_PATTERNS = (
    re.compile(r"(?i)\b(?:Bearer|Basic)\s+"),
    re.compile(
        r"(?i)[\"'](?:api[_-]?key|token|password|passwd|secret|access[_-]?key|"
        r"client[_-]?(?:id|secret)|private[_-]?key|auth(?:entication)?|credential)"
        r"[\"']\s*:\s*"
    ),
    re.compile(
        r"(?i)--(?:token|password|passwd|secret|api[-_]?key|access[-_]?key|auth(?:entication)?|credential|"
        r"client[-_]?(?:id|secret)|private[-_]?key)(?:=|\s+)"
    ),
    re.compile(r"(?i)--[a-z0-9][a-z0-9_-]*="),
    re.compile(
        r"(?i)(?:\b(?:api[_-]?key|token|password|passwd|secret|access[_-]?key|"
        r"client[_-]?(?:id|secret)|private[_-]?key|"
        r"auth(?:entication)?(?:[_-]?(?:key|token))?|credential(?:[_-]?key)?)|"
        r"[\"'](?:api[_-]?key|token|password|passwd|secret|access[_-]?key|"
        r"client[_-]?(?:id|secret)|private[_-]?key|"
        r"auth(?:entication)?(?:[_-]?(?:key|token))?|credential(?:[_-]?key)?)[\"'])"
        r"\s*[=:]\s*"
    ),
)


class ApprovalError(Exception):
    pass


class ApprovalNotFound(ApprovalError):
    pass


class ApprovalConflict(ApprovalError):
    pass


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
    """Redact accepted textual credential forms before anything is persisted.

    This is intentionally a textual policy, not a general JSON/YAML
    sanitizer: quoted values may contain spaces, and PEM private-key blocks
    are consumed through their matching END marker across newlines; malformed
    or unterminated blocks consume the remainder of the input fail-closed.
    """
    value = _redact_pem_private_keys(str(text))
    value = _redact_secret_values(value)
    return value[:MAX_TEXT_LENGTH]


def _consume_secret_value(value: str, start: int) -> int:
    """Return the exclusive end of a quoted or textual secret value."""
    if start >= len(value) or value[start] not in "\"'":
        end = start
        while end < len(value) and not (value[end].isspace() or value[end] in ",;}"):
            end += 1
        return end

    quote = value[start]
    cursor = start + 1
    while cursor < len(value):
        if value[cursor] == "\\":
            # Consume the escape and its target together. This handles both
            # escaped delimiters and escaped backslashes without decoding text.
            cursor += 2
        elif value[cursor] == quote:
            return cursor + 1
        else:
            cursor += 1
    return len(value)  # Unterminated quoted values fail closed.


def _redact_secret_values(value: str) -> str:
    output: list[str] = []
    cursor = 0
    while cursor < len(value):
        matches = [
            match
            for pattern in _SECRET_PREFIX_PATTERNS
            if (match := pattern.search(value, cursor)) is not None
        ]
        if not matches:
            output.append(value[cursor:])
            break
        match = min(matches, key=lambda item: item.start())
        output.append(value[cursor:match.end()])
        end = _consume_secret_value(value, match.end())
        output.append("[REDACTED]")
        cursor = end
    return "".join(output)


def _redact_pem_private_keys(value: str) -> str:
    """Consume private-key material, failing closed on malformed PEM.

    A recognized BEGIN marker is sensitive by itself.  Only an END marker with
    the same label can close it; a missing or mismatched END therefore consumes
    the remainder of the input instead of allowing key material to escape.
    Searching the complete input before applying the output bound also keeps
    a marker or secret beyond ``MAX_TEXT_LENGTH`` from being persisted.
    """
    output: list[str] = []
    cursor = 0
    while match := _PEM_PRIVATE_KEY_BEGIN.search(value, cursor):
        output.append(value[cursor:match.start()])
        end_marker = re.compile(r"(?is)-{5}END " + re.escape(match.group("label")) + r"-{5}")
        end = end_marker.search(value, match.end())
        if end is None:
            output.append("[REDACTED]")
            return "".join(output)
        output.append("[REDACTED]")
        cursor = end.end()
    output.append(value[cursor:])
    return "".join(output)


def _bounded_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    return limit


def _bounded_positive_int(value: int, name: str, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


class ApprovalStore:
    """SQLite-backed, profile-scoped approval state and append-only events.

    ``cleanup`` is the explicit retention mechanism. Hosts should run it from
    their maintenance job; the documented default deletes resolved approvals
    after 90 days and retains only the newest 10,000 events per profile.
    """

    def __init__(self, path: str | Path, *, profile: str):
        self.path, self.profile = Path(path), profile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as db:
            db.executescript(
                """
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
                CREATE INDEX IF NOT EXISTS approval_events_profile_id ON approval_events(profile, id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    @staticmethod
    def _row(row: sqlite3.Row) -> Approval:
        return Approval(**dict(row))

    @staticmethod
    def _begin(db: sqlite3.Connection) -> None:
        db.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _finish(db: sqlite3.Connection, commit: bool) -> None:
        db.commit() if commit else db.rollback()

    def create(self, *, session_id: str, explanation: str, source: str) -> Approval:
        now, rid = time.time(), secrets.token_urlsafe(24)
        approval = Approval(
            rid, self.profile, str(session_id), redact(explanation), redact(source),
            "pending", 1, secrets.token_urlsafe(18), now,
        )
        with self._lock, self._connect() as db:
            self._begin(db)
            try:
                db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)", tuple(approval.__dict__.values()))
                self._event(db, approval, "approval.created")
                self._finish(db, True)
            except Exception:
                self._finish(db, False)
                raise
        return approval

    def list_pending(self, *, limit: int = MAX_PAGE_SIZE) -> list[Approval]:
        limit = _bounded_limit(limit)
        with self._connect() as db:
            return [self._row(r) for r in db.execute(
                "SELECT * FROM approvals WHERE profile=? AND status='pending' ORDER BY created_at LIMIT ?",
                (self.profile, limit),
            )]

    def history(self, *, limit: int = 100) -> list[Approval]:
        limit = _bounded_limit(limit)
        with self._connect() as db:
            return [self._row(r) for r in db.execute(
                "SELECT * FROM approvals WHERE profile=? AND status!='pending' ORDER BY resolved_at DESC LIMIT ?",
                (self.profile, limit),
            )]

    def events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        limit = _bounded_limit(limit)
        if not isinstance(after_id, int) or isinstance(after_id, bool) or after_id < 0:
            raise ValueError("after_id must be a non-negative integer")
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM approval_events WHERE profile=? AND id>? ORDER BY id LIMIT ?",
                (self.profile, after_id, limit),
            )
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def respond(self, request_id: str, decision: str, *, expected_version: int, nonce: str | None = None) -> Approval:
        if decision not in {"approve", "deny"}:
            raise ValueError("decision must be approve or deny")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        with self._lock, self._connect() as db:
            self._begin(db)
            try:
                row = db.execute(
                    "SELECT * FROM approvals WHERE request_id=? AND profile=?", (request_id, self.profile)
                ).fetchone()
                if not row:
                    raise ApprovalNotFound(request_id)
                if row["status"] != "pending" or row["version"] != expected_version or (nonce is not None and nonce != row["nonce"]):
                    raise ApprovalConflict("approval is stale or already resolved")
                now = time.time()
                updated_status = "approved" if decision == "approve" else "denied"
                updated_count = db.execute(
                    "UPDATE approvals SET status=?, decision=?, version=version+1, resolved_at=? "
                    "WHERE request_id=? AND profile=? AND status='pending' AND version=?",
                    (updated_status, decision, now, request_id, self.profile, expected_version),
                ).rowcount
                if updated_count != 1:
                    raise ApprovalConflict("approval is stale or already resolved")
                updated = db.execute(
                    "SELECT * FROM approvals WHERE request_id=? AND profile=?", (request_id, self.profile)
                ).fetchone()
                result = self._row(updated)
                self._event(db, result, "approval.resolved")
                self._finish(db, True)
                return result
            except Exception:
                self._finish(db, False)
                raise

    def resolve_legacy_session(self, session_id: str, decision: str) -> Approval:
        # Resolve the oldest matching row in SQLite rather than filtering the
        # bounded public pending page.  The profile and pending status remain
        # part of the query, so this cannot expose or select another profile's
        # approvals; respond() still performs the version/CAS authorization.
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM approvals "
                "WHERE profile=? AND status='pending' AND session_id=? "
                "ORDER BY created_at, rowid LIMIT 1",
                (self.profile, str(session_id)),
            ).fetchone()
        if not row:
            raise ApprovalNotFound(session_id)
        approval = self._row(row)
        return self.respond(approval.request_id, decision, expected_version=approval.version)

    def cleanup(self, *, retention_days: int = DEFAULT_RETENTION_DAYS, max_events: int = DEFAULT_MAX_EVENTS) -> int:
        """Apply retention policy and return the number of deleted events.

        Resolved approvals are deleted when ``resolved_at`` is older than
        ``retention_days``. Events have no age-based deletion: they are kept
        until they fall outside the newest ``max_events`` rows for this
        profile. This distinction intentionally allows an event audit trail
        to outlive the approval row it describes.
        """
        retention_days = _bounded_positive_int(retention_days, "retention_days", MAX_RETENTION_DAYS)
        max_events = _bounded_positive_int(max_events, "max_events", MAX_RETENTION_EVENTS)
        cutoff = time.time() - retention_days * 86400
        with self._lock, self._connect() as db:
            self._begin(db)
            try:
                db.execute("DELETE FROM approvals WHERE profile=? AND status!='pending' AND resolved_at<?", (self.profile, cutoff))
                event_ids = [row["id"] for row in db.execute(
                    "SELECT id FROM approval_events WHERE profile=? ORDER BY id DESC", (self.profile,)
                ).fetchall()]
                stale_ids = event_ids[max_events:]
                deleted = 0
                if stale_ids:
                    placeholders = ",".join("?" for _ in stale_ids)
                    deleted = db.execute(
                        f"DELETE FROM approval_events WHERE profile=? AND id IN ({placeholders})",
                        (self.profile, *stale_ids),
                    ).rowcount
                self._finish(db, True)
                return deleted
            except Exception:
                self._finish(db, False)
                raise

    def _event(self, db: sqlite3.Connection, approval: Approval, kind: str) -> None:
        payload = {"request_id": approval.request_id, "status": approval.status, "version": approval.version}
        db.execute(
            "INSERT INTO approval_events(profile,request_id,type,payload,created_at) VALUES (?,?,?,?,?)",
            (self.profile, approval.request_id, kind, json.dumps(payload), time.time()),
        )
