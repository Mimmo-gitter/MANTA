"""Authenticated multi-tenant WebSocket stream router (TASK-303-1 & TASK-303-2)."""

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


@router.websocket("/tenants/{tenant_id}/stream")
async def tenant_events_stream(
    websocket: WebSocket,
    tenant_id: uuid.UUID,
    claims: TokenClaims = Depends(require_ws_permission(Permission.VIEW_PROJECT_DORA)),
) -> None:
    """
    Real-time tenant-scoped WebSocket alert stream.

    Auth, RBAC, and cross-tenant boundary validation complete strictly before
    `websocket.accept()`, rejecting unauthorized upgrades with HTTP 403 / WS 1008.
    """
    token_tenant_id = getattr(websocket.state, "tenant_uuid", None) or claims.tenant_uuid
    if token_tenant_id != tenant_id:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=FORBIDDEN_DETAIL,
        )

    gateway = _get_ws_gateway(websocket)
    initial_cursor = await gateway.snapshot_stream_tail(tenant_id)
    scrubbed_query = scrub_sensitive_query(websocket.scope.get("query_string"))

    with start_linked_span(
        "websocket.connect",
        kind=SpanKind.SERVER,
        attributes={
            "manta.tenant_id": str(tenant_id),
            "url.path": str(websocket.scope.get("path") or ""),
            "url.query": scrubbed_query,
        },
    ):
        subprotocol = getattr(websocket.state, "ws_subprotocol", None)
        await websocket.accept(subprotocol=subprotocol)
        await gateway.register_subscriber(tenant_id, websocket, initial_cursor=initial_cursor)

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
                if isinstance(parsed, dict) and parsed.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await gateway.unregister_subscriber(tenant_id, websocket)
