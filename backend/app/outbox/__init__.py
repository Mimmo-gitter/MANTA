"""
Transactional Outbox Package.

Provides atomic domain mutation + event staging and outbox relay query helpers.
"""

from app.outbox.consumer import (
    ConsumeResult,
    OutboxConsumerMetrics,
    OutboxStreamConsumer,
    OutboxStreamMessage,
    cleanup_expired_processed_outbox_events,
    purge_expired_processed_outbox_events,
)
from app.outbox.relay import OutboxRelayStats, OutboxRelayWorker
from app.outbox.service import (
    claim_unpublished_outbox_events,
    mark_outbox_events_published,
    record_deployment_failure_atomic,
    record_outbox_event,
)

__all__ = [
    "ConsumeResult",
    "OutboxConsumerMetrics",
    "OutboxRelayStats",
    "OutboxRelayWorker",
    "OutboxStreamConsumer",
    "OutboxStreamMessage",
    "claim_unpublished_outbox_events",
    "cleanup_expired_processed_outbox_events",
    "mark_outbox_events_published",
    "purge_expired_processed_outbox_events",
    "record_deployment_failure_atomic",
    "record_outbox_event",
]
