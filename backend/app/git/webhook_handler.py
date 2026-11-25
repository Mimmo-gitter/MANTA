from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.git.normalization import (
    normalize_pull_request,
    normalize_pull_request_review,
    normalize_push,
)
from app.git.service import process_pull_request, process_pull_request_review, process_push


def _payload(event: Any) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


async def handle_push(db: AsyncSession, event: Any, tenant_id: uuid.UUID) -> str:
    n = normalize_push(_payload(event))
    if n.kind == "noop":
        return f"noop:{n.reason}"
    return await process_push(db, tenant_id=tenant_id, n=n)


async def handle_pull_request(db: AsyncSession, event: Any, tenant_id: uuid.UUID) -> str:
    n = normalize_pull_request(_payload(event))
    if n.kind == "noop":
        return f"noop:{n.reason}"
    return await process_pull_request(
        db, tenant_id=tenant_id, n=n, integration_id=getattr(event, "integration_id", None)
    )


async def handle_pull_request_review(db: AsyncSession, event: Any, tenant_id: uuid.UUID) -> str:
    n = normalize_pull_request_review(_payload(event))
    if n.kind == "noop":
        return f"noop:{n.reason}"
    return await process_pull_request_review(
        db, tenant_id=tenant_id, n=n, integration_id=getattr(event, "integration_id", None)
    )
