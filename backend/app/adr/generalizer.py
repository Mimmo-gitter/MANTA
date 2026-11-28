"""ADR generalization ARQ worker with Local/Cloud ZDR policy guard & strict JSON schema output (TASK-316-1 / US-42)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.adr.scrubber import (
    ADRScrubber,
    _quarantine_adr_fail_closed,
    scrub_adr_source,
)
from app.ai.client_factory import (
    BaseAIClient,
    create_ai_client,
    enforce_ai_client_policy,
)
from app.core.uuidv7 import generate_uuidv7
from app.db.models.models import ADRSanitizationLog, AnonymizedADR, Tenant
from app.db.session import get_tenant_db_context
from app.observability.tracing import inject_trace_carrier
from app.tenants.schemas import normalize_tenant_deny_list
from app.webhooks.enqueue import get_redis
from workers.worker import QUEUE_DEFAULT, QUEUE_HIGH, enqueue_job, serialize_job_payload

logger = logging.getLogger("app.adr.generalizer")

ADR_GENERALIZE_JOB_TYPE = "adr.generalize"


class ADRGeneralizationError(RuntimeError):
    """Base error raised during ADR LLM generalization."""


class ADRGeneralizationSchemaError(ADRGeneralizationError):
    """Raised when LLM output violates the strict GeneralizedADRPattern JSON schema."""


class GeneralizedADRPattern(BaseModel):
    """Strict structured JSON schema emitted by the ADR generalization LLM worker."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(..., min_length=3, max_length=300)
    architecture_pattern: str = Field(..., min_length=2, max_length=128)
    context_and_problem: str = Field(..., min_length=10, max_length=4000)
    decision_outcome: str = Field(..., min_length=10, max_length=4000)
    consequences: list[str] = Field(..., min_length=1, max_length=20)
    quality_attributes: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("consequences", "quality_attributes")
    @classmethod
    def validate_non_empty_items(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
        return cleaned

    @field_validator("consequences")
    @classmethod
    def require_at_least_one_consequence(cls, items: list[str]) -> list[str]:
        if not items:
            raise ValueError("consequences must contain at least one non-empty item")
        return items


def render_generalized_decision_summary(pattern: GeneralizedADRPattern) -> str:
    """Render validated GeneralizedADRPattern into clean Markdown for AnonymizedADR.decision_summary."""
    sections = [
        "## Context & Problem",
        pattern.context_and_problem,
        "",
        "## Decision Outcome",
        pattern.decision_outcome,
        "",
        "## Consequences",
        *[f"- {item}" for item in pattern.consequences],
    ]
    if pattern.quality_attributes:
        sections.extend(
            [
                "",
                "## Quality Attributes",
                *[f"- {attr}" for attr in pattern.quality_attributes],
            ]
        )
    return "\n".join(sections).strip()


def _build_generalization_messages(
    *,
    scrubbed_title: str,
    scrubbed_markdown: str,
) -> list[dict[str, str]]:
    schema_json = json.dumps(GeneralizedADRPattern.model_json_schema(), separators=(",", ":"))
    system_prompt = (
        "You are a Principal Software Architect generalizing a scrubbed Architectural Decision Record (ADR) "
        "into a reusable, tenant-agnostic architectural pattern. "
        "All sensitive fields have already been replaced with placeholders such as "
        "[REDACTED_EMAIL], [REDACTED_IP_ADDRESS], [REDACTED_API_TOKEN], and [REDACTED_COMPANY_NAME]. "
        "Never invent or reintroduce company names, emails, IP addresses, hostnames, or credentials. "
        "Return ONLY a single valid JSON object conforming strictly to this JSON Schema (no Markdown fences, no extra keys): "
        f"{schema_json}"
    )
    user_prompt = (
        f"Scrubbed ADR Title: {scrubbed_title}\n\nScrubbed ADR Markdown:\n{scrubbed_markdown}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "generalized_adr_pattern",
            "strict": True,
            "schema": GeneralizedADRPattern.model_json_schema(),
        },
    }


