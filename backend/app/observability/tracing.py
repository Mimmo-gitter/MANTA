"""OpenTelemetry distributed tracing setup, instrumentation, and W3C propagation (TASK-302-2 / US-49)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import logging
import os
import re
import threading
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ReadableSpan,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import settings

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

logger = logging.getLogger("app.observability.tracing")

_PROPAGATOR = TraceContextTextMapPropagator()
_TOKEN_QUERY_RE = re.compile(r"(?i)(^|[?&])(token|access_token|jwt)=([^&#]*)")

_TRACE_STATE_LOCK = threading.RLock()
_INITIALIZED = False
_REGISTERED_OTLP_ENDPOINTS: set[str] = set()
_INSTRUMENTED_ENGINE_IDS: set[int] = set()


def scrub_sensitive_query(raw_query: str | bytes | None) -> str:
    """Redact sensitive token query parameters before recording query strings in telemetry spans."""
    if raw_query is None:
        return ""
    if isinstance(raw_query, bytes):
        query_str = raw_query.decode("utf-8", errors="replace")
    else:
        query_str = str(raw_query)
    if not query_str:
        return ""
    return _TOKEN_QUERY_RE.sub(r"\1\2=[REDACTED]", query_str)


class _TestProcessorMultiplexer(SpanProcessor):
    """Dynamic span processor multiplexer allowing tests to attach InMemorySpanExporters safely."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._exporters: list[SpanExporter] = []

    def add_exporter(self, exporter: SpanExporter) -> None:
        with self._lock:
            if exporter not in self._exporters:
                self._exporters.append(exporter)

    def remove_exporter(self, exporter: SpanExporter) -> None:
        with self._lock:
            self._exporters = [exp for exp in self._exporters if exp is not exporter]

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        with self._lock:
            exporters = list(self._exporters)
        for exporter in exporters:
            try:
                result = exporter.export((span,))
                if result == SpanExportResult.FAILURE:
                    logger.debug("Test span exporter reported failure for span %s", span.name)
            except Exception as exc:  # pragma: no cover - defensive test guard
                logger.debug("Test span exporter error: %s", exc)

    def shutdown(self) -> None:
        with self._lock:
            exporters = list(self._exporters)
            self._exporters.clear()
        for exporter in exporters:
            try:
                exporter.shutdown()
            except Exception:  # pragma: no cover
                pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_TEST_MULTIPLEXER = _TestProcessorMultiplexer()


def _create_otlp_exporter(endpoint: str) -> SpanExporter:
    """Construct the configured OTLP gRPC or HTTP exporter with non-blocking timeout."""
    protocol = (
        (settings.OTEL_EXPORTER_OTLP_PROTOCOL or os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") or "grpc")
        .strip()
        .lower()
    )
    if protocol in {"http", "http/protobuf"}:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as OTLPHttpSpanExporter,
        )

        return OTLPHttpSpanExporter(endpoint=endpoint, timeout=2)

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as OTLPGrpcSpanExporter,
    )

    return OTLPGrpcSpanExporter(
        endpoint=endpoint,
        insecure=settings.OTEL_EXPORTER_OTLP_INSECURE,
        timeout=2,
    )


def _get_or_attach_multiplexer(provider: TracerProvider) -> None:
    if not getattr(provider, "_manta_multiplexer_attached", False):
        provider.add_span_processor(_TEST_MULTIPLEXER)
        setattr(provider, "_manta_multiplexer_attached", True)


