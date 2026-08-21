"""Adapter for the CLI-owned Hermes governance approval ledger.

The decision service delegates to the governance module's supported ``decide``
function.  It never shells out, chooses an oldest request, or writes a second
ledger format.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import redact


class GovernanceApprovalError(Exception):
    status_code = 409


class GovernanceApprovalNotFound(GovernanceApprovalError):
    status_code = 404


class GovernanceApprovalConflict(GovernanceApprovalError):
    status_code = 409


class GovernanceApprovalStore:
    """Parse governance JSONL without exposing or implementing its decision path."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        latest: dict[str, dict[str, Any]] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(record, dict) and isinstance(record.get("approval_id"), str):
                latest[record["approval_id"]] = record
        return list(latest.values())

    def get(self, approval_id: str) -> dict[str, Any] | None:
        """Return one exact ledger record; never fall back to another ID."""
        return next((r for r in self._records() if r.get("approval_id") == approval_id), None)

    @staticmethod
    def status(record: dict[str, Any], *, now: datetime | None = None) -> str:
        status = str(record.get("status", "unknown"))
        if status == "pending":
            try:
                expires = datetime.fromisoformat(str(record["expires_at"]))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                if expires <= (now or datetime.now(timezone.utc)):
                    return "expired"
            except (KeyError, TypeError, ValueError):
                return "invalid"
        return status

    @staticmethod
    def _safe(record: dict[str, Any]) -> dict[str, Any]:
        """Build the sole public governance DTO, conservatively.

        Ledger rows are CLI-owned and may gain private or nested fields over
        time.  Never serialize a row (or stringify an arbitrary mapping) at
        this boundary: select the documented fields, remove internal keys from
        nested values, and run the shared credential/PEM redactor on the final
        text.  This protects both HTTP responses and the dashboard payload.
        """
        def safe(value: Any) -> str | None:
            if value is None:
                return None
            def scrub_nested(item: Any) -> Any:
                if isinstance(item, dict):
                    return {
                        str(key): scrub_nested(nested)
                        for key, nested in item.items()
                        if str(key).lower() not in {
                            "session_id", "session_key", "credentials", "credential",
                        }
                    }
                if isinstance(item, (list, tuple)):
                    return [scrub_nested(nested) for nested in item]
                if isinstance(item, str):
                    return redact(item)
                return item

            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(scrub_nested(value), sort_keys=True, default=str)
            return redact(str(value))

        return {
            "approval_id": str(record.get("approval_id", "")),
            "gate": safe(record.get("gate")) or "unknown",
            "target": safe(record.get("target")) or "",
            "action": safe(record.get("action")) or "",
            "rationale": safe(record.get("rationale")),
            "decision_note": safe(record.get("decision_note")),
            "status": "",  # assigned below so expiry is computed from raw data
            "created_at": record.get("created_at"),
            "expires_at": record.get("expires_at"),
            "decided_at": record.get("decided_at"),
        }

    def list(self, *, include_history: bool, limit: int = 500) -> list[dict[str, Any]]:
        rows = []
        for raw in self._records():
            item = self._safe(raw)
            item["status"] = self.status(raw)
            if include_history or item["status"] == "pending":
                rows.append(item)
        rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return rows[:limit]

    def pending(self, limit: int = 500) -> list[dict[str, Any]]:
        return self.list(include_history=False, limit=limit)

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        return [row for row in self.list(include_history=True, limit=limit * 2) if row["status"] != "pending"][:limit]


class GovernanceDecisionService:
    """Narrow, injectable adapter around the canonical governance decision path."""

    def __init__(self, store: GovernanceApprovalStore, decide_fn):
        self.store = store
        self.decide_fn = decide_fn

    def decide(self, approval_id: str, decision: str, note: str = "") -> dict[str, Any]:
        if decision not in ("approved", "denied"):
            raise ValueError("decision must be approved or denied")
        record = self.store.get(approval_id)
        if record is None:
            raise GovernanceApprovalNotFound("governance approval not found")
        status = self.store.status(record)
        if status != "pending":
            raise GovernanceApprovalConflict(
                "governance approval is already decided or expired"
            )
        # The canonical function performs the append-only audit/ledger write.
        result = self.decide_fn(approval_id, decision, note)
        result_status = result.get("status") if isinstance(result, dict) else None
        if result_status != decision:
            raise GovernanceApprovalConflict(
                "governance approval became decided or expired"
            )
        refreshed = self.store.get(approval_id) or record
        return self.store._safe(refreshed) | {"status": self.store.status(refreshed)}