async def enqueue_adr_generalize_job(
    *,
    tenant_id: uuid.UUID,
    adr_id: uuid.UUID,
    priority: str = "default",
    requested_mode: Optional[str] = None,
    force: bool = False,
    redis_client: Optional[Any] = None,
) -> dict[str, Any]:
    """Enqueue an `adr.generalize` job onto `arq:queue:default` or `arq:queue:high`."""
    norm_priority = priority.strip().lower() if isinstance(priority, str) else "default"
    if norm_priority not in ("default", "high"):
        raise ValueError(f"Unsupported queue priority: {priority!r}")

    job_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "adr_id": str(adr_id),
        "queue": QUEUE_HIGH if norm_priority == "high" else QUEUE_DEFAULT,
    }
    if requested_mode is not None:
        payload["requested_mode"] = requested_mode
    if force:
        payload["force"] = True

    carrier = inject_trace_carrier()
    envelope = serialize_job_payload(
        job_id=job_id,
        tenant_id=str(tenant_id),
        priority=norm_priority,
        job_type=ADR_GENERALIZE_JOB_TYPE,
        payload=payload,
        provider="internal",
        traceparent=carrier.get("traceparent"),
        tracestate=carrier.get("tracestate"),
    )
    client = redis_client or get_redis()
    await enqueue_job(client, envelope, priority=norm_priority)
    return {
        "job_id": job_id,
        "tenant_id": str(tenant_id),
        "adr_id": str(adr_id),
        "priority": norm_priority,
        "queue": payload["queue"],
    }


