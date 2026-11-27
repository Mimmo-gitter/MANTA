"""WebSocket gateway and real-time tenant stream package (TASK-303-1 & TASK-303-2)."""

from app.ws.gateway import WebSocketGatewayManager, build_ws_event_frame
from app.ws.router import router as ws_router

__all__ = [
    "WebSocketGatewayManager",
    "build_ws_event_frame",
    "ws_router",
]