def setup_opentelemetry(
    service_name: str | None = None,
    *,
    otlp_endpoint: str | None = None,
    enabled: bool | None = None,
) -> TracerProvider | None:
    """
    Initialize the OpenTelemetry TracerProvider idempotently and attach an async
    BatchSpanProcessor when an OTLP endpoint is configured.
    """
    global _INITIALIZED

    is_enabled = settings.OTEL_ENABLED if enabled is None else enabled
    if not is_enabled:
        return None

    with _TRACE_STATE_LOCK:
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider):
            provider = current
        else:
            resolved_service = (
                service_name
                or os.getenv("OTEL_SERVICE_NAME")
                or settings.OTEL_SERVICE_NAME
                or "manta-backend"
            )
            resource = Resource.create(
                {
                    "service.name": resolved_service,
                    "deployment.environment": settings.APP_ENV,
                }
            )
            provider = TracerProvider(resource=resource)
            trace.set_tracer_provider(provider)

        _get_or_attach_multiplexer(provider)

        raw_endpoint = (
            otlp_endpoint
            if otlp_endpoint is not None
            else (
                os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
                or settings.OTEL_EXPORTER_OTLP_ENDPOINT
                or ""
            )
        )
        endpoint = raw_endpoint.strip()
        if endpoint and endpoint not in _REGISTERED_OTLP_ENDPOINTS:
            try:
                exporter = _create_otlp_exporter(endpoint)
                processor = BatchSpanProcessor(
                    exporter,
                    schedule_delay_millis=1000,
                    max_export_batch_size=512,
                    export_timeout_millis=2000,
                )
                provider.add_span_processor(processor)
                _REGISTERED_OTLP_ENDPOINTS.add(endpoint)
            except Exception as exc:  # pragma: no cover - defensive startup fallback
                logger.warning("Failed to configure OTLP span exporter (%s): %s", endpoint, exc)

        _INITIALIZED = True
        return provider


def shutdown_opentelemetry(*, final: bool = False) -> None:
    """
    Flush pending spans cleanly; only permanently shut down TracerProvider when `final=True`.

    In FastAPI lifespan teardown (which runs repeatedly across TestClient sessions in pytest),
    calling `provider.force_flush(...)` preserves span recording for subsequent tests.
    """
    current = trace.get_tracer_provider()
    if not isinstance(current, TracerProvider):
        return
    try:
        current.force_flush(timeout_millis=1000)
        if final:
            current.shutdown()
    except Exception as exc:  # pragma: no cover
        logger.debug("OpenTelemetry flush/shutdown warning: %s", exc)


def _server_request_hook(span: Span, scope: dict[str, Any]) -> None:
    """Scrub sensitive query parameters (e.g. `?token=<jwt>`) on HTTP/WS root spans."""
    if not span or not span.is_recording():
        return
    raw_query = scope.get("query_string")
    if not raw_query:
        return
    scrubbed = scrub_sensitive_query(raw_query)
    if scrubbed == (
        raw_query.decode("utf-8", errors="replace")
        if isinstance(raw_query, bytes)
        else str(raw_query)
    ):
        return

    path = str(scope.get("path") or "")
    span.set_attribute("url.query", scrubbed)
    if path:
        span.set_attribute("http.target", f"{path}?{scrubbed}" if scrubbed else path)

    # Also rewrite already-recorded attributes if FastAPIInstrumentor populated them pre-hook
    attrs = getattr(span, "attributes", None)
    if isinstance(attrs, Mapping):
        for key in ("http.target", "http.url", "url.full", "url.query"):
            if key in attrs and isinstance(attrs[key], str):
                span.set_attribute(key, _TOKEN_QUERY_RE.sub(r"\1\2=[REDACTED]", attrs[key]))


def instrument_fastapi_app(app: FastAPI) -> None:
    """Instrument FastAPI HTTP and WebSocket intake idempotently."""
    if getattr(app.state, "_otel_instrumented", False):
        return
    provider = setup_opentelemetry()
    if provider is None:
        return
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls="/health,/metrics",
        server_request_hook=_server_request_hook,
    )
    app.state._otel_instrumented = True


def instrument_sqlalchemy_engine(async_engine: AsyncEngine | Engine) -> None:
    """Instrument SQLAlchemy AsyncEngine or sync Engine queries without mutating SQL statements."""
    provider = setup_opentelemetry()
    if provider is None:
        return
    sync_engine = getattr(async_engine, "sync_engine", async_engine)
    engine_id = id(sync_engine)
    with _TRACE_STATE_LOCK:
        if engine_id in _INSTRUMENTED_ENGINE_IDS:
            return
        instrumentor = SQLAlchemyInstrumentor()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument(
                engine=sync_engine,
                tracer_provider=provider,
                enable_commenter=False,
                skip_dep_check=True,
            )
        else:
            from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
            from opentelemetry.metrics import get_meter
            from opentelemetry.semconv._incubating.metrics import db_metrics

            tracer = get_tracer(__name__)
            meter = get_meter(__name__)
            connections_usage = meter.create_up_down_counter(
                name=db_metrics.DB_CLIENT_CONNECTION_COUNT,
                unit="connections",
                description="Active database connections.",
            )
            EngineTracer(
                tracer,
                sync_engine,
                connections_usage,
                False,
                {},
                False,
            )
        _INSTRUMENTED_ENGINE_IDS.add(engine_id)


