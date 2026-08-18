from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from .store import MAX_PAGE_SIZE, Approval, ApprovalConflict, ApprovalNotFound, ApprovalStore


class ResponseBody(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")
    expected_version: int = Field(ge=1)
    nonce: str | None = None


class ApprovalDTO(BaseModel):
    request_id: str
    profile: str
    explanation: str
    source: str
    status: str
    version: int
    nonce: str
    created_at: float
    resolved_at: float | None = None
    decision: str | None = None


class EventDTO(BaseModel):
    id: int
    profile: str
    request_id: str
    type: str
    payload: dict[str, Any]
    created_at: float

    @field_validator("payload", mode="before")
    @classmethod
    def remove_internal_fields(cls, payload: Any) -> dict[str, Any]:
        """Strip internal compatibility fields recursively before serialization.

        Event payloads are intentionally open-ended, so producer-side field
        selection alone cannot protect this public boundary.  ``session_id``
        is an internal host/session key and is removed wherever it occurs,
        including inside nested mappings and lists.
        """
        sanitized = _sanitize_event_value(payload)
        if not isinstance(sanitized, dict):
            raise TypeError("event payload must be an object")
        return sanitized


def approval_dto(approval: Approval) -> ApprovalDTO:
    # Explicit field selection is intentional: session_id is an internal
    # compatibility key and must never cross this API boundary.
    return ApprovalDTO(
        request_id=approval.request_id,
        profile=approval.profile,
        explanation=approval.explanation,
        source=approval.source,
        status=approval.status,
        version=approval.version,
        nonce=approval.nonce,
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
        decision=approval.decision,
    )


def _sanitize_event_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _sanitize_event_value(item)
            for key, item in value.items()
            if key != "session_id"
        }
    if isinstance(value, list):
        return [_sanitize_event_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_event_value(item) for item in value]
    return value


def event_dto(event: dict[str, Any]) -> EventDTO:
    return EventDTO(**event)


def create_router(store: ApprovalStore, *, authorize: Callable[[], bool]) -> APIRouter:
    router = APIRouter()

    def guard() -> None:
        if not authorize():
            raise HTTPException(403, "not authorized")

    @router.get("/pending")
    def pending(limit: int = Query(500, ge=1, le=MAX_PAGE_SIZE)):
        guard()
        return {"profile": store.profile, "items": [approval_dto(a) for a in store.list_pending(limit=limit)]}

    @router.get("/history")
    def history(limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE)):
        guard()
        return {"profile": store.profile, "items": [approval_dto(a) for a in store.history(limit=limit)]}

    @router.post("/{request_id}/respond", response_model=ApprovalDTO)
    def respond(request_id: str, body: ResponseBody):
        guard()
        try:
            return approval_dto(store.respond(request_id, body.decision, expected_version=body.expected_version, nonce=body.nonce))
        except ApprovalNotFound:
            raise HTTPException(404, "approval not found")
        except ApprovalConflict as exc:
            raise HTTPException(409, str(exc))

    @router.get("/events")
    def events(after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=MAX_PAGE_SIZE)):
        guard()
        return {"events": [event_dto(event) for event in store.events(after_id=after_id, limit=limit)]}

    @router.websocket("/events/stream")
    async def stream(ws: WebSocket):
        if not authorize():
            await ws.close(code=4403)
            return
        await ws.accept()
        cursor = 0
        try:
            while True:
                if not authorize():
                    await ws.close(code=4403)
                    return
                for event in store.events(after_id=cursor, limit=100):
                    cursor = event["id"]
                    await ws.send_json(event_dto(event).model_dump())
                import asyncio
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return

    return router
