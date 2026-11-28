"""WebSocket gateway and real-time tenant stream package (TASK-303-1, TASK-303-2 & TASK-305-1)."""

from app.ws.gateway import (
    DEFAULT_REPLAY_BUFFER_SIZE,
    ReplayPlan,
    ReplayResult,
    WebSocketGatewayManager,
    build_replay_gap_frame,
    build_ws_event_frame,
    parse_stream_seq,
)
from app.ws.router import router as ws_router

__all__ = [
    "DEFAULT_REPLAY_BUFFER_SIZE",
    "ReplayPlan",
    "ReplayResult",
    "WebSocketGatewayManager",
    "build_replay_gap_frame",
    "build_ws_event_frame",
    "parse_stream_seq",
    "ws_router",
]
