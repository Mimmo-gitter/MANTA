"""Authenticated multi-tenant WebSocket stream router (TASK-303-1, TASK-303-2 & TASK-305-1)."""

from __future__ import annotations

import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from opentelemetry.trace import SpanKind

from app.auth.permissions import (
    FORBIDDEN_DETAIL,
    Permission,
    require_ws_permission,
)
from app.auth.schemas import TokenClaims
from app.observability.tracing import scrub_sensitive_query, start_linked_span
from app.ws.gateway import WebSocketGatewayManager

router = APIRouter(prefix="/api/v1/ws", tags=["WebSockets"])


def _get_ws_gateway(websocket: WebSocket) -> WebSocketGatewayManager:
    gateway = getattr(websocket.app.state, "ws_gateway", None)
    if not isinstance(gateway, WebSocketGatewayManager):
        gateway = WebSocketGatewayManager()
        websocket.app.state.ws_gateway = gateway
    return gateway


def _extract_requested_last_seq(websocket: WebSocket) -> str | None:
    """Resolve optional `last_seq` cursor from `?last_seq=` query param or `Last-Event-ID` header."""
    raw_query = websocket.query_params.get("last_seq")
    if raw_query is not None and raw_query.strip():
        return raw_query.strip()
    raw_header = websocket.headers.get("last-event-id")
    if raw_header is not None and raw_header.strip():
        return raw_header.strip()
    return None


@router.websocket("/tenants/{tenant_id}/stream")
async def tenant_events_stream(
    websocket: WebSocket,
    tenant_id: uuid.UUID,
    claims: TokenClaims = Depends(require_ws_permission(Permission.VIEW_PROJECT_DORA)),
) -> None:
    """
    Real-time tenant-scoped WebSocket alert stream with monotonic `last_seq` replay.

    Auth, RBAC, and cross-tenant boundary validation complete strictly before
    `websocket.accept()`, rejecting unauthorized upgrades with HTTP 403 / WS 1008.
    When `?last_seq=` (or `Last-Event-ID`) is provided, replays missed events from
    `tenant_stream:{tenant_id}` (up to 5,000 events) or emits `REPLAY_GAP`.
    """
    token_tenant_id = getattr(websocket.state, "tenant_uuid", None) or claims.tenant_uuid
    if token_tenant_id != tenant_id:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=FORBIDDEN_DETAIL,
        )

    gateway = _get_ws_gateway(websocket)
    initial_cursor = await gateway.snapshot_stream_tail(tenant_id)
    requested_last_seq = _extract_requested_last_seq(websocket)
    scrubbed_query = scrub_sensitive_query(websocket.scope.get("query_string"))

    with start_linked_span(
        "websocket.connect",
        kind=SpanKind.SERVER,
        attributes={
            "manta.tenant_id": str(tenant_id),
            "url.path": str(websocket.scope.get("path") or ""),
            "url.query": scrubbed_query,
            "manta.ws.requested_last_seq": requested_last_seq or "",
        },
    ) as span:
        subprotocol = getattr(websocket.state, "ws_subprotocol", None)
        await websocket.accept(subprotocol=subprotocol)
        replay_result = await gateway.replay_and_register_subscriber(
            tenant_id,
            websocket,
            last_seq=requested_last_seq,
            initial_cursor=initial_cursor,
        )
        span.set_attribute("manta.ws.replay_outcome", replay_result.outcome)
        span.set_attribute("manta.ws.replayed_count", replay_result.replayed_count)
        if replay_result.gap_reason:
            span.set_attribute("manta.ws.replay_gap_reason", replay_result.gap_reason)

    try:
        while True:
            raw_msg = await websocket.receive_text()
            msg_clean = raw_msg.strip()
            if msg_clean == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            elif msg_clean.startswith("{"):
                try:
                    parsed = json.loads(msg_clean)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    msg_type = parsed.get("type")
                    if msg_type == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                    elif msg_type == "resume" and "last_seq" in parsed:
                        raw_resume_seq = parsed.get("last_seq")
                        resume_seq_str = "" if raw_resume_seq is None else str(raw_resume_seq)
                        await gateway.resume_subscriber(
                            tenant_id,
                            websocket,
                            last_seq=resume_seq_str,
                        )
    except WebSocketDisconnect:
        pass
    finally:
        await gateway.unregister_subscriber(tenant_id, websocket)