def get_tracer(name: str = "manta") -> Tracer:
    """Return an OpenTelemetry tracer bound to the active TracerProvider."""
    return trace.get_tracer(name)


def get_current_trace_id_hex() -> str | None:
    """Return the active 32-char hex W3C trace_id, or None when untraced."""
    span = trace.get_current_span()
    if span is None:
        return None
    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid or ctx.trace_id == 0:
        return None
    return f"{ctx.trace_id:032x}"


def extract_trace_id_from_traceparent(traceparent: str | None) -> str | None:
    """Extract the 32-char hex trace_id from a W3C traceparent header string."""
    if not traceparent or not isinstance(traceparent, str):
        return None
    ctx = extract_trace_context(traceparent)
    if ctx is None:
        return None
    span = trace.get_current_span(ctx)
    span_ctx = span.get_span_context() if span is not None else None
    if span_ctx is None or not span_ctx.is_valid or span_ctx.trace_id == 0:
        return None
    return f"{span_ctx.trace_id:032x}"


def inject_trace_carrier() -> dict[str, str]:
    """
    Inject the active W3C trace context (`traceparent` / optional `tracestate`) into a dict.

    Returns `{}` when no valid recording/active span is present so untraced paths omit headers.
    """
    if get_current_trace_id_hex() is None:
        return {}
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    if not carrier.get("traceparent"):
        return {}
    if not carrier.get("tracestate"):
        carrier.pop("tracestate", None)
    return carrier


def extract_trace_context(
    traceparent: str | None,
    tracestate: str | None = None,
) -> Context | None:
    """Extract a W3C OpenTelemetry Context from stored `traceparent` / `tracestate` strings."""
    if not traceparent or not isinstance(traceparent, str):
        return None
    carrier: dict[str, str] = {"traceparent": traceparent.strip()}
    if tracestate and isinstance(tracestate, str) and tracestate.strip():
        carrier["tracestate"] = tracestate.strip()
    ctx = _PROPAGATOR.extract(carrier=carrier, context=Context())
    span_ctx = trace.get_current_span(ctx).get_span_context()
    if span_ctx is None or not span_ctx.is_valid:
        return None
    return ctx


@contextmanager
def start_linked_span(
    name: str,
    *,
    traceparent: str | None = None,
    tracestate: str | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    tracer_name: str = "manta",
) -> Iterator[Span]:
    """
    Start a child span attached to either the extracted W3C `traceparent` context or
    the current active context, preserving a single end-to-end `trace_id`.
    """
    parent_ctx = extract_trace_context(traceparent, tracestate)
    if parent_ctx is None:
        parent_ctx = otel_context.get_current()
    tracer = get_tracer(tracer_name)
    attrs = dict(attributes) if attributes else None
    with tracer.start_as_current_span(
        name,
        context=parent_ctx,
        kind=kind,
        attributes=attrs,
    ) as span:
        yield span


def register_span_exporter(exporter: SpanExporter) -> None:
    """Attach an arbitrary SpanExporter to the multiplexer (ensuring OTel is initialized)."""
    setup_opentelemetry()
    _TEST_MULTIPLEXER.add_exporter(exporter)


def unregister_span_exporter(exporter: SpanExporter) -> None:
    """Detach a previously registered SpanExporter from the multiplexer."""
    _TEST_MULTIPLEXER.remove_exporter(exporter)


@contextmanager
def temporary_in_memory_span_exporter() -> Iterator[InMemorySpanExporter]:
    """Attach a scoped InMemorySpanExporter for deterministic distributed trace verification."""
    setup_opentelemetry()
    exporter = InMemorySpanExporter()
    _TEST_MULTIPLEXER.add_exporter(exporter)
    try:
        yield exporter
    finally:
        _TEST_MULTIPLEXER.remove_exporter(exporter)
        exporter.shutdown()
