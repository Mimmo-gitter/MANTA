"""
Transactional Outbox Package.

Provides atomic domain mutation + event staging and outbox relay query helpers.
"""

from app.outbox.service import (
    claim_unpublished_outbox_events,
    mark_outbox_events_published,
    record_deployment_failure_atomic,
    record_outbox_event,
)

__all__ = [
    "claim_unpublished_outbox_events",
    "mark_outbox_events_published",
    "record_deployment_failure_atomic",
    "record_outbox_event",
]