async def process_adr_generalize_job(
    parsed_payload: dict[str, Any],
    *,
    ai_client: Optional[BaseAIClient] = None,
    scrubber: Optional[ADRScrubber] = None,
) -> dict[str, Any]:
    """
    ARQ worker handler for `job_type == 'adr.generalize'`.

    1. Scrubs raw `adr_sources` via Presidio + entropy + tenant deny-list (quarantines fail-closed on error).
    2. Enforces `local_only` / `cloud_zdr` policy guard before constructing or invoking any AI client.
    3. Executes structured JSON completion against only scrubbed text and validates strict Pydantic schema.
    4. Runs post-generation safety guard, updates `AnonymizedADR` (`draft` -> `pending_review`), and logs telemetry.
    """
    raw_tenant_id = parsed_payload.get("tenant_id")
    if not raw_tenant_id:
        raise ValueError("adr.generalize payload missing tenant_id")
    tenant_id = uuid.UUID(str(raw_tenant_id))

    inner = parsed_payload.get("payload")
    inner_dict = inner if isinstance(inner, dict) else {}
    raw_adr_id = inner_dict.get("adr_id") or parsed_payload.get("adr_id")
    if not raw_adr_id:
        raise ValueError("adr.generalize payload missing adr_id")
    adr_id = uuid.UUID(str(raw_adr_id))

    requested_mode = (
        inner_dict.get("requested_mode")
        or inner_dict.get("target_mode")
        or parsed_payload.get("requested_mode")
    )
    force = bool(inner_dict.get("force", False))
    job_id = str(parsed_payload.get("job_id") or uuid.uuid4())
    active_scrubber = scrubber or ADRScrubber()

    async with get_tenant_db_context(tenant_id) as db:
        tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None:
            raise ValueError(f"Tenant {tenant_id} not found for adr.generalize job")

        adr = await db.scalar(
            select(AnonymizedADR).where(
                AnonymizedADR.tenant_id == tenant_id,
                AnonymizedADR.id == adr_id,
            )
        )
        if adr is None:
            raise ValueError(f"AnonymizedADR {adr_id} not found for tenant {tenant_id}")

        if adr.status != "draft" and not (force and adr.status == "pending_review"):
            return {
                "status": f"noop:{adr.status}",
                "tenant_id": str(tenant_id),
                "adr_id": str(adr_id),
                "job_id": job_id,
            }

        # Step 1: Scrub raw source BEFORE any policy/LLM invocation (quarantines fail-closed on scanner error)
        scrub_outcome = await scrub_adr_source(
            db,
            tenant_id=tenant_id,
            adr_id=adr_id,
            scrubber=active_scrubber,
            persist_log=False,
        )
        if scrub_outcome.quarantined or scrub_outcome.scrubbed_markdown is None:
            return {
                "status": "quarantined",
                "tenant_id": str(tenant_id),
                "adr_id": str(adr_id),
                "job_id": job_id,
                "error": scrub_outcome.error_message,
            }

        # Step 2: Enforce Local-Only / Cloud ZDR policy guard fail-closed BEFORE any HTTP request
        effective_mode = enforce_ai_client_policy(
            tenant.ai_processing_mode,
            requested_mode=str(requested_mode) if requested_mode is not None else None,
            ai_client=ai_client,
        )

        # Step 3: Invoke LLM strictly with scrubbed text and strict JSON schema response format
        messages = _build_generalization_messages(
            scrubbed_title=scrub_outcome.scrubbed_title or adr.title,
            scrubbed_markdown=scrub_outcome.scrubbed_markdown,
        )
        response_format = _build_response_format()

        if ai_client is not None:
            completion = await ai_client.chat_complete_with_usage(
                messages,
                response_format=response_format,
                temperature=0.0,
            )
        else:
            async with create_ai_client(effective_mode) as client:
                completion = await client.chat_complete_with_usage(
                    messages,
                    response_format=response_format,
                    temperature=0.0,
                )

        # Step 4: Strictly validate JSON output against GeneralizedADRPattern (extra="forbid")
        try:
            pattern = GeneralizedADRPattern.model_validate_json(completion.content.strip())
        except (ValidationError, ValueError) as exc:
            raise ADRGeneralizationSchemaError(
                f"LLM output failed strict GeneralizedADRPattern schema validation: {exc}"
            ) from exc

        # Step 5: Post-generation safety check ensuring zero residual PII/secrets/deny-list terms leaked
        rendered_summary = render_generalized_decision_summary(pattern)
        combined_output_text = "\n".join(
            [pattern.title, pattern.architecture_pattern, rendered_summary]
        )
        deny_cfg = normalize_tenant_deny_list(tenant.adr_deny_list)
        post_check = active_scrubber.scrub_text(
            combined_output_text,
            tenant_deny_list=deny_cfg,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            allowed_domain=tenant.allowed_domain,
        )
        if post_check.total_redactions > 0:
            logger.warning(
                "Quarantining ADR %s for tenant %s: post-LLM output leaked %d sensitive entity/entities",
                adr_id,
                tenant_id,
                post_check.total_redactions,
            )
            await _quarantine_adr_fail_closed(
                db,
                tenant_id=tenant_id,
                adr=adr,
                stage="post_llm_guard",
                error_message="Generalized LLM output contained unredacted sensitive entities; quarantined fail-closed.",
                error_type="ResidualSensitiveEntityError",
            )
            return {
                "status": "quarantined",
                "tenant_id": str(tenant_id),
                "adr_id": str(adr_id),
                "job_id": job_id,
                "error": "Residual sensitive entities detected in LLM output.",
            }

        # Step 6: Update AnonymizedADR and persist ADRSanitizationLog
        now = datetime.now(timezone.utc)
        adr.title = pattern.title
        adr.architecture_pattern = pattern.architecture_pattern
        adr.decision_summary = rendered_summary
        adr.status = "pending_review"
        adr.sanitization_verified = False
        adr.updated_at = now

        db.add(
            ADRSanitizationLog(
                id=generate_uuidv7(),
                tenant_id=tenant_id,
                adr_id=adr.id,
                raw_input_snippet=scrub_outcome.scrubbed_markdown[:500],
                presidio_entities_scrubbed=scrub_outcome.combined_entities_scrubbed,
                llm_abstraction_prompt_tokens=completion.prompt_tokens,
                solution_architect_diff_payload={
                    "status": "pending_review",
                    "ai_mode": effective_mode,
                    "model": completion.model,
                    "scrubbed_title": scrub_outcome.scrubbed_title,
                    "scrubbed_markdown": scrub_outcome.scrubbed_markdown,
                    "redactions": scrub_outcome.redactions,
                    "generalized_pattern": pattern.model_dump(),
                },
            )
        )
        await db.flush()

        return {
            "status": "pending_review",
            "tenant_id": str(tenant_id),
            "adr_id": str(adr.id),
            "job_id": job_id,
            "ai_mode": effective_mode,
            "architecture_pattern": pattern.architecture_pattern,
            "redaction_count": scrub_outcome.total_redactions,
            "redaction_categories": scrub_outcome.counts,
            "prompt_tokens": completion.prompt_tokens,
        }
