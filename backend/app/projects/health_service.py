"""
SCRUM-452 (Task 68.3) & SCRUM-453 (Task 68.4):
Production Health Signal Authentication, Webhook Routing, and Failure Session State Machine.

Features:
- Dual-endpoint HMAC-SHA256 / secret token / RS256 OIDC JWT authentication for:
  * POST /api/v1/projects/{project_id}/health-signals
  * POST /api/v1/webhooks/projects/{project_id}/health
- Constant-time signature & token comparison via `hmac.compare_digest` over raw bytes.
- SECURITY DEFINER tenant lookup via `get_project_health_webhook_route(UUID)` prior to
  setting `app.current_tenant_id` for PostgreSQL FORCE ROW LEVEL SECURITY.
- Idempotent production failure session state machine:
  * FAIL when healthy -> opens new OPEN session in `production_failures` (and syncs `incidents`).
  * Duplicate FAIL when OPEN -> no-op preserving original `started_at`.
  * PASS when OPEN -> resolves session with exact `recovery_time_seconds` (and syncs `incidents`).
  * Orphan PASS when healthy -> clean no-op creating 0 rows.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Mapping, Optional
import uuid

import asyncpg
from fastapi import HTTPException, Request, status as http_status
import jwt
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import AuthError, JWKSUnavailable
from app.auth.permissions import (
    FORBIDDEN_DETAIL,
    Permission,
    get_permission_matrix,
    resolve_caller_roles,
)
from app.auth.schemas import TokenClaims
from app.auth.tokens import decode_token, extract_bearer_token, read_kid
from app.config import settings
from app.db.models.models import (
    Deployment,
    Incident,
    Integration,
    ProductionFailure,
    Project,
)
from app.db.session import AsyncSessionLocal, get_tenant_db_context
from app.integrations.crypto import encryption_service, mask_secret
from app.integrations.service import generate_webhook_secret
from app.outbox.service import record_outbox_event
from app.projects.schemas import (
    FailureSessionStatus,
    HealthProbeConfigResponse,
    HealthProbeConfigUpdateRequest,
    HealthSignalIngestRequest,
    HealthSignalIngestResponse,
    HealthSignalStatus,
    ProductionFailureResponse,
    ProjectHealthStatus,
)

logger = logging.getLogger("app.projects.health_service")

_ROUTE_LOOKUP_SQL = text(
    "SELECT id, tenant_id, health_probe_enabled, health_probe_source, "
    "health_webhook_secret_hash, health_status, is_active, deleted_at "
    "FROM get_project_health_webhook_route(:project_id)"
)

_DB_OUTAGE_EXCEPTIONS = (
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    ConnectionError,
    OSError,
    TimeoutError,
)


def _is_db_outage_error(exc: BaseException) -> bool:
    """Return True if an exception indicates PostgreSQL/pooler connectivity unavailability."""
    return isinstance(exc, _DB_OUTAGE_EXCEPTIONS) or isinstance(
        getattr(exc, "orig", None), _DB_OUTAGE_EXCEPTIONS
    )


_HMAC_HEADER_NAMES: tuple[str, ...] = (
    "X-Hub-Signature-256",
    "X-Webhook-Signature",
    "X-Health-Signature",
    "X-Health-Signature-256",
    "X-Signature",
    "X-Hub-Signature",
)

_TOKEN_HEADER_NAMES: tuple[str, ...] = (
    "X-Webhook-Secret",
    "X-Health-Token",
    "X-Health-Secret",
    "X-Api-Key",
)

_DECRYPTED_SECRET_CACHE: dict[tuple[str, str], str] = {}


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    """Case-insensitive stripped header lookup."""
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            val = str(v).strip()
            if val:
                return val
    return None


def _get_first_header(headers: Mapping[str, str], names: tuple[str, ...]) -> Optional[str]:
    """Return the first non-empty header value matching any candidate header name."""
    for name in names:
        val = _get_header(headers, name)
        if val:
            return val
    return None


def _looks_like_jwt(token: str) -> bool:
    """Return True if a bearer token has a 3-segment JSON Web Token structure."""
    parts = token.split(".")
    return len(parts) == 3 and all(bool(p) for p in parts) and parts[0].startswith("ey")


def resolve_project_webhook_secret(secret_hash: Optional[str]) -> Optional[str]:
    """Decrypt an AES-256-GCM encrypted project webhook secret, or return plaintext if raw."""
    if not secret_hash:
        return None
    if not encryption_service.is_encrypted(secret_hash):
        return secret_hash
    cache_key = (encryption_service.active_key_id, secret_hash)
    cached = _DECRYPTED_SECRET_CACHE.get(cache_key)
    if cached is not None:
        return cached
    raw_secret = encryption_service.decrypt(secret_hash)
    if len(_DECRYPTED_SECRET_CACHE) < 1024:
        _DECRYPTED_SECRET_CACHE[cache_key] = raw_secret
    return raw_secret


def verify_health_webhook_hmac(secret: str, raw_body: bytes, signature_header: str) -> bool:
    """Verify HMAC-SHA256 signature over raw request bytes in constant time."""
    if not secret or not signature_header:
        return False
    computed_mac = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    expected_prefixed = f"sha256={computed_mac}"
    sig_clean = signature_header.strip()
    if sig_clean.lower().startswith("sha256="):
        return hmac.compare_digest(sig_clean.lower(), expected_prefixed.lower())
    return hmac.compare_digest(sig_clean.lower(), computed_mac.lower())


def build_health_webhook_urls(base_url: str, project_id: uuid.UUID) -> tuple[str, str]:
    """Return (webhook_url, project_health_signals_url) for a project."""
    clean_base = (settings.WEBHOOK_BASE_URL or str(base_url)).rstrip("/")
    webhook_url = f"{clean_base}/api/v1/webhooks/projects/{project_id}/health"
    signals_url = f"{clean_base}/api/v1/projects/{project_id}/health-signals"
    return webhook_url, signals_url


async def _fallback_lookup_project_route(
    project_id: uuid.UUID,
    tenant_hint: Optional[uuid.UUID],
) -> Optional[dict[str, Any]]:
    """Fallback lookup if SECURITY DEFINER function get_project_health_webhook_route is unmigrated."""
    query = select(
        Project.id,
        Project.tenant_id,
        Project.health_probe_enabled,
        Project.health_probe_source,
        Project.health_webhook_secret_hash,
        Project.health_status,
        Project.is_active,
        Project.deleted_at,
    ).where(Project.id == project_id)

    if tenant_hint is not None:
        async with get_tenant_db_context(tenant_hint) as session:
            row = (await session.execute(query)).first()
    else:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(query)).first()

    if row is None:
        return None
    return {
        "id": row[0],
        "tenant_id": row[1],
        "health_probe_enabled": row[2],
        "health_probe_source": row[3],
        "health_webhook_secret_hash": row[4],
        "health_status": row[5],
        "is_active": row[6],
        "deleted_at": row[7],
    }


async def _lookup_project_health_route(
    project_id: uuid.UUID,
    tenant_hint: Optional[uuid.UUID] = None,
) -> Optional[dict[str, Any]]:
    """Resolve project routing and webhook secret metadata prior to setting tenant RLS."""
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                res = await session.execute(_ROUTE_LOOKUP_SQL, {"project_id": project_id})
                row = res.mappings().first()
                return dict(row) if row is not None else None
    except ProgrammingError as exc:
        if "get_project_health_webhook_route" not in str(exc):
            raise
        return await _fallback_lookup_project_route(project_id, tenant_hint)


async def _lookup_project_bound_integration_secret(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Optional[str]:
    """Secondary fallback: inspect active Integration bound explicitly to project_id."""
    async with get_tenant_db_context(tenant_id) as session:
        stmt = (
            select(Integration.webhook_secret_hash)
            .where(
                Integration.tenant_id == tenant_id,
                Integration.project_id == project_id,
                Integration.is_active.is_(True),
            )
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
        secret_hash = (await session.execute(stmt)).scalar_one_or_none()
        if not secret_hash:
            return None
        return resolve_project_webhook_secret(secret_hash)


async def _authenticate_bearer_jwt_if_needed(
    request: Request,
    auth_header: Optional[str],
) -> tuple[Optional[TokenClaims], Optional[str], bool]:
    """
    Inspect `request.state.user` and `Authorization` header.

    Returns (jwt_user, bearer_secret_token, has_malformed_auth_header).
    """
    existing_user: Optional[TokenClaims] = getattr(request.state, "user", None)
    if existing_user is not None:
        return existing_user, None, False

    if not auth_header:
        return None, None, False

    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None, None, True

    raw_token = parts[1].strip()
    if not _looks_like_jwt(raw_token):
        return None, raw_token, False

    client = getattr(request.app.state, "jwks_client", None)
    if client is None:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service misconfigured",
        )

    try:
        token = extract_bearer_token(auth_header)
        kid = read_kid(token)
        key = await client.get_signing_key(kid)
        claims = decode_token(
            token,
            key,
            audience=settings.OIDC_AUDIENCE,
            issuer=settings.allowed_issuers,
        )
        return TokenClaims.from_claims(claims), None, False
    except JWKSUnavailable as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from exc
    except (AuthError, jwt.PyJWTError) as exc:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def process_health_signal(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    status: HealthSignalStatus | str,
    signal_timestamp: Optional[datetime] = None,
    probe_source: str = "webhook",
    severity: str = "SEV-1",
    deployment_id: Optional[uuid.UUID] = None,
) -> HealthSignalIngestResponse:
    """
    Execute the idempotent production failure session state machine (Task 68.4 / SCRUM-453):

    - On status == 'FAIL':
      * If no OPEN session exists for project_id -> create OPEN session in `production_failures`
        with started_at = signal_timestamp, sync matching open row in `incidents`, and set
        project.health_status = 'FAILING'.
      * If an OPEN session already exists -> keep original started_at unchanged (no-op).
    - On status == 'PASS':
      * If an OPEN session exists -> set status = 'RESOLVED', resolved_at = signal_timestamp,
        recovery_time_seconds = (resolved_at - started_at).total_seconds(), sync `incidents`,
        and set project.health_status = 'HEALTHY'.
      * If no OPEN session exists (orphan PASS) -> ignore as clean no-op creating 0 rows.
    """
    if isinstance(status, HealthSignalStatus):
        signal_status = status
    else:
        raw_clean = str(status).strip().upper()
        try:
            signal_status = HealthSignalStatus(raw_clean)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported health signal status '{status}'. Expected 'FAIL' or 'PASS'."
            ) from exc

    signal_ts = _ensure_utc(signal_timestamp or datetime.now(timezone.utc))

    proj_stmt = (
        select(Project)
        .where(
            Project.id == project_id,
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
        )
        .with_for_update()
    )
    project = (await db.execute(proj_stmt)).scalars().first()
    if project is None or not project.is_active:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    if deployment_id is not None:
        dep_exists = (
            await db.execute(
                select(Deployment.id).where(
                    Deployment.id == deployment_id,
                    Deployment.tenant_id == tenant_id,
                    Deployment.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if dep_exists is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Deployment '{deployment_id}' not found in project '{project_id}'.",
            )

    open_stmt = (
        select(ProductionFailure)
        .where(
            ProductionFailure.tenant_id == tenant_id,
            ProductionFailure.project_id == project_id,
            ProductionFailure.status == FailureSessionStatus.OPEN.value,
        )
        .with_for_update()
    )
    open_session = (await db.execute(open_stmt)).scalars().first()

    now = datetime.now(timezone.utc)

    if signal_status == HealthSignalStatus.FAIL:
        if open_session is not None:
            if project.health_status != ProjectHealthStatus.FAILING.value:
                project.health_status = ProjectHealthStatus.FAILING.value
                project.updated_at = now
                await db.flush()

            return HealthSignalIngestResponse(
                status="accepted",
                project_id=project_id,
                tenant_id=tenant_id,
                signal_status=HealthSignalStatus.FAIL,
                project_health_status=ProjectHealthStatus.FAILING,
                action="duplicate_fail_ignored",
                noop=True,
                duplicate=True,
                orphan=False,
                session_id=open_session.id,
                incident_id=open_session.incident_id,
                session_status=FailureSessionStatus.OPEN,
                probe_source=open_session.probe_source,
                started_at=_ensure_utc(open_session.started_at),
                resolved_at=None,
                recovery_time_seconds=None,
                message="Duplicate FAIL signal ignored; existing OPEN session preserved.",
            )

        session_id = uuid.uuid4()
        clean_source = (probe_source or project.health_probe_source or "webhook").strip()[:64]
        if not clean_source:
            clean_source = "webhook"
        clean_severity = (severity or "SEV-1").strip().upper()[:16] or "SEV-1"

        incident = Incident(
            id=session_id,
            tenant_id=tenant_id,
            project_id=project_id,
            deployment_id=deployment_id,
            started_at=signal_ts,
            resolved_at=None,
            recovery_time_seconds=None,
            severity=clean_severity,
            created_at=now,
        )
        db.add(incident)

        failure = ProductionFailure(
            id=session_id,
            tenant_id=tenant_id,
            project_id=project_id,
            incident_id=session_id,
            started_at=signal_ts,
            resolved_at=None,
            recovery_time_seconds=None,
            status=FailureSessionStatus.OPEN.value,
            probe_source=clean_source,
            severity=clean_severity,
            created_at=now,
            updated_at=now,
        )
        db.add(failure)

        project.health_status = ProjectHealthStatus.FAILING.value
        project.updated_at = now
        await db.flush()

        record_outbox_event(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            event_type="incident.opened",
            aggregate_type="production_failure",
            aggregate_id=session_id,
            payload={
                "session_id": str(session_id),
                "incident_id": str(session_id),
                "project_id": str(project_id),
                "deployment_id": str(deployment_id) if deployment_id else None,
                "probe_source": clean_source,
                "severity": clean_severity,
                "started_at": signal_ts.isoformat(),
            },
        )

        return HealthSignalIngestResponse(
            status="accepted",
            project_id=project_id,
            tenant_id=tenant_id,
            signal_status=HealthSignalStatus.FAIL,
            project_health_status=ProjectHealthStatus.FAILING,
            action="opened",
            noop=False,
            duplicate=False,
            orphan=False,
            session_id=session_id,
            incident_id=session_id,
            session_status=FailureSessionStatus.OPEN,
            probe_source=clean_source,
            started_at=signal_ts,
            resolved_at=None,
            recovery_time_seconds=None,
            message="Opened new production failure session.",
        )

    # signal_status == HealthSignalStatus.PASS
    if open_session is None:
        if project.health_status != ProjectHealthStatus.HEALTHY.value:
            project.health_status = ProjectHealthStatus.HEALTHY.value
            project.updated_at = now
            await db.flush()

        return HealthSignalIngestResponse(
            status="accepted",
            project_id=project_id,
            tenant_id=tenant_id,
            signal_status=HealthSignalStatus.PASS,
            project_health_status=ProjectHealthStatus.HEALTHY,
            action="orphan_pass_ignored",
            noop=True,
            duplicate=False,
            orphan=True,
            session_id=None,
            incident_id=None,
            session_status=None,
            probe_source=None,
            started_at=None,
            resolved_at=None,
            recovery_time_seconds=None,
            message="Orphan PASS signal ignored; no OPEN failure session exists.",
        )

    started_utc = _ensure_utc(open_session.started_at)
    if signal_ts < started_utc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PASS signal_timestamp cannot precede active failure session started_at.",
        )

    recovery_seconds = round((signal_ts - started_utc).total_seconds(), 2)
    open_session.status = FailureSessionStatus.RESOLVED.value
    open_session.resolved_at = signal_ts
    open_session.recovery_time_seconds = recovery_seconds
    open_session.updated_at = now

    target_incident: Optional[Incident] = None
    if open_session.incident_id is not None:
        target_incident = (
            (
                await db.execute(
                    select(Incident).where(
                        Incident.id == open_session.incident_id,
                        Incident.tenant_id == tenant_id,
                        Incident.project_id == project_id,
                    )
                )
            )
            .scalars()
            .first()
        )
    if target_incident is None:
        target_incident = (
            (
                await db.execute(
                    select(Incident)
                    .where(
                        Incident.tenant_id == tenant_id,
                        Incident.project_id == project_id,
                        Incident.resolved_at.is_(None),
                        Incident.started_at == started_utc,
                    )
                    .order_by(Incident.created_at.desc())
                )
            )
            .scalars()
            .first()
        )

    if target_incident is not None:
        target_incident.resolved_at = signal_ts
        target_incident.recovery_time_seconds = recovery_seconds
        if open_session.incident_id is None:
            open_session.incident_id = target_incident.id
    else:
        synced_incident_id = open_session.incident_id or open_session.id
        synced_incident = Incident(
            id=synced_incident_id,
            tenant_id=tenant_id,
            project_id=project_id,
            deployment_id=deployment_id,
            started_at=started_utc,
            resolved_at=signal_ts,
            recovery_time_seconds=recovery_seconds,
            severity=open_session.severity or "SEV-1",
            created_at=open_session.created_at or started_utc,
        )
        db.add(synced_incident)
        open_session.incident_id = synced_incident_id

    project.health_status = ProjectHealthStatus.HEALTHY.value
    project.updated_at = now
    await db.flush()

    record_outbox_event(
        db,
        tenant_id=tenant_id,
        project_id=project_id,
        event_type="deployment.recovered",
        aggregate_type="production_failure",
        aggregate_id=open_session.id or open_session.incident_id or project_id,
        payload={
            "session_id": str(open_session.id),
            "incident_id": str(open_session.incident_id) if open_session.incident_id else None,
            "project_id": str(project_id),
            "deployment_id": str(deployment_id) if deployment_id else None,
            "probe_source": open_session.probe_source,
            "started_at": started_utc.isoformat(),
            "resolved_at": signal_ts.isoformat(),
            "recovery_time_seconds": recovery_seconds,
        },
    )

    return HealthSignalIngestResponse(
        status="accepted",
        project_id=project_id,
        tenant_id=tenant_id,
        signal_status=HealthSignalStatus.PASS,
        project_health_status=ProjectHealthStatus.HEALTHY,
        action="resolved",
        noop=False,
        duplicate=False,
        orphan=False,
        session_id=open_session.id,
        incident_id=open_session.incident_id,
        session_status=FailureSessionStatus.RESOLVED,
        probe_source=open_session.probe_source,
        started_at=started_utc,
        resolved_at=signal_ts,
        recovery_time_seconds=recovery_seconds,
        message="Resolved open production failure session.",
    )


async def handle_project_health_signal_http(
    *,
    project_id: uuid.UUID,
    request: Request,
    x_tenant_id: Optional[str] = None,
) -> HealthSignalIngestResponse:
    """
    Shared HTTP controller for:
    - POST /api/v1/projects/{project_id}/health-signals
    - POST /api/v1/webhooks/projects/{project_id}/health
    """
    raw_body = await request.body()

    hmac_header = _get_first_header(request.headers, _HMAC_HEADER_NAMES)
    token_header = _get_first_header(request.headers, _TOKEN_HEADER_NAMES)
    auth_header = _get_header(request.headers, "Authorization")

    jwt_user, bearer_secret, malformed_auth = await _authenticate_bearer_jwt_if_needed(
        request, auth_header
    )

    if hmac_header is None and token_header is None and bearer_secret is None and jwt_user is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Missing health webhook signature or authentication token",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    if malformed_auth and hmac_header is None and token_header is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    header_tenant_uuid: Optional[uuid.UUID] = None
    if x_tenant_id:
        try:
            header_tenant_uuid = uuid.UUID(x_tenant_id.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-ID format (must be UUID)",
            ) from exc

    tenant_hint = jwt_user.tenant_uuid if jwt_user is not None else header_tenant_uuid

    try:
        route_row = await _lookup_project_health_route(project_id, tenant_hint=tenant_hint)
    except (SQLAlchemyError, DBAPIError, *_DB_OUTAGE_EXCEPTIONS) as exc:
        if _is_db_outage_error(exc):
            logger.error("Database unavailable during health webhook route lookup: %s", exc)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database storage service unavailable. Please retry delivery.",
                headers={"Retry-After": "30"},
            ) from exc
        raise

    # RBAC permission gate for JWT-authenticated callers before existence checks
    if jwt_user is not None:
        caller_roles = await resolve_caller_roles(jwt_user)
        allowed = get_permission_matrix().is_allowed(
            caller_roles,
            [
                Permission.INGEST_PROJECT_DEPLOYMENTS,
                Permission.MANAGE_WEBHOOK_INTEGRATIONS,
            ],
        )
        if not allowed:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail=FORBIDDEN_DETAIL,
            )

    if (
        route_row is None
        or route_row.get("deleted_at") is not None
        or not route_row.get("is_active")
    ):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    project_tenant_id: uuid.UUID = route_row["tenant_id"]

    # Anti-enumeration & strict cross-tenant isolation
    if jwt_user is not None and jwt_user.tenant_uuid != project_tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    if header_tenant_uuid is not None and header_tenant_uuid != project_tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant health signal rejected.",
        )

    # Resolve configured webhook secret for HMAC / token verification
    configured_secret: Optional[str] = None
    secret_hash = route_row.get("health_webhook_secret_hash")
    if secret_hash:
        try:
            configured_secret = resolve_project_webhook_secret(secret_hash)
        except Exception as exc:
            logger.error(
                "Failed to decrypt health webhook secret for project %s: %s", project_id, exc
            )
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to decrypt project health webhook credential",
            ) from exc
    elif hmac_header is not None or token_header is not None or bearer_secret is not None:
        configured_secret = await _lookup_project_bound_integration_secret(
            project_tenant_id, project_id
        )

    # Verify HMAC signature or shared secret token (always constant-time)
    if hmac_header is not None:
        if not configured_secret or not verify_health_webhook_hmac(
            configured_secret, raw_body, hmac_header
        ):
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid health webhook HMAC signature",
                headers={"WWW-Authenticate": "WebhookSignature"},
            )
    elif token_header is not None or bearer_secret is not None:
        candidate_token = token_header or bearer_secret or ""
        if not configured_secret or not hmac.compare_digest(candidate_token, configured_secret):
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Invalid health webhook authentication token",
                headers={"WWW-Authenticate": "WebhookSignature"},
            )
    elif jwt_user is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Missing health webhook signature or authentication token",
            headers={"WWW-Authenticate": "WebhookSignature"},
        )

    if not route_row.get("health_probe_enabled", True):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Health probe ingestion is disabled for this project.",
        )

    # Parse and validate JSON payload strictly AFTER cryptographic verification
    try:
        decoded_body = raw_body.decode("utf-8") if raw_body else ""
        raw_payload = json.loads(decoded_body) if decoded_body else None
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid JSON payload for health signal.",
        ) from exc

    if not isinstance(raw_payload, dict):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Health signal payload must be a JSON object.",
        )

    try:
        signal_req = HealthSignalIngestRequest.model_validate(raw_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid health signal payload: {exc}",
        ) from exc

    if signal_req.tenant_id is not None and signal_req.tenant_id != project_tenant_id:
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Cross-tenant health signal rejected.",
        )

    try:
        async with get_tenant_db_context(project_tenant_id) as session:
            return await process_health_signal(
                session,
                tenant_id=project_tenant_id,
                project_id=project_id,
                status=signal_req.status,
                signal_timestamp=signal_req.signal_timestamp,
                probe_source=signal_req.probe_source,
                severity=signal_req.severity,
                deployment_id=signal_req.deployment_id,
            )
    except HTTPException:
        raise
    except (SQLAlchemyError, DBAPIError, *_DB_OUTAGE_EXCEPTIONS) as exc:
        if _is_db_outage_error(exc):
            logger.error("Database unavailable during health signal processing: %s", exc)
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database storage service unavailable. Please retry delivery.",
                headers={"Retry-After": "30"},
            ) from exc
        raise


async def list_project_health_signals(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
) -> list[ProductionFailureResponse]:
    """List recorded production failure sessions for a project under tenant RLS."""
    async with get_tenant_db_context(tenant_id) as session:
        proj_res = await session.execute(
            select(Project.id).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
        )
        if proj_res.first() is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )

        rows = (
            (
                await session.execute(
                    select(ProductionFailure)
                    .where(
                        ProductionFailure.tenant_id == tenant_id,
                        ProductionFailure.project_id == project_id,
                    )
                    .order_by(ProductionFailure.started_at.desc())
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        return [ProductionFailureResponse.model_validate(r) for r in rows]


def _build_health_config_response(
    project: Project,
    base_url: str,
    *,
    plaintext_secret: Optional[str] = None,
) -> HealthProbeConfigResponse:
    webhook_url, signals_url = build_health_webhook_urls(base_url, project.id)
    masked = "********"
    if plaintext_secret:
        masked = mask_secret(plaintext_secret)
    elif project.health_webhook_secret_hash:
        try:
            decrypted = resolve_project_webhook_secret(project.health_webhook_secret_hash)
            masked = mask_secret(decrypted)
        except Exception:
            masked = "********"

    return HealthProbeConfigResponse(
        project_id=project.id,
        tenant_id=project.tenant_id,
        health_probe_enabled=project.health_probe_enabled,
        health_probe_source=project.health_probe_source,
        health_probe_url=project.health_probe_url,
        health_probe_interval_seconds=project.health_probe_interval_seconds,
        health_status=project.health_status,
        webhook_url=webhook_url,
        project_health_signals_url=signals_url,
        webhook_secret_masked=masked,
        webhook_secret=plaintext_secret,
        updated_at=project.updated_at,
    )


async def get_project_health_config(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    base_url: str,
) -> HealthProbeConfigResponse:
    """Retrieve health probe configuration and live health status for a project."""
    async with get_tenant_db_context(tenant_id) as session:
        project = (
            (
                await session.execute(
                    select(Project).where(
                        Project.id == project_id,
                        Project.tenant_id == tenant_id,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if project is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )
        return _build_health_config_response(project, base_url)


async def update_project_health_config(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    data: HealthProbeConfigUpdateRequest,
    base_url: str,
) -> HealthProbeConfigResponse:
    """Update health probe webhook configuration on a project under tenant RLS."""
    async with get_tenant_db_context(tenant_id) as session:
        project = (
            (
                await session.execute(
                    select(Project).where(
                        Project.id == project_id,
                        Project.tenant_id == tenant_id,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if project is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )

        fields = data.model_fields_set
        exposed_secret: Optional[str] = None
        if "health_probe_enabled" in fields and data.health_probe_enabled is not None:
            project.health_probe_enabled = data.health_probe_enabled
        if "health_probe_source" in fields and data.health_probe_source is not None:
            project.health_probe_source = data.health_probe_source.strip()[:64] or "webhook"
        if "health_probe_url" in fields:
            project.health_probe_url = (
                data.health_probe_url.strip() if data.health_probe_url else None
            )
        if (
            "health_probe_interval_seconds" in fields
            and data.health_probe_interval_seconds is not None
        ):
            project.health_probe_interval_seconds = data.health_probe_interval_seconds
        if "health_webhook_secret" in fields and data.health_webhook_secret is not None:
            exposed_secret = data.health_webhook_secret.strip()
            project.health_webhook_secret_hash = encryption_service.encrypt(exposed_secret)

        project.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return _build_health_config_response(project, base_url, plaintext_secret=exposed_secret)


async def rotate_project_health_secret(
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    base_url: str,
) -> HealthProbeConfigResponse:
    """Generate and persist a fresh AES-256-GCM encrypted HMAC secret for project health webhooks."""
    async with get_tenant_db_context(tenant_id) as session:
        project = (
            (
                await session.execute(
                    select(Project).where(
                        Project.id == project_id,
                        Project.tenant_id == tenant_id,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .first()
        )
        if project is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Project '{project_id}' not found.",
            )

        new_secret = generate_webhook_secret()
        project.health_webhook_secret_hash = encryption_service.encrypt(new_secret)
        project.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return _build_health_config_response(project, base_url, plaintext_secret=new_secret)
