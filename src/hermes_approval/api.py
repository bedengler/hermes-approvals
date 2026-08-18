from __future__ import annotations

from collections.abc import Callable
from typing import Optional
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from .store import ApprovalConflict, ApprovalNotFound, ApprovalStore

class ResponseBody(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")
    expected_version: int = Field(ge=1)
    nonce: Optional[str] = None

def create_router(store: ApprovalStore, *, authorize: Callable[[], bool]) -> APIRouter:
    router = APIRouter()
    def guard():
        if not authorize(): raise HTTPException(403, "not authorized")
    @router.get("/approvals/pending")
    def pending():
        guard(); return {"profile": store.profile, "items": [a.__dict__ for a in store.list_pending()]}
    @router.get("/approvals/history")
    def history(limit: int = 100):
        guard(); return {"profile": store.profile, "items": [a.__dict__ for a in store.history(limit=min(limit, 500))]}
    @router.post("/approvals/{request_id}/respond")
    def respond(request_id: str, body: ResponseBody):
        guard()
        try: return store.respond(request_id, body.decision, expected_version=body.expected_version, nonce=body.nonce).__dict__
        except ApprovalNotFound: raise HTTPException(404, "approval not found")
        except ApprovalConflict as exc: raise HTTPException(409, str(exc))
    @router.get("/approvals/events")
    def events(after_id: int = 0):
        guard(); return {"events": store.events(after_id=after_id)}
    @router.websocket("/approvals/events/stream")
    async def stream(ws: WebSocket):
        if not authorize(): await ws.close(code=4403); return
        await ws.accept(); cursor = 0
        try:
            while True:
                for event in store.events(after_id=cursor):
                    cursor = event["id"]; await ws.send_json(event)
                await __import__("asyncio").sleep(1)
        except WebSocketDisconnect: return
    return router
