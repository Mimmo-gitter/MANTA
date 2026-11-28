"""
TASK-316-2 (SCRUM-571 / US-42): ADR Generalization Zero-WAN Isolation, Cloud ZDR Refusal & Schema Tests.

DB-independent pytest verification covering:
1. 0 outbound WAN network calls in `local_only` mode (socket + HTTP transport guards).
2. Explicit `cloud_zdr` policy refusal guard (`CloudPolicyViolationError`) when tenant
   is `local_only` (both via `CloudZDRAIClient` and `requested_mode="cloud_zdr"`).
3. Strict `GeneralizedADRPattern` JSON schema adherence (`extra="forbid"`).
4. `AnonymizedADR` `draft -> pending_review` transition and `ADRSanitizationLog` persistence.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import socket
from types import SimpleNamespace
from typing import Any, AsyncIterator
import uuid

import httpx
import pytest

from app.adr import generalizer as generalizer_mod
from app.adr.generalizer import (
    ADRGeneralizationSchemaError,
    GeneralizedADRPattern,
    process_adr_generalize_job,
)
from app.ai.client_factory import (
    AI_MODE_CLOUD_ZDR,
    AI_MODE_LOCAL_ONLY,
    CloudPolicyViolationError,
    CloudZDRAIClient,
    LocalAIClient,
)
from app.db.models.models import ADRSanitizationLog


def _valid_pattern_json() -> str:
    return json.dumps(
        {
            "title": "Adopt Event-Driven Outbox Relay for Service Decoupling",
            "architecture_pattern": "Transactional Outbox + Redis Streams Relay",
            "context_and_problem": "High-throughput webhook intake requires atomic DB persistence and async fanout.",
            "decision_outcome": "Stage domain events in an outbox table and relay asynchronously to per-tenant streams.",
            "consequences": [
                "Zero lost events on broker outage.",
                "Sub-second webhook acknowledgment latency.",
            ],
            "quality_attributes": ["Reliability", "Scalability"],
        }
    )


class _InMemoryGeneralizerDB:
    """Minimal async session double for `process_adr_generalize_job`."""

    def __init__(self, tenant: Any, adr: Any) -> None:
        self.tenant = tenant
        self.adr = adr
        self.added: list[Any] = []
        self.flushed = False

    async def scalar(self, stmt: Any) -> Any:
        entity_name = str(stmt)
        if "FROM tenants" in entity_name:
            return self.tenant
        if "FROM anonymized_adrs" in entity_name:
            return self.adr
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True


class _NoOpScrubber:
    """Deterministic scrubber stub returning clean scrubbed text without Presidio model downloads."""

    def scrub_text(self, text: str, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            scrubbed_text=text,
            total_redactions=0,
            counts={},
            redactions=[],
        )


def _setup_generalizer_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ai_processing_mode: str,
) -> tuple[uuid.UUID, uuid.UUID, Any, Any, _InMemoryGeneralizerDB]:
    tenant_id = uuid.uuid4()
    adr_id = uuid.uuid4()
    tenant = SimpleNamespace(
        id=tenant_id,
        name="Acme Core",
        slug="acme-core",
        allowed_domain="acme.com",
        ai_processing_mode=ai_processing_mode,
        adr_deny_list={"terms": []},
    )
    adr = SimpleNamespace(
        id=adr_id,
        tenant_id=tenant_id,
        title="Original Internal ADR",
        architecture_pattern="Legacy",
        decision_summary="Legacy summary",
        status="draft",
        sanitization_verified=False,
        updated_at=datetime.now(timezone.utc),
    )
    fake_db = _InMemoryGeneralizerDB(tenant, adr)

    @asynccontextmanager
    async def _fake_tenant_ctx(_tid: uuid.UUID) -> AsyncIterator[_InMemoryGeneralizerDB]:
        yield fake_db

    async def _fake_scrub_adr_source(
        _db: Any,
        *,
        tenant_id: uuid.UUID,
        adr_id: uuid.UUID,
        **_kwargs: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            adr_id=adr_id,
            tenant_id=tenant_id,
            quarantined=False,
            scrubbed_title="Adopt Event-Driven Outbox",
            scrubbed_markdown="# Context\nUse transactional outbox for [REDACTED_ORG].",
            total_redactions=1,
            counts={"ORGANIZATION": 1},
            redactions=[{"category": "ORGANIZATION", "placeholder": "[REDACTED_ORG]"}],
            combined_entities_scrubbed=1,
        )

    monkeypatch.setattr(generalizer_mod, "get_tenant_db_context", _fake_tenant_ctx)
    monkeypatch.setattr(generalizer_mod, "scrub_adr_source", _fake_scrub_adr_source)
    return tenant_id, adr_id, tenant, adr, fake_db


@pytest.mark.asyncio
async def test_local_only_generalization_makes_zero_wan_calls_and_transitions_to_pending_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wan_attempts: list[str] = []
    loopback_urls: list[str] = []

    async def _forbid_wan_http(
        _self: httpx.AsyncHTTPTransport,
        request: httpx.Request,
    ) -> httpx.Response:
        wan_attempts.append(str(request.url))
        raise AssertionError(f"Unexpected WAN HTTP call: {request.url}")

    def _forbid_socket(*args: Any, **kwargs: Any) -> Any:
        wan_attempts.append(f"socket:{args!r}")
        raise AssertionError(f"Unexpected WAN socket call: {args!r}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _forbid_wan_http)
    monkeypatch.setattr(socket, "create_connection", _forbid_socket)

    tenant_id, adr_id, _tenant, adr, fake_db = _setup_generalizer_env(
        monkeypatch, ai_processing_mode=AI_MODE_LOCAL_ONLY
    )

    def _local_llm_handler(request: httpx.Request) -> httpx.Response:
        loopback_urls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "model": "llama3.2:3b",
                "choices": [{"message": {"content": _valid_pattern_json()}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 88},
            },
        )

    local_client = LocalAIClient(
        base_url="http://127.0.0.1:11434/v1",
        llm_model="llama3.2:3b",
        embedding_model="bge-small-en-v1.5",
        transport=httpx.MockTransport(_local_llm_handler),
    )

    result = await process_adr_generalize_job(
        {"tenant_id": str(tenant_id), "payload": {"adr_id": str(adr_id)}},
        ai_client=local_client,
        scrubber=_NoOpScrubber(),  # type: ignore[arg-type]
    )
    await local_client.aclose()

    assert wan_attempts == []
    assert loopback_urls == ["http://127.0.0.1:11434/v1/chat/completions"]
    assert result["status"] == "pending_review"
    assert result["ai_mode"] == AI_MODE_LOCAL_ONLY
    assert adr.status == "pending_review"
    assert adr.sanitization_verified is False

    logs = [obj for obj in fake_db.added if isinstance(obj, ADRSanitizationLog)]
    assert len(logs) == 1
    diff_payload = logs[0].solution_architect_diff_payload
    assert diff_payload["ai_mode"] == AI_MODE_LOCAL_ONLY
    validated = GeneralizedADRPattern.model_validate(diff_payload["generalized_pattern"])
    assert validated.architecture_pattern == "Transactional Outbox + Redis Streams Relay"


@pytest.mark.asyncio
async def test_cloud_zdr_refusal_guard_blocks_cloud_client_and_requested_mode_when_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http_calls: list[str] = []

    def _forbid_any_http(request: httpx.Request) -> httpx.Response:
        http_calls.append(str(request.url))
        raise AssertionError(f"HTTP call should never execute: {request.url}")

    tenant_id, adr_id, _tenant, adr, fake_db = _setup_generalizer_env(
        monkeypatch, ai_processing_mode=AI_MODE_LOCAL_ONLY
    )

    cloud_client = CloudZDRAIClient(
        api_key="sk-test-zdr",
        base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
        transport=httpx.MockTransport(_forbid_any_http),
    )

    # 1. Passing CloudZDRAIClient for a local_only tenant is refused fail-closed
    with pytest.raises(CloudPolicyViolationError):
        await process_adr_generalize_job(
            {"tenant_id": str(tenant_id), "payload": {"adr_id": str(adr_id)}},
            ai_client=cloud_client,
            scrubber=_NoOpScrubber(),  # type: ignore[arg-type]
        )
    await cloud_client.aclose()

    # 2. Explicitly requesting cloud_zdr mode for a local_only tenant is refused fail-closed
    local_client = LocalAIClient(
        base_url="http://127.0.0.1:11434/v1",
        transport=httpx.MockTransport(_forbid_any_http),
    )
    with pytest.raises(CloudPolicyViolationError):
        await process_adr_generalize_job(
            {
                "tenant_id": str(tenant_id),
                "payload": {"adr_id": str(adr_id), "requested_mode": AI_MODE_CLOUD_ZDR},
            },
            ai_client=local_client,
            scrubber=_NoOpScrubber(),  # type: ignore[arg-type]
        )
    await local_client.aclose()

    assert http_calls == []
    assert adr.status == "draft"
    assert fake_db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_llm_content",
    [
        "not-valid-json",
        json.dumps({"title": "Only title"}),
        json.dumps(
            {
                **json.loads(_valid_pattern_json()),
                "unexpected_extra_key": "forbidden",
            }
        ),
    ],
)
async def test_strict_json_schema_adherence_rejects_malformed_or_extra_fields(
    monkeypatch: pytest.MonkeyPatch,
    bad_llm_content: str,
) -> None:
    tenant_id, adr_id, _tenant, adr, fake_db = _setup_generalizer_env(
        monkeypatch, ai_processing_mode=AI_MODE_LOCAL_ONLY
    )

    local_client = LocalAIClient(
        base_url="http://127.0.0.1:11434/v1",
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                200,
                json={"choices": [{"message": {"content": bad_llm_content}}]},
            )
        ),
    )

    with pytest.raises(ADRGeneralizationSchemaError):
        await process_adr_generalize_job(
            {"tenant_id": str(tenant_id), "payload": {"adr_id": str(adr_id)}},
            ai_client=local_client,
            scrubber=_NoOpScrubber(),  # type: ignore[arg-type]
        )
    await local_client.aclose()

    assert adr.status == "draft"
    assert fake_db.added == []


@pytest.mark.asyncio
async def test_cloud_zdr_mode_with_mocked_cloud_provider_persists_valid_schema_and_pending_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_bodies: list[dict[str, Any]] = []

    def _cloud_handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": _valid_pattern_json()}}],
                "usage": {"prompt_tokens": 64, "completion_tokens": 96},
            },
        )

    tenant_id, adr_id, _tenant, adr, fake_db = _setup_generalizer_env(
        monkeypatch, ai_processing_mode=AI_MODE_CLOUD_ZDR
    )
    cloud_client = CloudZDRAIClient(
        api_key="sk-test-zdr",
        base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
        transport=httpx.MockTransport(_cloud_handler),
    )

    res = await process_adr_generalize_job(
        {"tenant_id": str(tenant_id), "payload": {"adr_id": str(adr_id)}},
        ai_client=cloud_client,
        scrubber=_NoOpScrubber(),  # type: ignore[arg-type]
    )
    await cloud_client.aclose()

    assert len(captured_bodies) == 1
    assert captured_bodies[0]["response_format"]["type"] == "json_schema"
    assert res["status"] == "pending_review"
    assert res["ai_mode"] == AI_MODE_CLOUD_ZDR
    assert adr.status == "pending_review"

    logs = [obj for obj in fake_db.added if isinstance(obj, ADRSanitizationLog)]
    assert len(logs) == 1
    assert logs[0].solution_architect_diff_payload["ai_mode"] == AI_MODE_CLOUD_ZDR
